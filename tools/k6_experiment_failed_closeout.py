#!/usr/bin/env python3
"""Close exhausted, verified-failure Flux RF attempts as FAILED (never PASS).

Runs after the supervised hardware stage. This does NOT execute RF, schedule a
retry, or update immutable hardware evidence. A new materially revised trial
must use a NEW issue number, referencing the failed original.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import k6_experiment_attempt_budget as budget
import k6_experiment_autopilot as journal

STATE = Path.home()/".local/state/flux-experiment-autopilot/failed-closeouts.json"
REPO = "tcarwash/flux_radio"


def gh(*argv):
    p=subprocess.run(["gh",*argv],capture_output=True,text=True,
                     check=True,timeout=45)
    return p.stdout


def readable_failure(receipt):
    path=Path(receipt)
    logs=sorted(path.parent.glob("job*-run.log"))
    for log in logs:
        lines=log.read_text(errors="replace").splitlines()
        for line in reversed(lines):
            line=line.strip()
            if line.startswith(("RuntimeError:", "refused:", "InstallError:", "ValueError:")):
                return line[:300]
    return "hardware qualification did not meet its predeclared gates"


def closeout_comment(issue, verified):
    return (
       f"PHYSICAL EXPERIMENT FAILED / ATTEMPT BUDGET EXHAUSTED | issue #{issue}\n\n"
       f"The supervised bench has recorded **{verified['attempts']} separate physical "
       f"attempts** against the immutable history for this issue, exceeding the "
       f"per-issue limit of {budget.MAX_HARDWARE_ATTEMPTS}. "
       "No attempt achieved an independently verified experiment PASS.\n\n"
       f"Latest session: {verified['session']}\n"
       f"Last blocker: {readable_failure(verified['receipt'])}\n\n"
       "The latest session's independent whole-radio restore and verify both "
       "**PASS**, including original slots/VFO and TX-off baseline; the bench "
       "must be released before any new work. Prior unsuccessful sessions remain "
       "in the immutable local hardware journal, not overwritten.\n\n"
       "**Disposition: FAILED / closed as not planned, not completed.** "
       "The existing issue and all results remain searchable. "
       "An interested agent may submit a **new issue number** with changed "
       "parameters or harness, a clear hypothesis, and a reference to this "
       "failed issue. It must obtain new review and bench authority; reopening "
       "or editing this issue does not reset the attempt budget.\n"
    )


def decide(jobs):
    ids=set()
    for key in jobs:
        if key.startswith("issue-") and ":" in key:
            try:
                ids.add(int(key.split(":",1)[0].removeprefix("issue-")))
            except ValueError:
                pass
    return {n:budget.latest_verified_failure(n,jobs)
            for n in sorted(ids) if budget.closeout_eligible(n,jobs)}


def one_cycle(*, state_path=STATE, hardware_state=budget.DEFAULT_HARDWARE_STATE,
              preview=False):
    jobs=budget.load_jobs(hardware_state)
    eligible=decide(jobs)
    events=[]
    if preview:
        return {"mode":"READ_ONLY_FAILED_EXPERIMENT_CLOSEOUT",
                "max_attempts":budget.MAX_HARDWARE_ATTEMPTS,
                "events":[{"issue":n,"attempts":budget.count(n,jobs),
                           "action":"CLOSE_FAILED" if verified else "HOLD_UNVERIFIED_POSTFLIGHT"}
                          for n,verified in eligible.items()]}
    state_path=Path(state_path)
    state_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with state_path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX)
        state=journal.state_load(state_path)
        for n,verified in eligible.items():
            key=f"issue-{n}"
            if not verified:
                events.append({"issue":n,"action":"HOLD_UNVERIFIED_POSTFLIGHT"})
                continue
            if state["jobs"].get(key,{}).get("state")=="CLOSED_FAILED":
                events.append({"issue":n,"action":"ALREADY_CLOSED_FAILED"})
                continue
            try:
                info=json.loads(gh("api",f"repos/{REPO}/issues/{n}"))
                if info.get("state")=="closed":
                    state["jobs"][key]={"state":"EXTERNALLY_CLOSED",
                                        "attempts":verified["attempts"]}
                    journal.state_write(state_path,state)
                    events.append({"issue":n,"action":"ALREADY_CLOSED"})
                    continue
                if info.get("state")!="open" or info.get("pull_request"):
                    raise ValueError("cannot close a non-open issue")
                # Don't post another closure summary if the first update
                # succeeded but GitHub close failed or worker restarted.
                entry=state["jobs"].get(key,{})
                if entry.get("state")!="CLOSE_COMMENT_POSTED":
                    gh("issue","comment",str(n),"--repo",REPO,
                       "--body",closeout_comment(n,verified))
                    state["jobs"][key]={"state":"CLOSE_COMMENT_POSTED",
                                        "attempts":verified["attempts"],
                                        "session":verified["session"]}
                    journal.state_write(state_path,state)
                # Tag the terminal failure for cross-thread search.
                gh("issue","edit",str(n),"--repo",REPO,"--add-label","experiment:failed")
                gh("issue","close",str(n),"--repo",REPO,"--reason","not planned")
                after=json.loads(gh("api",f"repos/{REPO}/issues/{n}"))
                if after.get("state")!="closed":
                    raise ValueError("GitHub did not confirm closeout")
                state["jobs"][key].update(state="CLOSED_FAILED")
                journal.state_write(state_path,state)
                events.append({"issue":n,"action":"CLOSED_FAILED",
                               "attempts":verified["attempts"]})
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                events.append({"issue":n,"action":"CLOSEOUT_FAILED_RETRY_LATER",
                               "reason":f"{type(exc).__name__}: {exc}"[:240]})
    return {"mode":"PHYSICAL_ATTEMPT_CLOSEOUT_NO_RADIO",
            "max_attempts":budget.MAX_HARDWARE_ATTEMPTS,
            "events":events}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--once",action="store_true")
    g.add_argument("--preview",action="store_true")
    args=p.parse_args()
    try:
        print(json.dumps(one_cycle(preview=args.preview),indent=2))
        return 0
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f"FAILED EXPERIMENT CLOSEOUT: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
