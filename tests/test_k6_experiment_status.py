"""Status CLI is observability-only; tests never use real radio or GitHub."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location(
    "k6_experiment_status", ROOT / "tools/k6_experiment_status.py")
q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)
NOW = datetime(2026, 11, 24, 9, 40, 0, tzinfo=timezone(timedelta(hours=-8)))


def status_props(last="Tue 2026-11-24 09:35:00 PST", result="success", timer="active"):
    def props(unit, keys):
        if unit.endswith(".timer"):
            return {"ActiveState": timer, "UnitFileState": "enabled",
                    "LastTriggerUSec": "Tue 2026-11-24 09:26:03 PST"}
        return {"ActiveState": "inactive", "Result": result,
                "ExecMainStatus": "0" if result == "success" else "1",
                "ExecMainExitTimestamp": last}
    return props


def test_scheduler_healthy_when_recent_and_idle(monkeypatch):
    monkeypatch.setattr(q, "show_properties", status_props())
    monkeypatch.setattr(q, "run_readonly", lambda args: (
        "Tue 2026-11-24 09:45:43 PST 5min Tue 2026-11-24 09:26:03 PST "
        "flux-experiment-autopilot.timer flux-experiment-autopilot.service"))
    x = q.scheduler_status(NOW)
    assert x["healthy"] is True
    assert x["last_success_age_seconds"] == 300
    assert "09:45:43" in x["next"]


@pytest.mark.parametrize("last,result,timer", [
    ("Tue 2026-11-24 08:30:00 PST", "success", "active"),
    ("Tue 2026-11-24 09:35:00 PST", "exit-code", "active"),
    ("Tue 2026-11-24 09:35:00 PST", "success", "inactive"),
])
def test_bad_timer_or_old_run_unhealthy(monkeypatch, last, result, timer):
    monkeypatch.setattr(q, "show_properties",
                        status_props(last=last, result=result, timer=timer))
    monkeypatch.setattr(q, "run_readonly", lambda args: "")
    assert q.scheduler_status(NOW)["healthy"] is False


def test_missing_journal_is_observable(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "STATEDIR", tmp_path)
    assert q.read_jobs(tmp_path / "not-here.json") == {"missing": True, "jobs": {}}
    (tmp_path / "state.json").write_text('{"schema":1,"jobs":{}}')
    assert q.read_jobs(tmp_path / "state.json")["missing"] is False
    (tmp_path / "state.json").write_text('{"schema":2,"jobs":{}}')
    with pytest.raises(ValueError, match="invalid experiment journal"):
        q.read_jobs(tmp_path / "state.json")


def test_offline_counts_only_local_registry_frozen_hash(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "STATEDIR", tmp_path)
    monkeypatch.setattr(q, "QUEUE_CONFIG", tmp_path / "registry.json")
    reg = {"experiments": {"9": {"kind":"OFFLINE","argv":["/usr/bin/true"]},
                           "10": {"kind":"OFFLINE","argv":["/usr/bin/false"]}}}
    q.QUEUE_CONFIG.write_text(json.dumps(reg))
    import k6_experiment_autopilot as auto
    doc = {"schema":1, "jobs":{
        auto.key(9, reg["experiments"]["9"]): {"state":"IMPORTED_PASS"}
    }}
    (tmp_path / "state.json").write_text(json.dumps(doc))
    x = q.offline_status()
    assert (x["registered"], x["passed"], x["pending"]) == (2, 1, 1)


def test_hardware_distinguishes_block_from_controller_health(monkeypatch, tmp_path):
    monkeypatch.setattr(q, "STATEDIR", tmp_path)
    monkeypatch.setattr(q, "HARDWARE_CONFIG", tmp_path / "cfg.json")
    monkeypatch.setattr(q, "STANDING_CONFIG", tmp_path / "standing.json")
    q.HARDWARE_CONFIG.write_text(json.dumps({"manifests":{
        "9": {"sha256":"a"*64}}}))
    q.STANDING_CONFIG.write_text(json.dumps({"approved":{
        "9": {"manifest_sha256":"a"*64}}}))
    (tmp_path / "hardware.json").write_text(json.dumps({"schema":1,"jobs":{
        "issue-9:"+("a"*64): {"state":"BLOCKED_NO_AUTO_RETRY"},
        "issue-9:"+("b"*64): {"state":"BLOCKED_NO_AUTO_RETRY"}
    }}))
    h = q.hardware_status()
    assert h["blocked_total"] == 2
    assert h["current"] == [{"issue":9,"state":"BLOCKED_NO_AUTO_RETRY",
                              "session_id":None}]


def test_bench_lock_probe_read_only(monkeypatch,tmp_path):
    import fcntl
    p = tmp_path / "bench.lock"
    p.touch()
    monkeypatch.setattr(q, "BENCH_LOCK", p)
    assert q.bench_status()["local"] == "free"
    with p.open("a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert q.bench_status()["local"] == "held"
    assert q.bench_status()["github"] == "unchecked"


def test_status_does_not_touch_device_and_blocked_is_not_timer_failure(monkeypatch):
    monkeypatch.setattr(q,"scheduler_status",lambda now:
                        {"healthy":True,"reason":"ok","timer":"active",
                         "enabled":True,"service":"success","last_run":"now",
                         "next":"soon"})
    monkeypatch.setattr(q,"bench_status",lambda check_github=False:
                        {"local":"free","github":"unchecked"})
    monkeypatch.setattr(q,"offline_status",lambda:
                        {"registered":2,"passed":2,"pending":0,"issues":[],"journal_missing":False})
    monkeypatch.setattr(q,"hardware_status",lambda:
                        {"active":0,"blocked_total":2,"current":[{
                          "issue":9,"state":"BLOCKED_NO_AUTO_RETRY"}],
                         "approved_in_policy":1,"journal_missing":False})
    monkeypatch.setattr(q,"review_status",lambda:
                        {"validation":{"counts":{"APPROVED":1},"journal_missing":False},
                         "postmortem":{"counts":{"COMPLETE":2},"journal_missing":False}})
    monkeypatch.setattr(q,"intake_status",lambda:{"journal_missing":False,
        "snapshot_missing":False,"counts":{"TRIAGED":2},"issues_seen":24,
        "open_without_intake":22,"last_scan_utc":"2026-11-24T17:40:00Z"})
    report=q.status(now=NOW)
    assert report["health"]=="HEALTHY"
    assert report["intake"]["open_without_intake"]==22
    assert any("22 GitHub RF issues" in x for x in report["alerts"])
    assert "experiment #9: blocked, no automatic retry" in report["alerts"]


def test_json_cli_exit_and_log_bounds(monkeypatch,capsys):
    sample={"health":"HEALTHY","scheduler":{},"bench":{}}
    monkeypatch.setattr(q,"status",lambda check_github=False:sample)
    assert q.main(["status","--json","--strict"])==0
    assert json.loads(capsys.readouterr().out)["health"]=="HEALTHY"
    monkeypatch.setattr(q,"status",lambda check_github=False:{"health":"DEGRADED"})
    assert q.main(["status","--json","--strict"])==1
    with pytest.raises(SystemExit):
        q.main(["logs","--lines","1001"])

def test_stale_timer_detected_even_if_manual_service_recent(monkeypatch):
    def props(unit, keys):
        val=status_props()(unit,keys)
        if unit.endswith(".timer"):
            val["LastTriggerUSec"]="Tue 2026-11-24 08:00:00 PST"
        return val
    monkeypatch.setattr(q,"show_properties",props)
    monkeypatch.setattr(q,"run_readonly",lambda args:"")
    result=q.scheduler_status(NOW)
    assert result["healthy"] is False
    assert "timer has not triggered" in result["reason"]


def test_missing_attempt_journal_reports_degraded(monkeypatch):
    monkeypatch.setattr(q,"scheduler_status",lambda now:
                        {"healthy":True,"reason":"ok","timer":"active"})
    monkeypatch.setattr(q,"bench_status",lambda check_github=False:
                        {"local":"free","github":"unchecked"})
    monkeypatch.setattr(q,"offline_status",lambda:
                        {"registered":1,"passed":1,"pending":0,
                         "journal_missing":False})
    monkeypatch.setattr(q,"hardware_status",lambda:
                        {"current":[],"approved_in_policy":1,"historical_attempts":0,
                         "journal_missing":True})
    monkeypatch.setattr(q,"review_status",lambda:
                        {"validation":{"counts":{},"journal_missing":False},
                         "postmortem":{"counts":{},"journal_missing":False}})
    x=q.status(now=NOW)
    assert x["health"]=="DEGRADED"
    assert any("journal missing" in line for line in x["alerts"])


def test_intake_backlog_and_missing_snapshot_visible(monkeypatch,tmp_path):
    monkeypatch.setattr(q,"STATEDIR",tmp_path)
    assert q.intake_status()["snapshot_missing"]
    (tmp_path/"intake.json").write_text(json.dumps({"schema":1,"jobs":{
        "issue-30:a":{"state":"REVIEW_SUBMITTED"},
        "issue-21:b":{"state":"TRIAGED"}}}))
    (tmp_path/"intake-snapshot.json").write_text(json.dumps({
        "mode":"READ_ONLY_PREPARATION_INTAKE","issues_seen":24,
        "open_without_intake":22,"hardware_approvals_created":0,
        "last_scan_utc":"2026-11-24T22:50:00Z", "daily_limit":6,
        "submitted_today":2}))
    x=q.intake_status()
    assert x["issues_seen"]==24 and x["open_without_intake"]==22
    assert x["counts"]=={"REVIEW_SUBMITTED":1,"TRIAGED":1}
    with pytest.raises(ValueError, match="unexpectedly claims"):
        (tmp_path/"intake-snapshot.json").write_text(json.dumps({
            "mode":"READ_ONLY_PREPARATION_INTAKE","hardware_approvals_created":1}))
        q.intake_status()
