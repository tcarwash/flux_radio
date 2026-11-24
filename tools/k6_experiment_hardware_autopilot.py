#!/usr/bin/env python3
"""One-shot autonomous radio/SDR controller for *locally approved* bench jobs.

GitHub supplies identity, ranked priority and audit trail, NEVER commands.
No generic agent can authorize a physical command. The reviewed manifest,
exact source hashes, approved dummy-load policy, current radio checks and
a separate read-only delegated review must all pass. Every attempted
session is journaled BEFORE posting a bench claim; never retry automatically.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import k6_experiment_autopilot as journal
import k6_experiment_batch as batch
import k6_experiment_dispatch as queue
import k6_experiment_hardware as hw
import k6_experiment_review as review

ROOT=Path(__file__).resolve().parents[1]
POLICY=ROOT/"config/k6-experiment-hardware-standing.json"
STATE=Path.home()/".local/state/flux-experiment-autopilot/hardware.json"
REVIEW=review.DEFAULT_STATE
BENCH_LOCK=Path.home()/".cache/flux/bench.lock"
IDENTITY="tcarwash"
OPERATOR="AG7SU"
BUILD_RE=re.compile(r"^[a-f0-9]{16}$")


def standing_policy(path):
    d=json.loads(Path(path).read_text())
    if not isinstance(d,dict) or set(d)!={"schema","enabled","trusted_operator","environment","approved"}:
        raise batch.Refused("invalid standing bench policy")
    if d["schema"]!=1 or type(d["enabled"]) is not bool or d["trusted_operator"]!=OPERATOR or d["environment"]!="dummy_load":
        raise batch.Refused("standing bench approval is limited to operator AG7SU and dummy load")
    if not isinstance(d["approved"],dict):
        raise batch.Refused("invalid reviewed manifest grants")
    for n,e in d["approved"].items():
        if not re.fullmatch(r"[1-9]\d*",n) or not isinstance(e,dict) or set(e)!={"manifest_sha256","expected_build","exact_api","expires_utc","one_shot","dummy_load_confirmed","no_cwid_for_dummy_load"}:
            raise batch.Refused("malformed local hardware grant")
        if not batch.SHA_RE.fullmatch(e["manifest_sha256"]) or not BUILD_RE.fullmatch(e["expected_build"]):
            raise batch.Refused("invalid approved build or manifest digest")
        if type(e["exact_api"]) is not int or not 1<=e["exact_api"]<=255:
            raise batch.Refused("invalid firmware module API")
        if e["one_shot"] is not True or e["dummy_load_confirmed"] is not True or type(e["no_cwid_for_dummy_load"]) is not bool:
            raise batch.Refused("unbounded or unconfirmed RF run forbidden")
        try:
            end=dt.datetime.fromisoformat(e["expires_utc"].replace("Z","+00:00"))
        except (ValueError,AttributeError) as exc:
            raise batch.Refused("invalid grant expiry") from exc
        if end.tzinfo is None:
            raise batch.Refused("grant expiry must be timezone aware")
    return d


def secure_approval(cfg,n,grant,review_state):
    m,manifest_sha,raw=hw.load_registered_manifest(cfg,n)
    if manifest_sha!=grant["manifest_sha256"]:
        raise batch.Refused("standing authorization is for a DIFFERENT manifest")
    api,api_raw=batch.read_api_config(batch.API_CONFIG)
    if api["target_module_api"]!=grant["exact_api"]:
        raise batch.Refused("API config differs from the approved hardware image")
    if dt.datetime.now(dt.timezone.utc)>=dt.datetime.fromisoformat(grant["expires_utc"].replace("Z","+00:00")):
        raise batch.Refused("local standing approval expired")
    batch.check_command_pins(m)
    if batch.check_api_compatibility(m,api):
        raise batch.Refused("module API incompatible")
    batch._check_lease(m)
    # The fixed, locally pinned session probe must require exact firmware
    # identity and verify post-run packed slots against its pre-run baseline.
    stages=(m["session_preflight"],m["session_restore"],m["session_verify"])
    for stage in stages:
        argv=stage["argv"]
        if "--expected-build" not in argv or argv[argv.index("--expected-build")+1]!=grant["expected_build"]:
            raise batch.Refused("physical build not attested in session stage")
        if "--exact-api" not in argv or argv[argv.index("--exact-api")+1]!=str(grant["exact_api"]):
            raise batch.Refused("physical API not attested in session stage")
        if "--include-slots" not in argv:
            raise batch.Refused("full packed directory snapshot is mandatory")
    if "--output" not in stages[0]["argv"] or any("--baseline" not in x["argv"] for x in stages[1:]):
        raise batch.Refused("full before/after slot-hash restoration evidence missing")
    approval=hw.digest_for(raw,api_raw)
    review_id=review.fingerprint(n,manifest_sha,api_raw)
    decision=review_state["jobs"].get(review_id,{})
    if decision.get("state")!="APPROVED" or not decision.get("job_id"):
        raise batch.Refused("independent delegated manifest validation has not APPROVED")
    # Read the final actual delegate response again, not just a cached value.
    if review.result_status(decision["job_id"])!="APPROVED":
        raise batch.Refused("independent reviewer no longer confirms approval")
    return m,raw,approval


def ordered_comments(config):
    pages=hw.gh_api(f"repos/{config['repo']}/issues/{config['queue_issue']}/comments?per_page=100",paginate=True)
    if not isinstance(pages,list) or not all(isinstance(p,list) for p in pages):
        raise batch.Refused("cannot inspect complete shared bench history")
    return sorted((v for page in pages for v in page),key=lambda v:v.get("id",0))


def require_unclaimed_bench(config):
    active=None
    for c in ordered_comments(config):
        if c.get("user",{}).get("login")!=config["trusted_author"]:
            continue
        lines=c.get("body","").strip().splitlines()
        header=lines[0].upper().strip() if lines else ""
        if ("RELEASE" in header or header.startswith("BENCH FREE |")):
            active=None
        elif header.startswith(("CLAIM ","CLAIM |","CLAIM /","RUNNING ","RUNNING |",
                                "RUNNING /","ACK / CLAIM","BENCH ACK |","BLOCKED / HOLD")):
            active=c.get("id")
    if active is not None:
        raise batch.Refused("another unreleased whole-bench claim is active")


def github_identity(config):
    p=subprocess.run(["gh","api","user","--jq",".login"],capture_output=True,text=True,check=True,timeout=25)
    if p.stdout.strip()!=config["trusted_author"] or config["trusted_author"]!=IDENTITY:
        raise batch.Refused("GitHub session is not authenticated as trusted owner")


def queue_comment(config,body):
    # FIXED locally authored metadata, never execute GitHub content.
    with tempfile.NamedTemporaryFile(mode="w",prefix="flux-bench-comment-",delete=True) as f:
        os.chmod(f.name,0o600);f.write(body);f.flush()
        p=subprocess.run(["gh","issue","comment",str(config["queue_issue"]),"-R",config["repo"],
                          "--body-file",f.name],capture_output=True,text=True,check=True,timeout=45)
    url=p.stdout.strip()
    if not hw.ACK_URL.fullmatch(url):
        raise batch.Refused("GitHub did not return a canonical bench comment URL")
    return url


def postflight_verified(result):
    """A failed *experiment* can release the bench after trusted global restore.

    Never trust a job's own restore verdict here. Only the separately
    SHA-pinned session-level baseline checker can release the whole bench.
    A missing/failed global postflight must remain BLOCKED / HOLD.
    """
    events = result.get("events", [])
    return all(
        any(isinstance(e, dict) and e.get("name")==stage and
            e.get("status")=="PASS" and e.get("returncode")==0
            for e in events)
        for stage in ("session_restore", "session_verify")
    )


def one_cycle(config_path=hw.CONFIG,policy_path=POLICY,state_path=STATE,review_path=REVIEW,
              bench_lock=BENCH_LOCK,output_root=None):
    cfg=hw.load_config(config_path)
    policy=standing_policy(policy_path)
    if not policy["enabled"]:
        return {"mode":"HARDWARE_DISABLED","events":[]}
    if cfg["repo"]!="tcarwash/flux_radio" or cfg["trusted_author"]!=IDENTITY:
        raise batch.Refused("unauthorized repository/operator configuration")
    if not policy["approved"]:
        return {"mode":"HARDWARE_UNENROLLED","events":[]}
    state_path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    lockpath=state_path.with_suffix(".lock")
    with lockpath.open("a+b") as guard:
        try:fcntl.flock(guard.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise batch.Refused("another hardware controller is active") from exc
        state=journal.state_load(state_path)
        reviews=journal.state_load(review_path)
        issues=sorted(int(x) for x in policy["approved"])
        order=queue.check_github({"repo":cfg["repo"],"queue_issue":cfg["queue_issue"],
                                  "trusted_author":cfg["trusted_author"]},issues)
        events=[]
        for n in sorted(issues,key=order.index):
            grant=policy["approved"][str(n)]
            key=f"issue-{n}:{grant['manifest_sha256']}"
            if key in state["jobs"]:
                events.append({"issue":n,"state":state["jobs"][key]["state"],"action":"NO_AUTO_RETRY"})
                continue
            # Do not mark as attempted for a pending/failed review: it can be fixed
            # only by changing and re-reviewing the local pinned manifest.
            try:
                m,raw,approval=secure_approval(cfg,n,grant,reviews)
            except batch.Refused as exc:
                events.append({"issue":n,"action":"WAITING_FOR_LOCAL_APPROVAL","reason":str(exc)})
                continue
            github_identity(cfg)
            bench_lock.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            with bench_lock.open("a+b") as bench:
                try:fcntl.flock(bench.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    events.append({"issue":n,"action":"BENCH_BUSY"});break
                require_unclaimed_bench(cfg)
                # Reconfirm trustworthy queue and local pins within whole-bench lock.
                queue.check_github({"repo":cfg["repo"],"queue_issue":cfg["queue_issue"],
                                    "trusted_author":cfg["trusted_author"]},[n])
                m,raw,approval=secure_approval(cfg,n,grant,journal.state_load(review_path))
                stamp=dt.datetime.now(dt.timezone.utc).isoformat()
                state["jobs"][key]={"state":"STARTED_NO_AUTO_RETRY","when":stamp,
                                    "session_id":m["session_id"],"approval_sha256":approval}
                journal.state_write(state_path,state)
                ack=None
                try:
                    line=f"BENCH ACK | issue={n} | session={m['session_id']} | approval_sha256={approval} | operator={OPERATOR} | scope=whole-bench"
                    ack=queue_comment(cfg,line+"\nAUTONOMOUS ONE-SHOT | local trusted permit, dummy load, independently validated harness; no automatic retry.\n")
                    state["jobs"][key]["ack_url"]=ack
                    journal.state_write(state_path,state)
                    hw.ack_comment(cfg,n,m,approval,ack,OPERATOR)
                    result=batch.run_session(m,raw,approval,output_root=output_root or Path.home()/"flux-bench/batches",
                        lock_root=Path.home()/".cache/flux/batch-locks",
                        approved_digest=approval,ack_url=ack,operator=OPERATOR,dummy_load_confirmed=True,
                        api_config=batch.read_api_config(batch.API_CONFIG)[0])
                    passed=result["result"]=="PASS"
                    state["jobs"][key]["state"]="PASS" if passed else "BLOCKED_NO_AUTO_RETRY"
                    state["jobs"][key]["evidence"]=str((output_root or Path.home()/"flux-bench/batches")/m["session_id"]/"result.json")
                    journal.state_write(state_path,state)
                    if passed:
                        queue_comment(cfg,f"PASS / RELEASE | Flux autonomous bench | issue #{n} session {m['session_id']} | full original packed slot hashes and VFO restored, session status PASS. Whole bench released.\n")
                        hw.publish(cfg,n,result,approval)
                    elif postflight_verified(result):
                        # Do not leave a stale GH claim after the exact
                        # independent whole-bench baseline was restored.
                        queue_comment(cfg,f"BLOCKED / RELEASE | Flux autonomous bench | issue #{n} session {m['session_id']} | experiment failed but independent session restore + verify PASS (full slot hashes/VFO/idle). This manifest is consumed, no auto-retry.\n")
                    else:
                        queue_comment(cfg,f"BLOCKED / HOLD | Flux autonomous bench | issue #{n} session {m['session_id']} | physical state may be unverified. No repeat without operator inspection.\n")
                    events.append({"issue":n,"action":"PHYSICAL_ONE_SHOT","result":result["result"]})
                    break
                except BaseException as exc:
                    state["jobs"][key]["state"]="BLOCKED_NO_AUTO_RETRY"
                    state["jobs"][key]["error"]=f"{type(exc).__name__}: {exc}"
                    journal.state_write(state_path,state)
                    if ack:
                        try:queue_comment(cfg,f"BLOCKED / HOLD | Flux autonomous bench | issue #{n} session {m['session_id']} | abort, operator inspection required.\n")
                        except (OSError,subprocess.SubprocessError):pass
                    raise
        return {"mode":"AUTONOMOUS_APPROVED_PHYSICAL_ONLY","events":events,
                "journal":str(state_path)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once",action="store_true")
    p.add_argument("--policy",type=Path,default=POLICY)
    p.add_argument("--config",type=Path,default=hw.CONFIG)
    p.add_argument("--state",type=Path,default=STATE)
    p.add_argument("--review-state",type=Path,default=REVIEW)
    args=p.parse_args(argv)
    if not args.once:p.error("only one bounded hardware queue iteration at a time")
    try:
        print(json.dumps(one_cycle(args.config,args.policy,args.state,args.review_state),indent=2))
        return 0
    except (batch.Refused,queue.Refused,ValueError,OSError,KeyError,subprocess.SubprocessError) as exc:
        print(f"REFUSED: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
