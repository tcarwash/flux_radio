"""Shared offline result ledger stays independent of GitHub and radio."""
import json
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import k6_offline_results as offline


def test_register_finish_search_and_immutable_result(tmp_path):
    with offline.database(tmp_path / "results.sqlite3") as con:
        rid = offline.register(con, name="Sotto mock link", owner="agent-1",
                               sha="a" * 40, ref="research/sotto",
                               tests=["tests/test_sotto.py"])
        assert rid.startswith("off-")
        assert len(offline.query(con, text="Sotto")) == 1
        assert offline.query(con, status="PASS") == []
        done = offline.finish(con, rid, status="PASS", summary="Codec mock passed, not RF",
                              metrics={"tests_passed": 12})
        assert done["scope"] == "OFFLINE"
        assert done["metrics"] == {"tests_passed": 12}
        assert done["hardware_operations"] == 0
        assert done["rf_transmissions"] == 0
        assert done["finished_at"]
        assert len(offline.query(con, text="research/sotto", status="PASS")) == 1
        with pytest.raises(ValueError, match="finalized"):
            offline.finish(con, rid, status="FAIL", summary="overwrite")


def test_import_sweep_idempotent_and_not_an_issue(tmp_path):
    state = tmp_path / "old-sweep.json"
    config = tmp_path / "cfg.json"
    config.write_text(json.dumps({"jobs": {"42": {"ref": "origin/research/some-branch"}}}))
    state.write_text(json.dumps({"schema": 1, "jobs": {
        "issue-42:" + "b"*40: {
            "issue": 42, "source_sha": "b"*40, "result": "PASS",
            "test_passed": 7, "tests": ["tests/test_fixture.py"],
            "log": "/tmp/result.log", "log_sha256": "f"*64,
            "rf_transmissions": 0, "hardware_operations": 0,
        },
        "issue-99:" + "a"*40: {
            "issue": 99, "source_sha": "a"*40,
            "result": "STARTED_NO_AUTO_RETRY", "tests": ["tests/test_pending.py"],
        },
    }}))
    with offline.database(tmp_path / "results.sqlite3") as con:
        first = offline.import_sweep(state, con=con, config_path=config)
        second = offline.import_sweep(state, con=con, config_path=config)
        assert first["imported"] == 1
        assert second["imported"] == 0 and second["already_present"] == 1
        rows = offline.query(con, text="test_fixture")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"].startswith("sweep-")
        assert "issue" not in row["id"].lower()
        assert row["provenance"] == "fixture-sweep"
        assert row["source_ref"] == "origin/research/some-branch"
        assert row["metrics"]["tests_passed"] == 7
        assert row["rf_transmissions"] == 0
        assert row["hardware_operations"] == 0
        assert not offline.query(con, text="test_pending")


def test_source_validation_and_terminal_states(tmp_path):
    with offline.database(tmp_path / "results.sqlite3") as con:
        with pytest.raises(ValueError):
            offline.register(con, name="bad", owner="agent", sha="HEAD")
        rid = offline.register(con, name="negative result", owner="agent")
        with pytest.raises(ValueError):
            offline.finish(con, rid, status="PASS", summary="")
        result = offline.finish(con, rid, status="FAIL", summary="model mismatch")
        assert result["status"] == "FAIL"
        assert offline.query(con, status="FAIL")[0]["id"] == rid
        with pytest.raises(ValueError):
            offline.query(con, limit=100000)
