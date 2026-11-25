#!/usr/bin/env python3
"""One immutable, cross-manifest physical attempt budget per Flux issue.

Only journaled one-shot physical session starts count. Offline tests,
preparation reviews and device-free repair attempts do not consume budget.
An issue number is a distinct trial family; changed parameters after exhaustion
must be submitted under a NEW issue. Previous evidence must never be erased.
"""
from __future__ import annotations
from pathlib import Path
import json

MAX_HARDWARE_ATTEMPTS = 3
DEFAULT_HARDWARE_STATE = Path.home() / ".local/state/flux-experiment-autopilot/hardware.json"
ATTEMPT_STATES = frozenset({"STARTED_NO_AUTO_RETRY", "PASS",
                            "BLOCKED_NO_AUTO_RETRY"})
FAILURE_STATES = frozenset({"BLOCKED_NO_AUTO_RETRY"})


def load_jobs(path=DEFAULT_HARDWARE_STATE):
    path=Path(path)
    if not path.exists():
        raise ValueError(f"missing hardware attempt journal: {path}")
    doc=json.loads(path.read_text())
    if doc.get("schema")!=1 or not isinstance(doc.get("jobs"),dict):
        raise ValueError("invalid hardware attempt journal; fail closed")
    return doc["jobs"]


def issue_attempts(issue,jobs):
    prefix=f"issue-{int(issue)}:"
    return sorted(
        ((key, entry) for key,entry in jobs.items()
         if key.startswith(prefix) and entry.get("state") in ATTEMPT_STATES),
        key=lambda pair:(pair[1].get("when",""),pair[0])
    )


def count(issue,jobs):
    return len(issue_attempts(issue,jobs))


def exhausted(issue,jobs):
    return count(issue,jobs)>=MAX_HARDWARE_ATTEMPTS


def closeout_eligible(issue,jobs):
    attempts=issue_attempts(issue,jobs)
    if len(attempts)<MAX_HARDWARE_ATTEMPTS:
        return False
    if any(rec.get("state")=="PASS" for _,rec in attempts):
        return False
    if any(rec.get("state") not in FAILURE_STATES for _,rec in attempts):
        return False
    return True


def latest_verified_failure(issue,jobs):
    """Only call an experiment FAIL when latest whole-radio postflight passed."""
    if not closeout_eligible(issue,jobs):
        return None
    key,last=issue_attempts(issue,jobs)[-1]
    receipt=last.get("evidence")
    if not receipt:
        return None
    path=Path(receipt)
    try:
        d=json.loads(path.read_text())
    except (ValueError,OSError):
        return None
    if d.get("result")!="BLOCKED" or d.get("session_id")!=last.get("session_id"):
        return None
    ends=[e for e in d.get("events",[]) if e.get("kind")=="SESSION_END"]
    if not ends or ends[-1].get("restored") is not True or ends[-1].get("verified") is not True:
        return None
    return {"key":key,"entry":last,"receipt":str(path),"session":d["session_id"],
            "attempts":len(issue_attempts(issue,jobs))}
