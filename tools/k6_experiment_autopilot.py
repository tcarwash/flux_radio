#!/usr/bin/env python3
"""Persistent Flux experiment-queue coordinator; OFFLINE only until hardware is enrolled.

Ranked GitHub issues from tcarwash are *selection data*, never commands.
The offline runner executes only locally registered SHA-pinned harnesses.
Runs exactly once per registration digest; failures and crashes require
an explicit new reviewed registration before retrying.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import k6_experiment_dispatch as dispatch

SCHEMA=1
DEFAULT_STATE=Path.home()/".local/state/flux-experiment-autopilot/state.json"
DEFAULT_REGISTRY=dispatch.DEFAULT_REGISTRY
DEFAULT_OUTPUT=Path.home()/"flux-bench/offline-queue"


class Refused(RuntimeError):
    pass


def key(issue, entry):
    blob=json.dumps(entry,sort_keys=True,separators=(",",":")).encode()
    return f"issue-{issue}:{hashlib.sha256(blob).hexdigest()}"


def state_load(path):
    if not path.exists():
        return {"schema":SCHEMA,"jobs":{}}
    state=json.loads(path.read_text())
    if not isinstance(state,dict) or set(state)!={"schema","jobs"} or state["schema"]!=SCHEMA or not isinstance(state["jobs"],dict):
        raise Refused("invalid or corrupted controller journal; do not resume")
    return state


def state_write(path,state):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    # Never truncate the previous journal in place.
    fd,tmp=tempfile.mkstemp(prefix=".autopilot-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w") as f:
            json.dump(state,f,indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def existing_evidence(output, issue, entry):
    """Import earlier local PASS to avoid duplicated emulator work/results."""
    if not output.is_dir():
        return None
    for candidate in sorted(output.glob(f"issue-{issue}-*/result.json"),reverse=True):
        try:
            result=json.loads(candidate.read_text())
            if (result.get("issue")==issue
                and result.get("kind")=="OFFLINE"
                and result.get("commit")==entry["head_sha"]
                and result.get("pins")==entry["pins"]
                and result.get("argv")==entry["argv"]
                and result.get("result")=="PASS"
                and hashlib.sha256(Path(result["log"]).read_bytes()).hexdigest()==result["log_sha256"]):
                return result
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return None


def one_cycle(registry_path=DEFAULT_REGISTRY,state_path=DEFAULT_STATE,
              output_root=DEFAULT_OUTPUT,publish=False,max_jobs=2):
    registry=dispatch.load_registry(registry_path)
    all_issues=sorted(map(int,registry["experiments"]))
    order=dispatch.check_github(registry,all_issues)
    selected=sorted(all_issues,key=order.index)
    state_path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    lockfile=state_path.with_suffix(".lock")
    with lockfile.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("another autopilot process holds the controller lock") from exc
        state=state_load(state_path)
        events=[]
        count=0
        for issue in selected:
            ident=key(issue,registry["experiments"][str(issue)])
            if ident in state["jobs"]:
                prior=state["jobs"][ident]["state"]
                events.append({"issue":issue,"action":"SKIPPED_ALREADY_ATTEMPTED",
                              "state":prior})
                if prior not in ("PASS","IMPORTED_PASS"):
                    events.append({"issue":issue,"action":"HALTED_ON_PRIOR_FAILURE"})
                    break
                continue
            if count>=max_jobs: break
            # Previously published completed evidence is authoritative for dedupe,
            # not a reason to execute it again.
            historical=existing_evidence(output_root,issue,registry["experiments"][str(issue)])
            if historical:
                state["jobs"][ident]={"state":"IMPORTED_PASS","when":dt.datetime.now(dt.timezone.utc).isoformat(),
                                      "evidence":historical["log"],"result_sha":historical["log_sha256"]}
                state_write(state_path,state)
                events.append({"issue":issue,"action":"IMPORTED_PREVIOUS_PASS"})
                continue
            dispatch.check_github(registry,[issue])
            dispatch.verify_local(issue,registry["experiments"][str(issue)])
            state["jobs"][ident]={"state":"STARTED","when":dt.datetime.now(dt.timezone.utc).isoformat()}
            state_write(state_path,state)
            # A power failure after STARTED is deliberately NOT retried unattended.
            count+=1
            try:
                result=dispatch.offline_once(issue,registry,output_root)
                state["jobs"][ident]={"state":result["result"],"when":result["completed_utc"],
                                      "evidence":result["log"],"result_sha":result["log_sha256"]}
                state_write(state_path,state)
                events.append({"issue":issue,"action":"EXECUTED","result":result["result"]})
                if publish:
                    dispatch.publish_result(registry,result)
                    state["jobs"][ident]["published"]=True
                    state_write(state_path,state)
            except BaseException as exc:
                # Never retry a partial operation automatically.
                state["jobs"][ident]["state"]="BLOCKED_NO_AUTO_RETRY"
                state["jobs"][ident]["error"]=f"{type(exc).__name__}: {exc}"
                state_write(state_path,state)
                raise
        return {"mode":"AUTONOMOUS_OFFLINE_ONLY","trusted_author":registry["trusted_author"],
                "events":events,"journal":str(state_path)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry",type=Path,default=DEFAULT_REGISTRY)
    p.add_argument("--state",type=Path,default=DEFAULT_STATE)
    p.add_argument("--output-root",type=Path,default=DEFAULT_OUTPUT)
    p.add_argument("--once",action="store_true",help="perform one bounded iteration")
    p.add_argument("--publish",action="store_true",help="publish sanitized result to verified GitHub issue")
    p.add_argument("--max-jobs",type=int,default=2)
    args=p.parse_args(argv)
    if not args.once:
        p.error("use --once; schedule via a systemd timer, not an unbounded shell loop")
    if not 1<=args.max_jobs<=20:
        p.error("max jobs must be 1..20")
    try:
        result=one_cycle(args.registry,args.state,args.output_root,args.publish,args.max_jobs)
        print(json.dumps(result,indent=2))
        return 0
    except (Refused,dispatch.Refused,ValueError,OSError,KeyError) as exc:
        print(f"REFUSED: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
