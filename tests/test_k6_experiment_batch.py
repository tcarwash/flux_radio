"""Hardware-free tests for Flux's fail-closed multi-experiment bench coordinator."""
from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("k6_experiment_batch", ROOT / "tools/k6_experiment_batch.py")
batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(batch)
ACK = "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-1234567890"
QUEUE = "https://github.com/tcarwash/flux_radio/issues/11"


def command(script: Path, trace: Path, event: str, exit_status: str = "PASS", timeout: int = 5):
    return {
        "argv": [sys.executable, str(script), str(trace), event, exit_status],
        "timeout_seconds": timeout,
    }


def fresh(tmp_path, *, jobs: int = 2, expire: bool = False):
    """All child commands only record text files; never a real device."""
    script = tmp_path / "fake_harness.py"
    script.write_text(
        "import sys,time,pathlib\n"
        "p=pathlib.Path(sys.argv[1]); phase=sys.argv[2]; mode=sys.argv[3]\n"
        "with p.open('a') as f: f.write(phase+'\\n')\n"
        "if mode=='TIMEOUT': time.sleep(3)\n"
        "if mode=='FAIL': sys.exit(13)\n"
        "if 'verify' in phase:\n"
        "  earlier=p.read_text()\n"
        "  match=phase.replace('verify','restore')\n"
        "  if match not in earlier: sys.exit(29)\n"
    )
    hashpin = {"path": str(script), "sha256": hashlib.sha256(script.read_bytes()).hexdigest()}
    trace = tmp_path / "trace.txt"
    lease = "2001-01-01T00:00:00Z" if expire else "2099-01-01T00:00:00Z"
    m = {
        "version": 1, "session_id": "fake-session",
        "port": "/dev/fake-flux-k6", "queue_issue_url": QUEUE,
        "lease_expires_utc": lease,
        "session_pins": [hashpin],
        "session_preflight": command(script, trace, "session_preflight"),
        "session_restore": command(script, trace, "session_restore"),
        "session_verify": command(script, trace, "session_verify"),
        "jobs": [],
    }
    for i in range(jobs):
        n = f"j{i}"
        m["jobs"].append({
            "id": n, "issue_url": "https://github.com/tcarwash/flux_radio/issues/29",
            "cwd": str(tmp_path), "pins": [hashpin],
            **{phase: command(script, trace, f"{n}-{phase}") for phase in batch.STEPS},
        })
    return m, trace, script


def invoke(m, tmp_path, *, digest=None, ack=ACK, operator="operator",
           dummy=True, output_root=None):
    b = json.dumps(m, sort_keys=True).encode()
    sha = hashlib.sha256(b).hexdigest()
    return batch.run_session(
        m, b, sha, output_root=output_root or (tmp_path / "results"),
        lock_root=tmp_path / "locks", approved_digest=sha if digest is None else digest,
        ack_url=ack, operator=operator, dummy_load_confirmed=dummy
    )


def test_dry_run_requires_no_hardware_or_files(tmp_path, capsys):
    m, trace, _ = fresh(tmp_path)
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(m))
    assert batch.main([str(path)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "DRY_RUN_NO_COMMANDS"
    assert len(output["jobs"]) == 2
    assert len(output["manifest_sha256"]) == 64
    assert not trace.exists()


def test_two_jobs_sequential_with_full_checks(tmp_path):
    m, trace, _ = fresh(tmp_path)
    result = invoke(m, tmp_path)
    assert result["result"] == "PASS", result
    assert [j["result"] for j in result["jobs"]] == ["PASS", "PASS"]
    assert trace.read_text().splitlines() == [
        "session_preflight", "j0-preflight", "j0-run", "j0-restore",
        "j0-verify", "j1-preflight", "j1-run", "j1-restore",
        "j1-verify", "session_restore", "session_verify",
    ]
    saved = json.loads((tmp_path / "results/fake-session/result.json").read_text())
    assert saved["result"] == "PASS"
    assert (tmp_path / "results/fake-session/manifest.json").is_file()
    assert (tmp_path / "results/fake-session/job01-j0-run.log").is_file()
    assert (tmp_path / "results/fake-session").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "results/fake-session/result.json").stat().st_mode & 0o777 == 0o600


def test_failing_job_stops_batch_but_still_restores_and_verifies(tmp_path):
    m, trace, _ = fresh(tmp_path)
    m["jobs"][0]["run"]["argv"][-1] = "FAIL"
    r = invoke(m, tmp_path)
    assert r["result"] == "BLOCKED"
    assert [j["result"] for j in r["jobs"]] == ["BLOCKED"]
    assert trace.read_text().splitlines() == [
        "session_preflight", "j0-preflight", "j0-run",
        "j0-restore", "j0-verify", "session_restore", "session_verify"
    ]


def test_failing_restore_stops_batch(tmp_path):
    m, trace, _ = fresh(tmp_path)
    m["jobs"][0]["restore"]["argv"][-1] = "FAIL"
    r = invoke(m, tmp_path)
    assert r["result"] == "BLOCKED"
    assert len(r["jobs"]) == 1
    assert "j0-verify" in trace.read_text()
    assert "j1-run" not in trace.read_text()


def test_failing_verify_stops_batch(tmp_path):
    m, trace, _ = fresh(tmp_path)
    m["jobs"][0]["verify"]["argv"][-1] = "FAIL"
    r = invoke(m, tmp_path)
    assert r["result"] == "BLOCKED"
    assert len(r["jobs"]) == 1
    assert "session_restore" in trace.read_text()


def test_session_preflight_failure_never_runs_jobs(tmp_path):
    m, trace, _ = fresh(tmp_path)
    m["session_preflight"]["argv"][-1] = "FAIL"
    r = invoke(m, tmp_path)
    assert r["result"] == "BLOCKED"
    assert not r["jobs"]
    assert trace.read_text().splitlines() == [
        "session_preflight", "session_restore", "session_verify"
    ]


def test_timeout_terminates_subprocess_and_restores(tmp_path):
    m, trace, _ = fresh(tmp_path, jobs=1)
    m["jobs"][0]["run"]["argv"][-1] = "TIMEOUT"
    m["jobs"][0]["run"]["timeout_seconds"] = 1
    r = invoke(m, tmp_path)
    assert r["result"] == "BLOCKED"
    assert any(e.get("status") == "TIMEOUT" for e in r["events"])
    assert "j0-restore" in trace.read_text()
    assert "session_restore" in trace.read_text()


@pytest.mark.parametrize("kwargs", [
    {"digest": "0" * 64}, {"ack": ""}, {"dummy": False},
    {"operator": ""}, {"ack": "https://github.com/tcarwash/flux_radio/issues/11"},
])
def test_missing_or_invalid_approvals_refuse_without_running(tmp_path, kwargs):
    m, trace, _ = fresh(tmp_path)
    with pytest.raises(batch.Refused):
        invoke(m, tmp_path, **kwargs)
    assert not trace.exists()


def test_expired_lease_refuses_without_running(tmp_path):
    m, trace, _ = fresh(tmp_path, expire=True)
    with pytest.raises(batch.Refused, match="expired"):
        invoke(m, tmp_path)
    assert not trace.exists()


def test_source_pin_drift_blocks_job_and_runs_session_cleanup(tmp_path):
    m, trace, script = fresh(tmp_path)
    script.write_text(script.read_text() + "\n# altered\n")
    r = invoke(m, tmp_path)
    assert r["result"] == "BLOCKED"
    assert "j0-run" not in trace.read_text()
    assert "session_restore" in trace.read_text()


def test_invalid_schema_rejected(tmp_path):
    m, _, _ = fresh(tmp_path)
    m["jobs"][1]["id"] = m["jobs"][0]["id"]
    with pytest.raises(batch.Refused, match="duplicate"):
        batch.validate_manifest(m)
    m, _, _ = fresh(tmp_path)
    m["jobs"][0]["run"]["argv"] = ["sh", "-c", "true"]
    with pytest.raises(batch.Refused, match="absolute executable"):
        batch.validate_manifest(m)


def test_nonblocking_session_lock_prevents_two_batch_runners(tmp_path):
    m, trace, _ = fresh(tmp_path)
    locks = tmp_path / "locks"
    locks.mkdir()
    name = hashlib.sha256(m["port"].encode()).hexdigest() + ".batch.lock"
    with (locks / name).open("a+b") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(batch.Refused, match="another batch"):
            invoke(m, tmp_path)
    assert not trace.exists()


def test_same_session_id_refuses_to_overwrite_evidence(tmp_path):
    m, trace, _ = fresh(tmp_path, jobs=1)
    assert invoke(m, tmp_path)["result"] == "PASS"
    with pytest.raises(FileExistsError):
        invoke(m, tmp_path)
