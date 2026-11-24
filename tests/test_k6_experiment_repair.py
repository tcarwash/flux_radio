"""Blocked candidate repairs stay device-free, scoped and bounded."""
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import k6_experiment_repair as repair


def test_repair_is_explicitly_bounded_to_three_owned_candidates():
    assert repair.MAX_REPAIRS==2
    assert repair.PRIORITY==(26,30,20)


def test_no_external_device_access_in_repair_prompt(tmp_path):
    cfg=repair.builder.config_load()
    worktree=tmp_path/"ws"
    prompt=tmp_path/"prompt"
    repair.make_prompt(26,cfg["recipes"]["26"],{"error":"a test failed"},
                       worktree,"EAssertionError: fake fixture",prompt)
    content=prompt.read_text()
    assert "No fake passing fixture data." in content
    assert "OFFLINE, device-isolated" in content
    assert "tests/test_k6_tx24b_bench.py" in content
    assert "--preflight-only" in content
    assert "no serial" not in content.lower() or "USB" in content
    assert prompt.stat().st_mode&0o777==0o600


def test_stubbed_offline_code_is_not_promoted_as_physical():
    minimal=" --preflight-only --run --verify-only --prefix --dummy-load-confirmed --operator-lease-confirmed --queue-lease-acknowledged --max-tx-ms --frequency-hz"
    assert repair.has_live_hardware_path(minimal,"BOUNDED_TX")[0] is False
    assert repair.has_live_hardware_path(minimal+"\nPHYSICAL_BLOCKED run_module() SDR","BOUNDED_TX")[0] is False
    assert repair.has_live_hardware_path(minimal+"\nrun_module() mutate_library()","BOUNDED_TX")[0] is False
    assert repair.has_live_hardware_path(minimal+"\nrun_module() capture_sdr()","BOUNDED_TX")[0] is True
    assert repair.has_live_hardware_path(minimal+"\nmutate_library()","RX_ONLY")[0] is True


def test_no_blocked_work_returns_no_delegates(tmp_path):
    config=repair.builder.CONFIG
    path=tmp_path/"state.json"
    path.write_text(json.dumps({"schema":1,"jobs":{}}))
    out=repair.one_cycle(config,path,tmp_path/"out")
    assert out["events"]==[]
    assert out["hardware_operations"]==0
    assert out["eligible"]==[]


def test_too_many_attempts_is_never_retried(tmp_path,monkeypatch):
    path=tmp_path/"state.json"
    path.write_text(json.dumps({"schema":1,"jobs":{
        "issue-26":{"state":"BLOCKED_CANDIDATE_NEEDS_REPAIR",
                    "repair_attempts":repair.MAX_REPAIRS,
                    "worktree":"/should/not/be/visited"}}}))
    monkeypatch.setattr(repair.builder,"supervised",lambda *a,**kw:
                        pytest.fail("delegate called past retry limit"))
    out=repair.one_cycle(repair.builder.CONFIG,path,tmp_path/"out")
    assert out["events"]==[] and out["eligible"]==[]
