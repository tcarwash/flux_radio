import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_experiment_queue import plan

def test_order_trust_and_api():
    repo = "owner/project"
    q = {"body": "| 1 | [#9](https://github.com/owner/project/issues/9) |\n| 2 | [#7](https://github.com/owner/project/issues/7) |"}
    issues = [{"number": n, "state": "OPEN", "title": "untrusted text", "author": {"login": owner}}
              for n, owner in [(7,"owner"),(9,"other"),(10,"owner")]]
    registry = {"7":{"validated_module_api":28}, "9":{"validated_module_api":29}}
    result = plan(q, issues, registry, 29, repo, "owner")
    assert [r["number"] for r in result] == [9,7,10]
    assert result[0]["status"] == "untrusted issue author"
    assert "retargeting" in result[1]["status"]
    assert "not ranked" in result[2]["status"]
    assert "locally registered" in plan(q, issues, registry, 28, repo, "owner")[1]["status"]

def test_offline_registration_not_blocked_by_hw_api():
    q={"body":"| 1 | [#10](https://github.com/owner/project/issues/10) |"}
    issues=[{"number":10,"state":"OPEN","title":"sotto",
             "author":{"login":"owner"}}]
    got=plan(q,issues,{"10":{"kind":"OFFLINE","head_sha":"abc"}},29,"owner/project","owner")
    assert "locally pinned OFFLINE" in got[0]["status"]

def test_mismatched_rank_link_not_approved():
    q={"body":"| 1 | [#9](https://github.com/owner/project/issues/99) |"}
    issues=[{"number":9,"state":"OPEN","title":"trick","author":{"login":"owner"}}]
    got=plan(q,issues,{},29,"owner/project","owner")
    assert not got[0]["ranked"]
