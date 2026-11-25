"""GitHub archival requires explicit agent decision and preserves unfinished research."""
from pathlib import Path
import json
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"tools"))
import k6_experiment_archive as archive


def issue(n=16, title="[Flux RF][OFFLINE] Dual tone audit", state="open"):
    return {"number": n, "title": title, "state": state,
            "html_url": f"https://github.com/tcarwash/flux_radio/issues/{n}"}


def test_only_offline_scopes_archivable(tmp_path):
    for bad in ("[Flux RF][HARDWARE] physical", "[Flux RF] coordination", "KISS issue"):
        with pytest.raises(archive.Refused):
            archive.close_plan(issue(title=bad), "completed", "Full result verified",
                               ["https://github.com/tcarwash/flux_radio/commit/abcdef123"],state_dir=tmp_path)
    with pytest.raises(archive.Refused):
        archive.close_plan(issue(state="closed"), "completed", "full result verified",
                           ["https://github.com/tcarwash/flux_radio/commit/abcdef123"],state_dir=tmp_path)


def test_migration_and_completion_plans_are_dry_run(tmp_path):
    data=archive.close_plan(issue(), "completed", "Offline audit was completed; RF not qualified",
                            ["https://github.com/tcarwash/flux_radio/commit/abcdef123"],state_dir=tmp_path)
    assert data["ledger_id"] is None
    migrated=archive.close_plan(issue(8), "migrated", "Pending refinement moved to shared ledger",
                            ["https://github.com/tcarwash/flux_radio/issues/8"],state_dir=tmp_path)
    assert migrated["ledger_id"]=="legacy-issue-8"


def test_refuse_active_candidate_even_if_historic(tmp_path):
    (tmp_path/"builder.json").write_text(json.dumps({"schema":1,"jobs":{"issue-21":{"state":"BLOCKED_NO_AUTO_RETRY"}}}))
    assert archive.active_candidate(21,tmp_path)
    with pytest.raises(archive.Refused,match="candidate"):
        archive.close_plan(issue(21), "completed", "RX probe passed but waveform pending",
                           ["https://github.com/tcarwash/flux_radio/issues/21"],state_dir=tmp_path)


def test_physical_closeout_requires_independent_restore_and_full_acceptance(tmp_path):
    item = issue(9, title="[Flux RF][HARDWARE] FIFO append test")
    evidence = ["https://github.com/tcarwash/flux_radio/issues/9"]
    with pytest.raises(archive.Refused, match="reviewed acceptance"):
        archive.close_plan(item, "completed", "all stated RF gates met",
                           evidence, scope="hardware", state_dir=tmp_path)
    with pytest.raises(archive.Refused, match="reviewed acceptance"):
        archive.close_plan(item, "completed", "all stated RF gates met",
                           evidence, scope="hardware", acceptance_reviewed=True,
                           state_dir=tmp_path)
    plan = archive.close_plan(item, "completed",
                              "All stated RF gates met and radio safely restored",
                              evidence, scope="hardware", acceptance_reviewed=True,
                              restoration_evidence="https://github.com/tcarwash/flux_radio/issues/11#issuecomment-123",
                              state_dir=tmp_path)
    assert plan["scope"] == "hardware"
    assert "issuecomment-123" in plan["restoration_evidence"]


def test_invalid_evidence_refused(tmp_path):
    with pytest.raises(archive.Refused,match="source links"):
        archive.close_plan(issue(), "completed", "a reasonably long conclusion",
                           ["https://example.com/instructions"],state_dir=tmp_path)


def test_closed_search_includes_closed_flag(monkeypatch):
    calls=[]
    def fake(*args):
        calls.append(args)
        return '[{"number": 17, "title":"REG40", "state":"CLOSED"}]'
    monkeypatch.setattr(archive,"gh",fake)
    result=archive.search("REG40","closed",50)
    assert result["count"]==1
    assert calls[0][:5]==("issue","list","--repo",archive.REPO,"--state")
    assert calls[0][5]=="closed"
    assert calls[0][-2:]==("--search","REG40")
