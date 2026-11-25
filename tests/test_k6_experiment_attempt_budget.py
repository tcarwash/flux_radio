"""Per-issue physical attempt cap: no RF, tests use synthetic journals."""
from pathlib import Path
import json
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import k6_experiment_attempt_budget as budget
import k6_experiment_failed_closeout as closer


def attempt_record(tmp_path,issue,seq,status="BLOCKED_NO_AUTO_RETRY",restored=True,
                   verified=True):
    session=f"auto-{issue}-{seq}"
    path=tmp_path/session/"result.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    d={"session_id":session,"result":"PASS" if status=="PASS" else "BLOCKED",
       "events":[{"kind":"SESSION_END","restored":restored,"verified":verified}]}
    path.write_text(json.dumps(d))
    (path.parent/"job01-run.log").write_text("refused: incomplete data")
    return f"issue-{issue}:{seq:064x}",{
        "state":status,"when":f"2026-11-24T0{seq}:00:00Z",
        "session_id":session,"evidence":str(path),
    }


def make_jobs(tmp_path,n,statuses=None):
    statuses=statuses or ["BLOCKED_NO_AUTO_RETRY"]*n
    return dict(attempt_record(tmp_path,20,i+1,s) for i,s in enumerate(statuses))


def test_exactly_three_physical_attempts_per_issue(tmp_path):
    jobs=make_jobs(tmp_path,2)
    jobs["issue-20:new-review"]={"state":"APPROVED"}
    jobs["issue-22:"+ "f"*64]={"state":"PASS"}
    assert budget.count(20,jobs)==2
    assert budget.exhausted(20,jobs) is False
    assert budget.closeout_eligible(20,jobs) is False
    jobs.update(make_jobs(tmp_path,3))
    assert budget.count(20,jobs)==3
    assert budget.exhausted(20,jobs)
    assert budget.closeout_eligible(20,jobs)
    assert budget.latest_verified_failure(20,jobs)["attempts"]==3
    assert budget.count(22,jobs)==1


def test_historic_pass_is_never_closed_as_failure(tmp_path):
    jobs=make_jobs(tmp_path,4,["BLOCKED_NO_AUTO_RETRY"]*3+["PASS"])
    assert budget.exhausted(20,jobs)
    assert not budget.closeout_eligible(20,jobs)
    assert closer.decide(jobs)=={}


def test_uncertain_restore_holds_closeout(tmp_path):
    jobs=make_jobs(tmp_path,3)
    latest=budget.issue_attempts(20,jobs)[-1][1]
    d=json.loads(Path(latest["evidence"]).read_text())
    d["events"][0]["verified"]=False
    Path(latest["evidence"]).write_text(json.dumps(d))
    assert budget.closeout_eligible(20,jobs)
    assert budget.latest_verified_failure(20,jobs) is None
    assert closer.decide(jobs)=={20:None}


def test_closeout_preview_never_touches_github(monkeypatch,tmp_path):
    jobs=make_jobs(tmp_path,3)
    state=tmp_path/"hardware.json"
    state.write_text(json.dumps({"schema":1,"jobs":jobs}))
    monkeypatch.setattr(closer,"gh",lambda *args: pytest.fail("preview called Github"))
    r=closer.one_cycle(state_path=tmp_path/"result.json",
                       hardware_state=state,preview=True)
    assert r["max_attempts"]==3
    assert r["events"]==[{"issue":20,"attempts":3,"action":"CLOSE_FAILED"}]


def test_failure_issue_closed_once_not_as_completed(monkeypatch,tmp_path):
    jobs=make_jobs(tmp_path,3)
    hardware=tmp_path/"hardware.json"
    hardware.write_text(json.dumps({"schema":1,"jobs":jobs}))
    calls=[]
    current={"state":"open"}
    def fake_gh(*args):
        calls.append(args)
        if args[:2]==("issue","close"):
            assert args[-2:]==("--reason","not planned")
            current["state"]="closed"
        if args[0]=="api":
            return json.dumps(current)
        return ""
    monkeypatch.setattr(closer,"gh",fake_gh)
    state=tmp_path/"failed-closeouts.json"
    r=closer.one_cycle(state_path=state,hardware_state=hardware)
    assert r["events"]==[{"issue":20,"action":"CLOSED_FAILED","attempts":3}]
    assert len([x for x in calls if x[:2]==("issue","comment")])==1
    r2=closer.one_cycle(state_path=state,hardware_state=hardware)
    assert r2["events"][0]["action"]=="ALREADY_CLOSED_FAILED"
    assert len([x for x in calls if x[:2]==("issue","comment")])==1


def test_missing_journal_fails_closed(tmp_path):
    with pytest.raises(ValueError,match="missing hardware"):
        budget.load_jobs(tmp_path/"missing.json")
