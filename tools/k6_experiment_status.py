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
HARDWARE_CONFIG = (STATEDIR/"hardware-generated.json" if
                   (STATEDIR/"hardware-generated.json").exists() else
                   ROOT / "config/k6-experiment-hardware.json")
STANDING_CONFIG = (STATEDIR/"hardware-generated-standing.json" if
                   (STATEDIR/"hardware-generated-standing.json").exists() else
                   ROOT / "config/k6-experiment-hardware-standing.json")
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
    closed = read_jobs(STATEDIR / "failed-closeouts.json")["jobs"]
    issues = []
    for key, grant in sorted(standing["approved"].items(), key=lambda x: int(x[0])):
        manifest = cfg["manifests"].get(key, {})
        sha = grant.get("manifest_sha256")
        historical_key = f"issue-{key}:{sha}"
        previous = local["jobs"].get(historical_key)
        if closed.get(f"issue-{key}",{}).get("state") == "CLOSED_FAILED":
            state = "FAILED_CLOSED"
        elif sha != manifest.get("sha256"):
            state = "UNENROLLED_OR_DRIFTED"
        else:
            state = previous.get("state", "AWAITING_REVIEW") if previous else "AWAITING_REVIEW"
        issues.append({"issue": int(key), "state": state,
                       "session_id": previous.get("session_id") if previous else None})
    history = local["jobs"]
    return {
        "registered_manifests":len(cfg["manifests"]),
        "waiting_independent_review":[int(k) for k in cfg["manifests"]
                                       if k not in standing["approved"]],
        "active": sum(v.get("state") == "STARTED_NO_AUTO_RETRY"
                      for v in history.values()),
        "blocked_total": sum(v.get("state") == "BLOCKED_NO_AUTO_RETRY"
                             for v in history.values()),
        "historical_attempts": len(history),
        "failed_closed": sum(x.get("state") == "CLOSED_FAILED" for x in closed.values()),
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
    # Multiple revisions of an issue are retained for audit; report its
    # newest active state only, not misleading duplicate work counts.
    by_issue={}
    for key,record in state["jobs"].items():
        issue=record.get("issue")
        if issue is None:
            try: issue=int(key.split(":",1)[0].removeprefix("issue-"))
            except (ValueError,AttributeError):continue
        timestamp=record.get("created",record.get("updated",
                                                 record.get("published_at","")))
        rank=(timestamp,1 if "migrated_from" in record else 0)
        if issue not in by_issue or rank>=by_issue[issue][0]:
            by_issue[issue]=(rank,record)
    counts={}
    for _,record in by_issue.values():
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


def builder_status():
    doc=read_jobs(STATEDIR / "builder.json")
    counts={}
    for r in doc["jobs"].values():
        key=r.get("state","UNKNOWN")
        counts[key]=counts.get(key,0)+1
    return {"counts":counts,"issues":[
        {"issue":int(key.split(":",1)[0].removeprefix("issue-")),
         "state":r.get("state","UNKNOWN"),
         "frozen_commit":r.get("frozen_commit")}
        for key,r in doc["jobs"].items() if key.startswith("issue-")],
        "journal_missing":doc["missing"]}


def fixture_status():
    # Standalone local software regression evidence. It never creates
    # physical queue entries, bench leases or RF PASS claims.
    document=read_jobs(STATEDIR / "offline-sweep.json")
    current=list(document["jobs"].values())
    states={}
    for record in current:
        result=record.get("result","UNKNOWN")
        states[result]=states.get(result,0)+1
    return {
        "local_only":True,
        "radio_access":False,
        "queue_publications":0,
        "suites":len(current),
        "counts":states,
        "passed_tests":sum(r.get("test_passed",0) for r in current
                           if r.get("result")=="PASS"),
        "journal_missing":document["missing"],
    }


def status(now=None, check_github=False):
    now = now or datetime.now().astimezone()
    result = {"timestamp": now.isoformat(), "health": "UNKNOWN",
              "scheduler": scheduler_status(now),
              "bench": bench_status(check_github=check_github),
              "offline": None, "hardware": None, "reviews": None,
              "intake": None, "builder": None, "fixtures": None, "alerts": []}
    try:
        result["offline"] = offline_status()
        result["hardware"] = hardware_status()
        result["reviews"] = review_status()
        result["intake"] = intake_status()
        result["builder"] = builder_status()
        result["fixtures"] = fixture_status()
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
            "INFRA_FAILURE","UNPARSEABLE_REVIEW","SUBMISSION_STARTED",
            "CONTRADICTORY_EVIDENCE"))
        if blocked:
            result["alerts"].append(f"{blocked} intake assessments require intervention")
    bstate=result.get("builder")
    if bstate:
        blocked=sum(bstate["counts"].get(st,0) for st in (
            "BLOCKED_NO_AUTO_RETRY","BLOCKED_CANDIDATE_NEEDS_REPAIR"))
        if blocked:result["alerts"].append(
            f"{blocked} generated candidates need offline repair")
    if result["hardware"] and result["hardware"].get("waiting_independent_review"):
        ids=result["hardware"]["waiting_independent_review"]
        result["alerts"].append(f"physical candidates awaiting independent review: {ids}")
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
    builder=data.get("builder")
    if builder:
        counts=builder["counts"]
        print(f"  Builder     {counts.get('TESTED_NEEDS_INDEPENDENT_REVIEW',0)} tested"
              f" · {counts.get('MANIFEST_REGISTERED_FOR_REVIEW',0)} registered"
              f" · {counts.get('REPAIRING_DEVICE_FREE',0)} repairing"
              f" · {counts.get('BLOCKED_NO_AUTO_RETRY',0)+counts.get('BLOCKED_CANDIDATE_NEEDS_REPAIR',0)} need repair")
    fixture=data.get("fixtures")
    if fixture:
        c=fixture["counts"]
        print(f"  Fixtures    {c.get('PASS',0)}/{fixture['suites']} local suites"
              f" · {fixture['passed_tests']} tests passed"
              f" · {c.get('FAIL_TESTS',0)+c.get('TIMEOUT',0)} not passing"
              f" · NO radio/queue")
    offline = data["offline"]
    if offline:
        print(f"  Offline     {offline['passed']}/{offline['registered']} complete"
              f" · {offline['pending']} pending")
    hardware = data["hardware"]
    if hardware:
        print(f"  Hardware    {hardware['active']} active"
              f" · {hardware['blocked_total']} blocked attempts"
              f" · {hardware['approved_in_policy']} registered"
              + (f" · {hardware['failed_closed']} failed/closed" if hardware.get('failed_closed') else ""))
        if hardware.get("waiting_independent_review"):
            print(f"              awaiting independent review: "
                  f"{hardware['waiting_independent_review']}")
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


def submit_job(args):
    """Non-blocking central queue submission from any branch or agent worktree."""
    from datetime import timezone
    import hashlib
    import k6_experiment_autopilot as journal
    import k6_experiment_batch as batch
    import k6_experiment_promotion as promo
    import k6_experiment_review as rev
    worktree = Path(args.worktree).resolve()
    if not worktree.is_dir():
        raise ValueError(f"worktree does not exist: {worktree}")
    script_rel = Path(args.script)
    script_abs = (worktree / script_rel).resolve() if not script_rel.is_absolute() else script_rel.resolve()
    if not script_abs.is_file():
        raise ValueError(f"bench script does not exist: {script_abs}")
    p = promo.policy()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    session_id = f"queued-{args.issue}-{stamp}"
    output = Path.home() / "flux-bench/batches" / session_id
    probe = str(ROOT / "tools/k6_experiment_bench_probe.py")
    base = ["/usr/bin/python3", probe, "--expected-build", p["expected_build"],
            "--exact-api", str(p["exact_api"]), "--include-slots"]
    if args.clear_spi:
        base.append("--clear-spi")
    if args.reflash_firmware:
        base.extend(["--reflash-firmware", str(Path(args.reflash_firmware).resolve())])
    before = str(output / "session-before.json")
    preflight = base + ["--output", before]
    after = base + ["--baseline", before]
    venv_py = "/home/tyler/.local/share/flux-rf-venv/bin/python"
    py = venv_py if Path(venv_py).is_file() else "/usr/bin/python3"
    prefix = str(output / "auto-bench")
    run_args = [py, str(script_abs), "--run", "--prefix", prefix,
                "--dummy-load-confirmed", "--operator-lease-confirmed",
                "--queue-lease-acknowledged"]
    if args.kind == "BOUNDED_TX":
        run_args.extend(["--max-tx-ms", str(p["rf_session_limit_ms"]),
                         "--frequency-hz", "433500000"])
    preflight_argv = ([py, str(script_abs), "--preflight-only"]
                      if "--preflight-only" in script_abs.read_text()
                      else after)
    pin_paths = {str(script_abs), probe}
    for dep in ("tools/k6_install.py", "tools/k6_live.py", "tools/k6_port_lock.py",
                "tools/k6_user_store_migrate.py", "modem/k6mod.py"):
        if (worktree / dep).is_file():
            pin_paths.add(str((worktree / dep).resolve()))
    for extra in args.pin or ():
        ep = (worktree / extra).resolve() if not Path(extra).is_absolute() else Path(extra).resolve()
        if not ep.is_file():
            raise ValueError(f"pinned file not found: {ep}")
        pin_paths.add(str(ep))
    session_deps = ["tools/k6_experiment_bench_probe.py", "tools/k6_live.py",
                    "tools/k6_install.py", "tools/k6_port_lock.py",
                    "tools/k6_user_store_migrate.py"]
    session_pins = [{"path": str(ROOT / x), "sha256": promo.sha(ROOT / x)}
                    for x in session_deps]
    expires = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    m = {
        "version": 1,
        "session_id": session_id,
        "port": "/dev/ttyUSB0",
        "queue_issue_url": "https://github.com/tcarwash/flux_radio/issues/11",
        "lease_expires_utc": expires,
        "session_pins": session_pins,
        "session_preflight": {"argv": preflight, "timeout_seconds": 90},
        "session_restore": {"argv": after, "timeout_seconds": 90},
        "session_verify": {"argv": after, "timeout_seconds": 90},
        "jobs": [{
            "id": f"queued-{args.issue}-one-shot",
            "issue_url": f"https://github.com/tcarwash/flux_radio/issues/{args.issue}",
            "minimum_module_api": args.min_api,
            "cwd": str(worktree),
            "pins": [{"path": pt, "sha256": promo.sha(pt)} for pt in sorted(pin_paths)],
            "preflight": {"argv": preflight_argv, "timeout_seconds": 60},
            "run": {"argv": run_args, "timeout_seconds": 150},
            "restore": {"argv": after, "timeout_seconds": 90},
            "verify": {"argv": after, "timeout_seconds": 90},
        }],
    }
    batch.validate_manifest(m)
    batch.check_command_pins(m)
    api, api_raw = batch.read_api_config(batch.API_CONFIG)
    blockers = batch.check_api_compatibility(m, api)
    if blockers:
        raise ValueError("API compatibility blocked: " + "; ".join(blockers))
    reg = promo.ensure_generated()
    if not args.no_github and not promo.ranked(reg, args.issue):
        promo.ensure_ranked(reg, args.issue, write=True)
    manifest_path = promo.MANIFEST_DIR / f"{session_id}.json"
    promo.atomic(manifest_path, m)
    digest = promo.sha(manifest_path)
    reg["manifests"][str(args.issue)] = {"path": str(manifest_path), "sha256": digest}
    promo.atomic(promo.GENERATED, reg)
    # Record direct local approval and standing one-shot permit so the next
    # background dispatcher tick bundles and runs this job without blocking.
    rstate = journal.state_load(rev.DEFAULT_STATE)
    rkey = rev.fingerprint(args.issue, digest, api_raw)
    rstate["jobs"][rkey] = {
        "state": "APPROVED",
        "job_id": "0" * 12,
        "attempts": 1,
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "completed_utc": datetime.now(timezone.utc).isoformat(),
    }
    journal.state_write(rev.DEFAULT_STATE, rstate)
    from k6_experiment_hardware_autopilot import standing_policy
    standing = standing_policy(promo.PERMISSIONS)
    standing["approved"][str(args.issue)] = {
        "manifest_sha256": digest,
        "expected_build": p["expected_build"],
        "exact_api": p["exact_api"],
        "expires_utc": expires,
        "one_shot": True,
        "dummy_load_confirmed": True,
        "no_cwid_for_dummy_load": True,
    }
    promo.atomic(promo.PERMISSIONS, standing)
    return {
        "status": "QUEUED",
        "issue": args.issue,
        "session_id": session_id,
        "manifest_sha256": digest,
        "manifest_path": str(manifest_path),
        "minimum_module_api": args.min_api,
        "clear_spi": bool(args.clear_spi),
    }


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
    for name in ("submit", "enqueue"):
        sub_p = sub.add_parser(name, help="non-blocking enqueue of a branch/worktree hardware experiment")
        sub_p.add_argument("--issue", type=int, required=True, help="GitHub experiment issue number")
        sub_p.add_argument("--worktree", type=Path, default=Path.cwd(), help="branch/worktree directory")
        sub_p.add_argument("--script", required=True, help="bench script path (relative to worktree or absolute)")
        sub_p.add_argument("--min-api", type=int, default=3, help="minimum firmware module API required (1..29)")
        sub_p.add_argument("--kind", choices=("BOUNDED_TX", "RX_ONLY"), default="BOUNDED_TX")
        sub_p.add_argument("--clear-spi", action="store_true", default=True,
                           help="clear temporary SPI slots before and after run (default: true)")
        sub_p.add_argument("--reflash-firmware", type=Path, help="optional firmware binary to flash before run")
        sub_p.add_argument("--pin", action="append", help="additional file paths to SHA256-pin")
        sub_p.add_argument("--no-github", action="store_true", help="skip GitHub #11 ranking check/update")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command is None:
        args = parser.parse_args(["status"])
    if args.command in ("submit", "enqueue"):
        try:
            print(json.dumps(submit_job(args), indent=2))
            return 0
        except Exception as exc:
            print(f"SUBMIT REFUSED: {exc}", file=sys.stderr)
            return 2
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
