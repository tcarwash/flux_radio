#!/usr/bin/env python3
"""Exclusive, reversible operator mode for the Flux experiment runner.

An agent can stop both *physical* timers, acquire a persistent ownership
record, run one of the existing trusted supervisors on demand, and resume
the original timers. This does not invent a second radio controller.
Offline fixture tests remain independent and are never paused here.
"""
from __future__ import annotations
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

BASE = Path(__file__).resolve().parents[1]
STATE = Path.home()/".local/state/flux-experiment-autopilot/manual-owner.json"
TIMERS = ("flux-experiment-autopilot.timer", "flux-experiment-candidate.timer")
SERVICES = ("flux-experiment-autopilot.service", "flux-experiment-candidate.service")
PYTHON = "/usr/bin/python3"
STAGES = {
    "cycle": ["systemctl", "--user", "start", "--wait", "flux-experiment-autopilot.service"],
    "candidate": ["systemctl", "--user", "start", "--wait", "flux-experiment-candidate.service"],
    "intake": [PYTHON, str(BASE/"tools/k6_experiment_intake.py"),
               "--once","--max-new","4","--max-active","4","--max-daily","32","--hardware-only"],
    "promotion": [PYTHON, str(BASE/"tools/k6_experiment_promotion.py"),"--once"],
    "review": [PYTHON, str(BASE/"tools/k6_experiment_review.py"),"--once",
               "--config",str(Path.home()/".local/state/flux-experiment-autopilot/hardware-generated.json")],
    "grants": [PYTHON, str(BASE/"tools/k6_experiment_promotion.py"),"--once","--grants"],
    "postmortem": [PYTHON, str(BASE/"tools/k6_experiment_postmortem.py"),"--once"],
    "hardware": [PYTHON, str(BASE/"tools/k6_experiment_hardware_autopilot.py"),
                 "--once",
                 "--config",str(Path.home()/".local/state/flux-experiment-autopilot/hardware-generated.json"),
                 "--policy",str(Path.home()/".local/state/flux-experiment-autopilot/hardware-generated-standing.json")],
    "closeout": [PYTHON,str(BASE/"tools/k6_experiment_failed_closeout.py"),"--once"],
}

class Refused(RuntimeError):
    pass


def systemctl(*args):
    env=os.environ.copy()
    env.setdefault("XDG_RUNTIME_DIR",f"/run/user/{os.getuid()}")
    result=subprocess.run(["systemctl","--user",*args],capture_output=True,text=True,
                          env=env,timeout=20)
    if result.returncode:
        raise Refused(f"systemctl {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def active(unit):
    return systemctl("show","--property=ActiveState","--value",unit)=="active"


def ensure_idle():
    running=[unit for unit in SERVICES if systemctl("show","--property=ActiveState",
                                                     "--value",unit) in ("active","activating","reloading")]
    if running:
        raise Refused("worker still running: "+", ".join(running)+"; try after it finishes")


def read_owner(state=STATE):
    if not Path(state).exists():
        return None
    data=json.loads(Path(state).read_text())
    if data.get("schema")!=1 or not isinstance(data.get("original_timers"),dict):
        raise Refused("invalid manual owner journal; refuse to overwrite")
    return data


def write_owner(data,state=STATE):
    state=Path(state)
    state.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix=".owner-",dir=state.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w") as file:
            json.dump(data,file,indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp,state)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def command(args):
    state=Path(getattr(args,"state",STATE))
    state.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.command=="status":
        owner=read_owner(state)
        return {"mode":"MANUAL" if owner else "SCHEDULED",
                "owner": owner.get("owner") if owner else None,
                "started":owner.get("started") if owner else None,
                "timers":{unit:active(unit) for unit in TIMERS},
                "services":{unit:systemctl("show","--property=ActiveState","--value",unit)
                            for unit in SERVICES},
                "fixture_timer_unaffected":True}
    with state.with_suffix(".lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("manual owner operation already running") from exc
        owner=read_owner(state)
        if args.command=="takeover":
            if owner:
                raise Refused(f"already owned by {owner['owner']}; request a release or explicit handoff")
            ensure_idle()
            was={unit:active(unit) for unit in TIMERS}
            try:
                for unit in TIMERS:
                    systemctl("stop",unit)
                ensure_idle()
            except Exception:
                for unit in TIMERS:
                    if was[unit]:
                        try:systemctl("start",unit)
                        except Refused:pass
                raise
            key=secrets.token_urlsafe(24)
            doc={"schema":1,"owner":args.owner,"lease":key,
                 "started":dt.datetime.now(dt.timezone.utc).isoformat(),
                 "original_timers":was}
            write_owner(doc,state)
            return {"mode":"MANUAL","owner":args.owner,"lease":key,
                    "physical_timers_paused":list(TIMERS),
                    "fixture_timer_unaffected":True}
        if owner is None:
            raise Refused("no manual owner; use takeover first")
        if args.lease!=owner["lease"]:
            raise Refused("manual lease mismatch; owner is "+owner["owner"])
        if args.command=="release":
            ensure_idle()
            for unit,wanted in owner["original_timers"].items():
                if wanted: systemctl("start",unit)
                else: systemctl("stop",unit)
            state.unlink()
            return {"mode":"SCHEDULED","released_owner":owner["owner"],
                    "timers_restored":owner["original_timers"]}
        if args.command=="run":
            if any(active(unit) for unit in TIMERS):
                raise Refused("physical scheduler is active; manual run refused")
            ensure_idle()
            stage=args.stage
            if args.issue is not None and stage!="hardware":
                raise Refused("--issue only applies to the hardware stage")
            cmd=list(STAGES[stage])
            if args.issue is not None:
                if args.issue<1:raise Refused("invalid issue number")
                cmd += ["--issue",str(args.issue)]
            if cmd[0]=="systemctl":
                env=os.environ.copy()
                env.setdefault("XDG_RUNTIME_DIR",f"/run/user/{os.getuid()}")
            else:
                env=os.environ.copy()
            # Preserve full logs in the local manual ledger; report only tail.
            stamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            logfile=state.parent/f"manual-{stage}-{stamp}.log"
            with logfile.open("x") as output:
                outcome=subprocess.run(cmd,env=env,cwd=BASE,stdout=output,
                                       stderr=subprocess.STDOUT,timeout=620)
            return {"mode":"MANUAL","stage":stage,"issue":args.issue,
                    "exit_code":outcome.returncode,
                    "log":str(logfile),
                    "tail":logfile.read_text(errors="replace")[-5500:]}
    raise Refused("unsupported manual operation")


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state",type=Path,default=STATE,
                   help=argparse.SUPPRESS)
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("status")
    take=sub.add_parser("takeover",help="pause physical timers and acquire ownership")
    take.add_argument("--owner",required=True)
    run=sub.add_parser("run",help="execute one trusted stage on demand")
    run.add_argument("--lease",required=True)
    run.add_argument("--stage",required=True,choices=sorted(STAGES))
    run.add_argument("--issue",type=int)
    release=sub.add_parser("release",help="restore prior physical timer state")
    release.add_argument("--lease",required=True)
    args=p.parse_args(argv)
    try:
        result=command(args)
        print(json.dumps(result,indent=2))
        return 0 if result.get("exit_code",0)==0 else 1
    except (OSError,ValueError,Refused,subprocess.TimeoutExpired) as exc:
        print(f"MANUAL MODE REFUSED: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
