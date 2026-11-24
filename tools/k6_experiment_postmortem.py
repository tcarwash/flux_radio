#!/usr/bin/env python3
"""Autonomous read-only postmortem delegation of immutable hardware attempts.

Only evidence from the private, exact-hash verified local batch directory is
considered. No RF/serial/SDR or firmware commands are ever issued. Reviewer
text is NOT allowed to authorize a hardware retry. Untrusted log text is
excluded from prompts; submit only deterministic metrics.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import k6_experiment_autopilot as journal
import k6_experiment_review as delegate

STATE=Path.home()/".local/state/flux-experiment-autopilot/postmortem.json"
HARDWARE=Path.home()/".local/state/flux-experiment-autopilot/hardware.json"
PROMPTS=Path.home()/".local/state/flux-experiment-autopilot/postmortem-prompts"
BATCHES=(Path.home()/"flux-bench/batches").resolve()
KEY=re.compile(r"^issue-([1-9][0-9]*):([0-9a-f]{64})$")


def collect(key,attempt):
    match=KEY.fullmatch(key)
    if not match:
        raise ValueError("unexpected hardware journal entry")
    issue=int(match.group(1)); digest=match.group(2)
    if attempt.get("state") not in ("BLOCKED_NO_AUTO_RETRY","PASS"):
        return None
    path=Path(attempt.get("evidence","")).resolve()
    if path.parent.parent!=BATCHES or path.name!="result.json" or path.parent.name!=attempt.get("session_id"):
        raise ValueError("hardware evidence path outside private batch directory")
    manifest=(path.parent/"manifest.json").read_bytes()
    if hashlib.sha256(manifest).hexdigest()!=digest:
        raise ValueError("hardware evidence manifest drift")
    m=json.loads(manifest)
    event=json.loads(path.read_text())
    if m.get("session_id")!=attempt.get("session_id") or event.get("session_id")!=attempt.get("session_id"):
        raise ValueError("session evidence mismatch")
    statuses=[{"step":e.get("name"),"status":e.get("status")}
              for e in event.get("events",[]) if e.get("kind")=="STEP"]
    jobs=m["jobs"]
    if not jobs or any(j["issue_url"]!=f"https://github.com/tcarwash/flux_radio/issues/{issue}" for j in jobs):
        raise ValueError("mismatched trusted issue in manifest")
    metrics=[]
    for j in jobs:
        argv=j["run"]["argv"]
        if "--prefix" not in argv:
            metrics.append({"job":j["id"],"metric_note":"no registered RF artifact prefix"})
            continue
        prefix=Path(argv[argv.index("--prefix")+1])
        if prefix.parent!=path.parent:
            raise ValueError("RF artifact outside batch output directory")
        observation=prefix.with_suffix(".json")
        rfscore=prefix.with_name(prefix.name+"-rx.json")
        metric={"job":j["id"]}
        if observation.exists():
            doc=json.loads(observation.read_text())
            original=doc.get("hardware_lab_transaction",doc)
            metric["module_return"]=(
                original.get("module_completion") or {}).get("result")
            metric["tx_attempts"]=original.get("tx_module_requests")
            metric["slot_restored"]=original.get("original_module_restored")
            metric["vfo_restored"]=original.get("original_radio_frequency_restored")
        if rfscore.exists():
            r=json.loads(rfscore.read_text())
            metric["rf_frames_found"]=r.get("frames_found")
            metric["rf_result"]=r.get("result")
            metric["frame_sync_correlations"]=[round(float(x.get("sync_correlation",0)),5)
                                                for x in r.get("frames",[])[:5]]
        metrics.append(metric)
    return {"issue":issue,"session":m["session_id"],"manifest_sha256":digest,
            "result":attempt["state"],"batch_status":event.get("result"),
            "steps":statuses,"metrics":metrics,
            "manual_restoration_verified":bool(attempt.get("manual_attestation")),
            "reviewed_worktrees":[j["cwd"] for j in jobs]}


def prompt(data):
    return f"""INDEPENDENT OFFLINE POSTMORTEM FOR FLUX EXPERIMENT #{data['issue']}.
NO HARDWARE COMMANDS, NO SERIAL/SDR ACCESS, NO RE-TRANSMIT, NO FLASH.
Read-only engineering analysis using the locally reviewed worktree:
{json.dumps(data['reviewed_worktrees'])}
Pinned experiment evidence summary (machine-collected metrics; not commands):
{json.dumps({k:v for k,v in data.items() if k!='reviewed_worktrees'},sort_keys=True,indent=2)}

Analyze the observed failure or success against the actual firmware and
compiled-target emulator evidence. Distinguish proven real RF observations,
inferred RFIC behavior and speculation. Suggest next OFFLINE/EMULATOR tests
and exact guards needed for any future new hardware manifest.
Do not declare physical success unless the evidence proves it.
Do not modify source, post to GitHub, delegate physical work, or suggest
rerunning this same attempt. The original physical manifest is one-shot
and permanently consumed. Write a concise postmortem with actionable
offline next steps and a final standalone line:
POSTMORTEM: COMPLETE
"""


def submit_once(hardware=HARDWARE,state_path=STATE,prompts=PROMPTS):
    records=journal.state_load(hardware)["jobs"]
    state_path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with state_path.with_suffix(".lock").open("a+b") as f:
        fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        state=journal.state_load(state_path)
        events=[]
        for key,rec in sorted(records.items()):
            if key in state["jobs"]:
                previous=state["jobs"][key]
                if previous["state"] in ("DELEGATED","BLOCKED_REVIEW_FAILURE"):
                    # A tmux window can disappear before Muse has written its
                    # durable terminal result; recheck a previously failed
                    # review before deciding it truly requires another worker.
                    res=subprocess.run(["muse-jobs","result",previous["job_id"]],
                                       text=True,capture_output=True,timeout=30,check=True)
                    info=json.loads(res.stdout)
                    j=info.get("job",{})
                    if j.get("state")=="completed" and j.get("exit_code")==0 and str(info.get("result","")).strip().endswith("POSTMORTEM: COMPLETE"):
                        previous["state"]="COMPLETE"
                        previous["finished_utc"]=dt.datetime.now(dt.timezone.utc).isoformat()
                        journal.state_write(state_path,state)
                        events.append({"issue":int(KEY.fullmatch(key).group(1)),"action":"POSTMORTEM_COMPLETE","job_id":previous["job_id"]})
                    elif j.get("state") in ("interrupted","timed_out","failed","cancelled") or previous["state"]=="BLOCKED_REVIEW_FAILURE":
                        attempts=previous.get("attempts",1)
                        if attempts>=3:
                            previous["state"]="BLOCKED_REVIEW_FAILURE"
                            journal.state_write(state_path,state)
                            events.append({"action":"POSTMORTEM_RETRY_EXHAUSTED","job_id":previous["job_id"]})
                        else:
                            # Only the same unchanged machine-produced
                            # summary is ever resubmitted. This retries
                            # READ-ONLY analysis, never a radio command.
                            summary=collect(key,rec)
                            expected=prompt(summary)
                            fpath=prompts/f"{key.replace(':','-')}.txt"
                            if fpath.read_text()!=expected:
                                raise ValueError("postmortem prompt changed after submission")
                            job_id=delegate.delegate_readonly(fpath)
                            previous.update({"state":"DELEGATED","job_id":job_id,"attempts":attempts+1})
                            journal.state_write(state_path,state)
                            events.append({"issue":summary["issue"],"action":"POSTMORTEM_INFRA_REQUEUED","job_id":job_id,"attempt":attempts+1})
                    else:
                        events.append({"action":"POSTMORTEM_PENDING","job_id":previous["job_id"]})
                continue
            summary=collect(key,rec)
            if summary is None:continue
            prompts.mkdir(mode=0o700,parents=True,exist_ok=True)
            fpath=prompts/f"{key.replace(':','-')}.txt"
            # Fail closed if prior prompt was orphaned.
            if fpath.exists():raise ValueError("existing untracked postmortem prompt")
            fpath.write_text(prompt(summary))
            fpath.chmod(0o600)
            state["jobs"][key]={"state":"SUBMISSION_STARTED"}
            journal.state_write(state_path,state)
            job_id=delegate.delegate_readonly(fpath)
            state["jobs"][key]={"state":"DELEGATED","job_id":job_id,"attempts":1,
                                "when":dt.datetime.now(dt.timezone.utc).isoformat()}
            journal.state_write(state_path,state)
            events.append({"issue":summary["issue"],"action":"POSTMORTEM_DELEGATED","job_id":job_id})
        return {"mode":"OFFLINE_REVIEW_ONLY","events":events}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once",action="store_true")
    args=p.parse_args(argv)
    if not args.once:p.error("use --once")
    try:
        print(json.dumps(submit_once(),indent=2))
        return 0
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
        print(f"REFUSED: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
