#!/usr/bin/env python3
"""Hardware handoff for locally registered Flux batch manifests.

GitHub cannot supply commands or grant permission by itself. A trusted,
fresh, session-specific operator ACK is required before any bench commands.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import k6_experiment_batch as batch
import k6_experiment_dispatch as queue

ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/"config/k6-experiment-hardware.json"
ACK_URL=re.compile(r"^https://github\.com/([^/]+/[^/]+)/issues/([1-9]\d*)#issuecomment-([1-9]\d*)$")

def gh_api(route, paginate=False):
    args=["gh","api"]
    if paginate: args.append("--paginate")
    args.append(route)
    raw=subprocess.run(args,check=True,capture_output=True,text=True,timeout=60).stdout
    if not paginate:
        return json.loads(raw)
    # gh 2.28's --paginate emits adjacent JSON arrays, but --slurp was
    # introduced later. Decode ALL complete pages, never just the first.
    decoder=json.JSONDecoder()
    pages=[]
    pos=0
    while pos<len(raw):
        while pos<len(raw) and raw[pos].isspace():
            pos+=1
        if pos==len(raw):
            break
        page,end=decoder.raw_decode(raw,pos)
        if not isinstance(page,list):
            raise batch.Refused("paginated bench coordination response is not an array")
        pages.append(page)
        pos=end
    if not pages:
        raise batch.Refused("no GitHub coordination pages returned")
    return pages

def load_config(path):
    d=json.loads(Path(path).read_text())
    if not isinstance(d,dict) or set(d)!={"schema","repo","queue_issue","trusted_author","manifests"} or d["schema"]!=1:
        raise batch.Refused("invalid local hardware registry")
    if not isinstance(d["manifests"],dict) or not isinstance(d["queue_issue"],int):
        raise batch.Refused("invalid local hardware registration")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",d["repo"]) or not re.fullmatch(r"[A-Za-z0-9-]+",d["trusted_author"]):
        raise batch.Refused("invalid local trusted identity")
    for key,e in d["manifests"].items():
        if not re.fullmatch(r"[1-9]\d*",key) or not isinstance(e,dict) or set(e)!={"path","sha256"} or not isinstance(e["path"],str) or not Path(e["path"]).is_absolute() or not isinstance(e["sha256"],str) or not batch.SHA_RE.fullmatch(e["sha256"]):
            raise batch.Refused(f"#{key}: invalid approved local manifest registration")
    return d

def load_registered_manifest(config,issue):
    e=config["manifests"].get(str(issue))
    if not e: raise batch.Refused(f"#{issue}: no locally reviewed hardware manifest")
    p=Path(e["path"])
    m,manifest_hash,raw=batch.read_manifest(p)
    if manifest_hash!=e["sha256"]:
        raise batch.Refused("local hardware manifest changed since review")
    base=f"https://github.com/{config['repo']}/issues/"
    if m["queue_issue_url"]!=base+str(config["queue_issue"]):
        raise batch.Refused("hardware manifest queue issue mismatch")
    if m["port"]!="/dev/ttyUSB0":
        raise batch.Refused("unexpected canonical K6 radio serial port")
    if any(j["issue_url"]!=base+str(issue) or j.get("minimum_module_api") is None for j in m["jobs"]):
        raise batch.Refused("job issue mismatch or missing minimum API qualification")
    # Fail early on missing/drifted reviewed host scripts and source pins.
    batch._check_pins({"id":"session","pins":m["session_pins"]})
    for j in m["jobs"]: batch._check_pins(j)
    return m,manifest_hash,raw

def digest_for(raw,api_bytes):
    return hashlib.sha256(raw+b"\nAPI-CONFIG\n"+api_bytes).hexdigest()

def ack_comment(config, issue, m, approval, url, operator):
    match=ACK_URL.fullmatch(url or "")
    if not match or match.group(1)!=config["repo"] or int(match.group(2))!=config["queue_issue"]:
        raise batch.Refused("ACK URL must point to the trusted queue issue")
    comment_id=int(match.group(3))
    c=gh_api(f"repos/{config['repo']}/issues/comments/{comment_id}")
    if c.get("id")!=comment_id or c.get("user",{}).get("login")!=config["trusted_author"] or c.get("issue_url")!=f"https://api.github.com/repos/{config['repo']}/issues/{config['queue_issue']}":
        raise batch.Refused("ACK not a genuine comment by the trusted operator on this queue")
    line=f"BENCH ACK | issue={issue} | session={m['session_id']} | approval_sha256={approval} | operator={operator} | scope=whole-bench"
    if line not in c.get("body","").splitlines():
        raise batch.Refused("ACK does not bind the exact issue, session, approval, operator and entire bench")
    try:
        created=dt.datetime.fromisoformat(c["created_at"].replace("Z","+00:00"))
        lease_end=dt.datetime.fromisoformat(m["lease_expires_utc"].replace("Z","+00:00"))
    except (KeyError,ValueError,AttributeError) as exc:
        raise batch.Refused("invalid ACK timestamp") from exc
    age=dt.datetime.now(dt.timezone.utc)-created
    if not dt.timedelta(0)<=age<=dt.timedelta(minutes=45) or created>=lease_end:
        raise batch.Refused("ACK stale, future dated, or outside lease")
    # Full paginated queue comment scan; do not treat a single recent comment
    # or an unclaimed radio port as evidence the whole bench is idle.
    pages=gh_api(f"repos/{config['repo']}/issues/{config['queue_issue']}/comments?per_page=100",paginate=True)
    if not isinstance(pages,list) or not all(isinstance(p,list) for p in pages):
        raise batch.Refused("could not review all bench coordination comments")
    comments=[item for page in pages for item in page]
    if not any(item.get("id")==comment_id for item in comments):
        raise batch.Refused("ACK missing from complete coordination history")
    active=None
    for item in sorted(comments,key=lambda item:item.get("id",0)):
        body=item.get("body","").strip().upper()
        if body.startswith(("CLAIM ","CLAIM |","CLAIM /","RUNNING ","RUNNING |","RUNNING /")):
            active=item.get("id")
        if "RELEASE" in body.splitlines()[0] if body else False:
            active=None
        if item.get("id")==comment_id:
            if active is not None:
                raise batch.Refused("bench already claimed by another session at ACK time")
            active=comment_id
        if item.get("id",0)>comment_id and active!=comment_id:
            raise batch.Refused("new conflicting bench claim")
    if active!=comment_id:
        raise batch.Refused("ACK lease superseded or released")
    return c

def publish(config,issue,result,approval):
    # Use fixed-format results, not captured radio logs or issue contents.
    queue.check_github({"repo":config["repo"],"queue_issue":config["queue_issue"],
                        "trusted_author":config["trusted_author"]},[issue])
    body=(f"HARDWARE RUNNER RESULT | {result['result']}\n"
          f"Session {result['session_id']} | manifest/API approval {approval}\n"
          "Each job was independently preflighted, restored and verified by its reviewed harness. "
          "PASS is reported only when all steps returned success; inspect local evidence for measurements. "
          "No private RF capture or raw radio memory is attached.\n")
    with tempfile.NamedTemporaryFile(mode="w",prefix="flux-hw-result-",delete=True) as f:
        os.chmod(f.name,0o600);f.write(body);f.flush()
        subprocess.run(["gh","issue","comment",str(issue),"-R",config["repo"],
                        "--body-file",f.name],check=True,timeout=45)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config",type=Path,default=CONFIG)
    p.add_argument("--api-config",type=Path,default=batch.API_CONFIG)
    p.add_argument("--issue",type=int,required=True)
    p.add_argument("--execute",action="store_true")
    p.add_argument("--approve-digest")
    p.add_argument("--ack-url")
    p.add_argument("--operator")
    p.add_argument("--dummy-load-confirmed",action="store_true")
    p.add_argument("--publish",action="store_true")
    p.add_argument("--output-root",type=Path,default=Path.home()/"flux-bench/batches")
    p.add_argument("--lock-root",type=Path,default=Path.home()/".cache/flux/batch-locks")
    p.add_argument("--bench-lock",type=Path,default=Path.home()/".cache/flux/bench.lock")
    args=p.parse_args(argv)
    try:
        cfg=load_config(args.config)
        queue.check_github({"repo":cfg["repo"],"queue_issue":cfg["queue_issue"],
                            "trusted_author":cfg["trusted_author"]},[args.issue])
        m,hash_,raw=load_registered_manifest(cfg,args.issue)
        api,api_raw=batch.read_api_config(args.api_config)
        blockers=batch.check_api_compatibility(m,api)
        approval=digest_for(raw,api_raw)
        if not args.execute:
            print(json.dumps({"mode":"HARDWARE_DRY_RUN_NO_COMMANDS","issue":args.issue,
                "session_id":m["session_id"],"manifest_sha256":hash_,
                "approval_sha256":approval,"target_module_api":api["target_module_api"],
                "api_blockers":blockers,"lease_expires_utc":m["lease_expires_utc"],
                "jobs":[j["id"] for j in m["jobs"]]},indent=2))
            return 0
        if blockers: raise batch.Refused("API qualification blocked: "+"; ".join(blockers))
        if args.approve_digest!=approval: raise batch.Refused("no exact local manifest/API approval")
        if not args.operator or not args.dummy_load_confirmed:
            raise batch.Refused("operator and explicit current dummy-load confirmation required")
        batch._check_lease(m)
        ack_comment(cfg,args.issue,m,approval,args.ack_url,args.operator)
        # Cooperative WHOLE BENCH lock. Per-child canonical serial_lock is
        # still required by each reviewed hardware harness.
        args.bench_lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        with args.bench_lock.open("a+b") as f:
            try: fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc: raise batch.Refused("whole bench already in use") from exc
            queue.check_github({"repo":cfg["repo"],"queue_issue":cfg["queue_issue"],
                                "trusted_author":cfg["trusted_author"]},[args.issue])
            ack_comment(cfg,args.issue,m,approval,args.ack_url,args.operator)
            r=batch.run_session(m,raw,approval,output_root=args.output_root,
                lock_root=args.lock_root,approved_digest=approval,
                ack_url=args.ack_url,operator=args.operator,
                dummy_load_confirmed=True,api_config=api)
            if args.publish: publish(cfg,args.issue,r,approval)
            print(json.dumps({"result":r["result"],"session_id":m["session_id"],
                "output":str(args.output_root/m["session_id"]),"jobs":r["jobs"]},indent=2))
            return 0 if r["result"]=="PASS" else 1
    except (batch.Refused,queue.Refused,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print(f"REFUSED: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
