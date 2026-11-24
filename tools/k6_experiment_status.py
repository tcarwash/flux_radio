#!/usr/bin/env python3
"""Read-only operator CLI for the Flux autonomous experiment queue.

Examples:
    flux-queue status
    flux-queue status --github
    flux-queue status --json
    flux-queue logs --lines 60

Status never claims or releases the bench, starts experiments, touches radio
or SDR, or modifies a journal. An offline status does not assert that an
independent agent is absent from the shared GitHub bench queue.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
UNIT = "flux-experiment-autopilot"
STATEDIR = Path.home() / ".local/state/flux-experiment-autopilot"
BENCH_LOCK = Path.home() / ".cache/flux/bench.lock"
QUEUE_CONFIG = ROOT / "config/k6-experiment-queue.json"
HARDWARE_CONFIG = ROOT / "config/k6-experiment-hardware.json"
STANDING_CONFIG = ROOT / "config/k6-experiment-hardware-standing.json"
LAST_RUN_MAX_AGE = timedelta(minutes=30)


def user_env():
    env = os.environ.copy()
    runtime = env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env


def run_readonly(argv, *, timeout=10):
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=timeout, check=True, env=user_env()).stdout


def show_properties(unit, names):
    output = run_readonly(["systemctl", "--user", "show", unit,
                           *[f"-p{key}" for key in names]])
    d = {}
    for ln in output.splitlines():
        if "=" in ln:
            k, v = ln.split("=", 1)
            d[k] = v
    return d


def local_stamp(text, now):
    if not text or text in ("n/a", "0", "0s"):
        return None
    # systemctl --user show uses local dates: "Tue 2026-11-24 09:35:29 PST".
    match = re.search(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d)", text)
    if not match:
        return None
    try:
        return datetime.fromisoformat(f"{match.group(1)}T{match.group(2)}").replace(
            tzinfo=now.tzinfo
        )
    except ValueError:
        return None


def scheduler_status(now):
    try:
        timer = show_properties(f"{UNIT}.timer",
                                ["ActiveState", "UnitFileState", "LastTriggerUSec"])
        service = show_properties(f"{UNIT}.service",
                                  ["ActiveState", "Result", "ExecMainStatus",
                                   "ExecMainExitTimestamp", "ExecMainStartTimestamp"])
        next_line = run_readonly(["systemctl", "--user", "list-timers",
                                  "--all", "--no-legend", "--no-pager",
                                  f"{UNIT}.timer"]).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"healthy": False, "reason": f"systemd unavailable: {exc}",
                "timer": "unknown", "next": None, "last_run": None}

    when = local_stamp(service.get("ExecMainExitTimestamp", ""), now)
    age = round((now - when).total_seconds()) if when else None
    last_tick = local_stamp(timer.get("LastTriggerUSec", ""), now)
    last_tick_age = round((now - last_tick).total_seconds()) if last_tick else None
    ticker_recent = last_tick_age is not None and -60 <= last_tick_age <= LAST_RUN_MAX_AGE.total_seconds()
    timer_active = timer.get("ActiveState") == "active"
    timer_enabled = timer.get("UnitFileState") == "enabled"
    service_ok = (service.get("Result") == "success"
                  and service.get("ExecMainStatus") == "0")
    recent = age is not None and -60 <= age <= LAST_RUN_MAX_AGE.total_seconds()
    # A running oneshot is healthy if it has not exceeded its configured
    # TimeoutStartSec (650 s). It may not yet have an exit timestamp.
    started = local_stamp(service.get("ExecMainStartTimestamp", ""), now)
    running = service.get("ActiveState") in ("activating", "active")
    running_age = (now - started).total_seconds() if started else None
    recent_running = running and running_age is not None and 0 <= running_age <= 650

    next_match = re.search(r"^(\w{3} \d{4}-\d\d-\d\d \d\d:\d\d:\d\d \S+)",
                           next_line)
    next_run = next_match.group(1) if next_match else None
    healthy = timer_active and timer_enabled and ticker_recent and (
        (service_ok and recent) or recent_running
    )
    if not timer_active or not timer_enabled:
        reason = "timer is disabled or inactive"
    elif not ticker_recent:
        reason = "timer has not triggered within 30 minutes"
    elif not (service_ok or recent_running):
        reason = "last service run failed"
    elif not recent and not recent_running:
        reason = "no successful service run within 30 minutes"
    else:
        reason = "periodic checks succeeding"
    return {
        "healthy": bool(healthy),
        "reason": reason,
        "timer": timer.get("ActiveState", "unknown"),
        "enabled": timer_enabled,
        "service": "running" if recent_running else service.get("Result", "unknown"),
        "last_run": service.get("ExecMainExitTimestamp") or None,
        "last_success_age_seconds": age if service_ok else None,
        "last_trigger": timer.get("LastTriggerUSec") or None,
        "last_trigger_age_seconds": last_tick_age,
        "next": next_run,
    }


def read_json(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return None


def read_jobs(path):
    doc = read_json(path)
    if doc is None:
        return {"missing": True, "jobs": {}}
    if (not isinstance(doc, dict) or doc.get("schema") != 1
            or not isinstance(doc.get("jobs"), dict)):
        raise ValueError(f"invalid experiment journal: {path}")
    return {"missing": False, "jobs": doc["jobs"]}


def offline_status():
    registry = read_json(QUEUE_CONFIG)
    if not isinstance(registry, dict) or not isinstance(registry.get("experiments"), dict):
        raise ValueError("offline local allowlist is missing or corrupt")
    import k6_experiment_autopilot as autopilot
    local = read_jobs(STATEDIR / "state.json")
    results = []
    for issue, reg in sorted(registry["experiments"].items(), key=lambda x: int(x[0])):
        key = autopilot.key(int(issue), reg)
        item = local["jobs"].get(key)
        results.append({"issue": int(issue),
                        "state": item.get("state", "PENDING") if item else "PENDING"})
    return {"registered": len(results),
            "passed": sum(x["state"] in ("PASS", "IMPORTED_PASS") for x in results),
            "pending": sum(x["state"] == "PENDING" for x in results),
            "issues": results,
            "journal_missing": local["missing"]}


def hardware_status():
    cfg = read_json(HARDWARE_CONFIG)
    standing = read_json(STANDING_CONFIG)
    if not isinstance(cfg, dict) or not isinstance(cfg.get("manifests"), dict):
        raise ValueError("hardware manifest allowlist missing or corrupt")
    if not isinstance(standing, dict) or not isinstance(standing.get("approved"), dict):
        raise ValueError("hardware standing permissions missing or corrupt")
    local = read_jobs(STATEDIR / "hardware.json")
    issues = []
    for key, grant in sorted(standing["approved"].items(), key=lambda x: int(x[0])):
        manifest = cfg["manifests"].get(key, {})
        sha = grant.get("manifest_sha256")
        historical_key = f"issue-{key}:{sha}"
        previous = local["jobs"].get(historical_key)
        if sha != manifest.get("sha256"):
            state = "UNENROLLED_OR_DRIFTED"
        else:
            state = previous.get("state", "AWAITING_REVIEW") if previous else "AWAITING_REVIEW"
        issues.append({"issue": int(key), "state": state,
                       "session_id": previous.get("session_id") if previous else None})
    history = local["jobs"]
    return {
        "active": sum(v.get("state") == "STARTED_NO_AUTO_RETRY"
                      for v in history.values()),
        "blocked_total": sum(v.get("state") == "BLOCKED_NO_AUTO_RETRY"
                             for v in history.values()),
        "historical_attempts": len(history),
        "approved_in_policy": len(standing["approved"]),
        "current": issues,
        "journal_missing": local["missing"],
    }


def review_status():
    review = read_jobs(STATEDIR / "review.json")
    postmortem = read_jobs(STATEDIR / "postmortem.json")
    summary = {}
    for name, doc in (("validation", review), ("postmortem", postmortem)):
        counters = {}
        for job in doc["jobs"].values():
            state = job.get("state", "UNKNOWN")
            counters[state] = counters.get(state, 0) + 1
        summary[name] = {"counts": counters, "journal_missing": doc["missing"]}
    return summary


def bench_status(check_github=False):
    # Never acquire the canonical radio serial lock here, nor touch devices.
    # flock probing merely checks whether another cooperative process has
    # acquired the entire-bench lock.
    if not BENCH_LOCK.exists():
        locked = None
    else:
        try:
            with BENCH_LOCK.open("rb") as f:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    locked = True
                else:
                    locked = False
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            return {"local": "unknown", "github": "unchecked",
                    "detail": f"bench lock unreadable: {exc}"}

    data = {"local": "held" if locked else "free" if locked is False else "unknown",
            "github": "unchecked"}
    if check_github:
        # This is read-only GitHub coordination, not reservation. Other
        # uncooperative agents may still access the physical equipment.
        try:
            import k6_experiment_hardware_autopilot as hw_auto
            import k6_experiment_hardware as hardware
            cfg = hardware.load_config(HARDWARE_CONFIG)
            hw_auto.require_unclaimed_bench(cfg)
            data["github"] = "released"
        except Exception as exc:
            if "unreleased whole-bench claim" in str(exc):
                data["github"] = "held"
            else:
                data["github"] = "unknown"
                data["detail"] = f"GitHub coordination check failed: {exc}"
    return data


def intake_status():
    state=read_jobs(STATEDIR / "intake.json")
    snapshot=read_json(STATEDIR / "intake-snapshot.json")
    counts={}
    for record in state["jobs"].values():
        phase=record.get("state","UNKNOWN")
        counts[phase]=counts.get(phase,0)+1
    if snapshot is None:
        return {"journal_missing":state["missing"],"snapshot_missing":True,
                "counts":counts,"issues_seen":None,"open_without_intake":None,
                "last_scan_utc":None}
    if not isinstance(snapshot,dict) or snapshot.get("mode")!="READ_ONLY_PREPARATION_INTAKE":
        raise ValueError("invalid autonomous intake snapshot")
    if snapshot.get("hardware_approvals_created")!=0:
        raise ValueError("intake unexpectedly claims hardware approval")
    return {"journal_missing":state["missing"],"snapshot_missing":False,
            "counts":counts,"issues_seen":snapshot.get("issues_seen"),
            "open_without_intake":snapshot.get("open_without_intake"),
            "last_scan_utc":snapshot.get("last_scan_utc"),
            "daily_limit":snapshot.get("daily_limit"),
            "submitted_today":snapshot.get("submitted_today")}


def status(now=None, check_github=False):
    now = now or datetime.now().astimezone()
    result = {"timestamp": now.isoformat(), "health": "UNKNOWN",
              "scheduler": scheduler_status(now),
              "bench": bench_status(check_github=check_github),
              "offline": None, "hardware": None, "reviews": None,
              "intake": None, "alerts": []}
    try:
        result["offline"] = offline_status()
        result["hardware"] = hardware_status()
        result["reviews"] = review_status()
        result["intake"] = intake_status()
    except (OSError, ValueError, KeyError) as exc:
        result["alerts"].append(f"local state unreadable: {exc}")
    if not result["scheduler"]["healthy"]:
        result["alerts"].append(result["scheduler"]["reason"])
    if result["bench"]["local"] == "held":
        result["alerts"].append("local bench lock currently held")
    if result["bench"]["github"] == "held":
        result["alerts"].append("GitHub coordination has an unreleased bench claim")
    if result["bench"]["github"] == "unknown":
        result["alerts"].append("could not verify remote bench claim")
    state_missing = False
    for label in ("offline", "hardware"):
        section = result.get(label)
        if section and section.get("journal_missing"):
            result["alerts"].append(f"{label} journal missing; attempt history cannot be trusted")
            state_missing = True
    if result["reviews"]:
        if result["reviews"]["validation"]["journal_missing"] and result["hardware"] and result["hardware"]["approved_in_policy"]:
            result["alerts"].append("validation journal missing for enrolled hardware")
            state_missing = True
        if result["reviews"]["postmortem"]["journal_missing"] and result["hardware"] and result["hardware"]["historical_attempts"]:
            result["alerts"].append("postmortem journal missing for hardware attempts")
            state_missing = True
    intake=result.get("intake")
    if intake:
        if intake.get("snapshot_missing"):
            result["alerts"].append("intake scan has not run; GitHub backlog is unknown")
        elif intake.get("open_without_intake"):
            result["alerts"].append(
                f"{intake['open_without_intake']} GitHub RF issues need first intake assessment")
        blocked=sum(intake["counts"].get(x,0) for x in (
            "INFRA_FAILURE","UNPARSEABLE_REVIEW","SUBMISSION_STARTED"))
        if blocked:
            result["alerts"].append(f"{blocked} intake assessments require intervention")
    if result["offline"] and result["offline"]["pending"]:
        result["alerts"].append(f"{result['offline']['pending']} offline jobs pending")
    manifest_drift = False
    if result["hardware"]:
        for item in result["hardware"]["current"]:
            if item["state"] == "BLOCKED_NO_AUTO_RETRY":
                result["alerts"].append(
                    f"experiment #{item['issue']}: blocked, no automatic retry")
            elif item["state"] == "UNENROLLED_OR_DRIFTED":
                result["alerts"].append(f"experiment #{item['issue']}: manifest approval drift")
                manifest_drift = True
    if result["reviews"]:
        for kind in ("validation", "postmortem"):
            counts = result["reviews"][kind]["counts"]
            if counts.get("DELEGATED", 0) or counts.get("REVIEW_SUBMITTED", 0):
                result["alerts"].append(f"{kind} review still in progress")
    # Blocked *experiments* are normal for a healthy runner with fail-closed
    # semantics. They require attention but do not imply timer failure.
    result["health"] = ("HEALTHY" if result["scheduler"]["healthy"]
                        and result["offline"] is not None
                        and result["hardware"] is not None
                        and result["reviews"] is not None
                        and result["intake"] is not None
                        and not result["intake"].get("snapshot_missing")
                        and not state_missing and not manifest_drift
                        and (not check_github or result["bench"]["github"] != "unknown")
                        else "DEGRADED")
    return result


def display(data):
    print(f"Flux queue: {data['health']}")
    scheduler = data["scheduler"]
    print(f"  Scheduler   {scheduler['timer']}, "
          f"{'enabled' if scheduler.get('enabled') else 'disabled'}")
    print(f"  Last check  {scheduler.get('last_run') or 'unknown'}"
          f" · {scheduler.get('service', 'unknown')}")
    print(f"  Next check  {scheduler.get('next') or 'not reported'}")
    bench = data["bench"]
    print(f"  Bench       local {bench['local']}; "
          f"GitHub {bench['github']}")
    intake=data.get("intake")
    if intake:
        print(f"  Intake      {intake.get('issues_seen') or 0} GitHub RF issues"
              f" · {intake.get('open_without_intake') or 0} not yet reviewed")
        counts=intake.get("counts",{})
        print(f"              {counts.get('TRIAGED',0)} triaged"
              f" · {counts.get('REVIEW_SUBMITTED',0)} preparing"
              f" · {counts.get('PUBLISH_PENDING',0)} awaiting publication")
    offline = data["offline"]
    if offline:
        print(f"  Offline     {offline['passed']}/{offline['registered']} complete"
              f" · {offline['pending']} pending")
    hardware = data["hardware"]
    if hardware:
        print(f"  Hardware    {hardware['active']} active"
              f" · {hardware['blocked_total']} blocked attempts"
              f" · {hardware['approved_in_policy']} registered")
        for issue in hardware["current"]:
            print(f"              #{issue['issue']}: {issue['state']}")
    reviews = data["reviews"]
    if reviews:
        for name, label in (("validation", "Validation"), ("postmortem", "Postmortem")):
            counts = reviews[name]["counts"]
            print(f"  {label:<11} " +
                  (", ".join(f"{state.lower()} {count}" for state, count in sorted(counts.items()))
                   or "no records"))
    if data["alerts"]:
        print("\n  Attention:")
        for message in data["alerts"]:
            print(f"    - {message}")
    if bench["github"] == "unchecked":
        print("\n  Local-only bench check; add --github to verify GitHub leases.")
    print("  Logs: flux-queue logs")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("status", help="read-only controller health report")
    p.add_argument("--json", action="store_true", help="machine-readable report")
    p.add_argument("--github", action="store_true",
                   help="also inspect live trusted GitHub bench coordination")
    p.add_argument("--strict", action="store_true",
                   help="exit nonzero if runner is unhealthy (blocked jobs alone do not count)")
    log = sub.add_parser("logs", help="show the systemd service log")
    log.add_argument("-n", "--lines", type=int, default=60)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command is None:
        args = parser.parse_args(["status"])
    if args.command == "logs":
        if not 1 <= args.lines <= 1000:
            parser.error("log lines must be 1..1000")
        try:
            out = run_readonly(["journalctl", "--user", "-u", f"{UNIT}.service",
                                "--no-pager", "-n", str(args.lines)])
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"Cannot read service logs: {exc}", file=sys.stderr)
            return 2
        print(out, end="")
        return 0

    report = status(check_github=args.github)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        display(report)
    return 1 if args.strict and report["health"] != "HEALTHY" else 0


if __name__ == "__main__":
    raise SystemExit(main())
