#!/usr/bin/env python3
"""Trusted GitHub issue intake and locally hash-pinned OFFLINE experiments.

No commands, paths, permissions, or arguments are read from GitHub.
This executable has no radio, serial, SDR, or hardware-dispatch mode.
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
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "config/k6-experiment-queue.json"
SHA = re.compile(r"^[a-f0-9]{64}$")
GIT_SHA = re.compile(r"^[a-f0-9]{40,64}$")

class Refused(RuntimeError):
    pass

def gh(*args):
    return json.loads(subprocess.run(["gh", *args], check=True, capture_output=True,
                                     text=True, timeout=45).stdout)

def load_registry(path):
    d = json.loads(Path(path).read_text())
    if not isinstance(d, dict) or set(d) != {"schema","repo","queue_issue","trusted_author","experiments"}:
        raise Refused("invalid local registry keys")
    if type(d["schema"]) is not int or d["schema"] != 1 or type(d["queue_issue"]) is not int or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", d["repo"]
    ) or not re.fullmatch(r"[A-Za-z0-9-]+", d["trusted_author"]):
        raise Refused("invalid local registry identity")
    if not isinstance(d["experiments"], dict):
        raise Refused("invalid local experiment registry")
    for key, e in d["experiments"].items():
        if not re.fullmatch(r"[1-9][0-9]*", key) or not isinstance(e, dict) or set(e) != {
            "kind","worktree","head_sha","argv","timeout_seconds","pins"
        }:
            raise Refused(f"invalid local experiment registration: {key}")
        if e["kind"] != "OFFLINE" or not isinstance(e["worktree"], str) or not Path(e["worktree"]).is_absolute():
            raise Refused(f"{key}: only explicitly reviewed offline worktrees supported")
        if not isinstance(e["head_sha"], str) or not GIT_SHA.fullmatch(e["head_sha"]):
            raise Refused(f"{key}: invalid commit pin")
        argv=e["argv"]
        if not isinstance(argv,list) or not argv or not all(isinstance(s,str) and s and "\0" not in s for s in argv) or not Path(argv[0]).is_absolute():
            raise Refused(f"{key}: executable must be an absolute argv array")
        t=e["timeout_seconds"]
        if type(t) is not int or not 1 <= t <= 300:
            raise Refused(f"{key}: invalid timeout")
        if not isinstance(e["pins"],list) or not e["pins"]:
            raise Refused(f"{key}: no reviewed file pins")
        for p in e["pins"]:
            if not isinstance(p,dict) or set(p)!={"path","sha256"} or not isinstance(p["path"],str) or not p["path"] or Path(p["path"]).is_absolute() or ".." in Path(p["path"]).parts or not isinstance(p["sha256"],str) or not SHA.fullmatch(p["sha256"]):
                raise Refused(f"{key}: invalid pin")
    return d

def ranked_ids(body,repo):
    prefix=re.escape(f"https://github.com/{repo}/issues/")
    pattern=re.compile(r"\[#(\d+)[^\]]*\]\(" + prefix + r"(\d+)\)")
    ranked=[]
    for line in body.splitlines():
        if line.startswith("|"):
            for a,b in pattern.findall(line):
                if a==b and int(a) not in ranked:
                    ranked.append(int(a))
    return ranked

def check_github(reg,numbers):
    repo=reg["repo"];owner=reg["trusted_author"]
    q=gh("issue","view",str(reg["queue_issue"]),"-R",repo,"--json","body,author,state")
    if q.get("author",{}).get("login")!=owner or q.get("state")!="OPEN":
        raise Refused("untrusted/closed coordination issue")
    order=ranked_ids(q.get("body",""),repo)
    for n in numbers:
        if n not in order:
            raise Refused(f"#{n} not ranked in trusted coordination issue")
        i=gh("issue","view",str(n),"-R",repo,"--json","author,state,title,number")
        if i.get("author",{}).get("login")!=owner or i.get("state")!="OPEN" or i.get("number")!=n:
            raise Refused(f"#{n} not open and authored by {owner}")
    return order

def git(cwd,*args):
    return subprocess.run(["git","-C",str(cwd),*args],check=True,capture_output=True,
                          text=True,timeout=25).stdout.strip()

def verify_local(n,e):
    cwd=Path(e["worktree"]).resolve(strict=True)
    if not cwd.is_dir() or git(cwd,"rev-parse","--show-toplevel")!=str(cwd):
        raise Refused(f"#{n}: not a repository worktree root")
    if git(cwd,"rev-parse","HEAD")!=e["head_sha"]:
        raise Refused(f"#{n}: pinned commit drift")
    if git(cwd,"status","--porcelain","--untracked-files=normal"):
        raise Refused(f"#{n}: worktree dirty")
    pinned=set()
    for p in e["pins"]:
        full=(cwd/p["path"]).resolve(strict=True)
        if not full.is_relative_to(cwd) or not full.is_file():
            raise Refused(f"#{n}: pin escapes worktree")
        if hashlib.sha256(full.read_bytes()).hexdigest()!=p["sha256"]:
            raise Refused(f"#{n}: pin drift: {p['path']}")
        pinned.add(str(full))
    if len(e["argv"])>1 and e["argv"][0] in ("/usr/bin/bash","/bin/bash","/usr/bin/python3","/usr/bin/env"):
        if len(e["argv"])>=5 and e["argv"][:4]==["/usr/bin/python3","-m","pytest","-q"]:
            tests=e["argv"][4:]
            if not tests or not all(t.startswith("tests/") and t.endswith(".py") and
                                    str((cwd/t).resolve(strict=True)) in pinned for t in tests):
                raise Refused(f"#{n}: every pytest target must be pinned")
        else:
            script=(cwd/e["argv"][1]).resolve(strict=True)
            if str(script) not in pinned:
                raise Refused(f"#{n}: unpinned entrypoint")
    return cwd

def private_write(p,payload):
    with os.fdopen(os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"wb") as f:
        f.write(payload)

def offline_once(n,reg,output_root):
    e=reg["experiments"][str(n)]
    cwd=verify_local(n,e)
    output_root.mkdir(mode=0o700,parents=True,exist_ok=True)
    out=output_root / f"issue-{n}-{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    out.mkdir(mode=0o700)
    started=dt.datetime.now(dt.timezone.utc).isoformat()
    log=out/"offline.log"
    status="ERROR";rc=None
    start=time.monotonic()
    with os.fdopen(os.open(log,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),"wb") as f:
        try:
            proc=subprocess.Popen(e["argv"],cwd=cwd,stdout=f,stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL,start_new_session=True,
                                  env=dict(os.environ,K6_EXPERIMENT_OFFLINE="1"))
            try:
                rc=proc.wait(timeout=e["timeout_seconds"])
                status="PASS" if rc==0 else "FAIL"
            except subprocess.TimeoutExpired:
                status="TIMEOUT"
                try: os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError: pass
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try: os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError: pass
                    proc.wait()
        except OSError as exc:
            f.write(f"launch error: {exc}\n".encode())
    elapsed=round(time.monotonic()-start,3)
    try: verify_local(n,e)
    except (Refused,OSError,subprocess.CalledProcessError) as exc:
        status="BLOCKED_SOURCE_DRIFT"
        with log.open("ab") as f: f.write(f"\nPOSTRUN source drift: {exc}\n".encode())
    result={
        "issue":n,"issue_url":f"https://github.com/{reg['repo']}/issues/{n}",
        "kind":"OFFLINE","result":status,"returncode":rc,"elapsed_seconds":elapsed,
        "started_utc":started,"completed_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "commit":e["head_sha"],"pins":e["pins"],"argv":e["argv"],
        "log_sha256":hashlib.sha256(log.read_bytes()).hexdigest(),"log":str(log),
        "hardware_access":"NOT REQUESTED BY DISPATCHER"}
    private_write(out/"result.json",json.dumps(result,indent=2).encode())
    return result

def publish_result(reg,result):
    check_github(reg,[result["issue"]])
    body=(
        f"OFFLINE RUNNER RESULT | {result['result']}\n\n"
        f"Reviewed local harness on pinned commit {result['commit']}.\n"
        f"Duration {result['elapsed_seconds']}s; exit code {result['returncode']}.\n"
        f"Private log SHA256 {result['log_sha256']}.\n"
        "No radio, RF, SDR, serial, or hardware lease requested by dispatcher. "
        "Offline result only; not RF evidence or hardware execution authorization.\n")
    with tempfile.NamedTemporaryFile(mode="w",prefix="flux-gh-result-",delete=True) as f:
        os.chmod(f.name,0o600)
        f.write(body);f.flush()
        subprocess.run(["gh","issue","comment",str(result["issue"]),"-R",reg["repo"],
                        "--body-file",f.name],check=True,timeout=45)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",type=Path,default=DEFAULT_REGISTRY)
    p.add_argument("--issue",type=int)
    p.add_argument("--all",action="store_true")
    p.add_argument("--run-offline",action="store_true")
    p.add_argument("--publish",action="store_true")
    p.add_argument("--output-root",type=Path,default=Path.home()/"flux-bench/offline-queue")
    args=p.parse_args(argv)
    try:
        if args.issue is not None and args.all: raise Refused("choose --issue or --all")
        if args.publish and not args.run_offline: raise Refused("--publish requires --run-offline")
        reg=load_registry(args.registry)
        nums=([args.issue] if args.issue is not None else
              [int(n) for n in reg["experiments"]] if args.all else [])
        if any(str(n) not in reg["experiments"] for n in nums):
            raise Refused("issue not locally registered")
        order=check_github(reg,nums)
        nums.sort(key=lambda n:order.index(n))
        if not args.run_offline:
            print(json.dumps({"mode":"READ_ONLY","trusted_author":reg["trusted_author"],
                 "ranked":order,"registered":sorted(int(n) for n in reg["experiments"]),
                 "selected":nums},indent=2))
            return 0
        if not nums: raise Refused("select --issue or --all")
        lockdir=Path.home()/".cache/flux/experiment-queue"
        lockdir.mkdir(mode=0o700,parents=True,exist_ok=True)
        with (lockdir/"offline-dispatch.lock").open("a+b") as lock:
            try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc: raise Refused("dispatcher already active") from exc
            results=[]
            for n in nums:
                check_github(reg,[n])
                r=offline_once(n,reg,args.output_root)
                results.append(r)
                if args.publish: publish_result(reg,r)
            print(json.dumps({"results":results},indent=2))
            return 0 if all(r["result"]=="PASS" for r in results) else 1
    except (Refused,OSError,ValueError,subprocess.SubprocessError,KeyError) as exc:
        print(f"REFUSED: {exc}",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
