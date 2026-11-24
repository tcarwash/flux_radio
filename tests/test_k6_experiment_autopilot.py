"""No-device tests of durable, single-attempt trusted queue autopilot."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
SPEC=importlib.util.spec_from_file_location("k6_experiment_autopilot",ROOT/"tools/k6_experiment_autopilot.py")
a=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(a)

def fixture(tmp_path,monkeypatch):
    reg={"schema":1,"repo":"tcarwash/flux_radio","queue_issue":11,
         "trusted_author":"tcarwash","experiments":{
             str(n):{"kind":"OFFLINE","head_sha":f"{n:040x}","pins":[{"path":"x","sha256":f"{n:064x}"}],
                    "argv":["/usr/bin/true"],"worktree":str(tmp_path),"timeout_seconds":3} for n in (9,10)}}
    path=tmp_path/"reg.json";path.write_text(json.dumps(reg))
    monkeypatch.setattr(a.dispatch,"check_github",lambda reg,nums:[9,10])
    monkeypatch.setattr(a.dispatch,"verify_local",lambda *args:None)
    return reg,path

def test_once_then_no_duplicates(tmp_path,monkeypatch):
    reg,path=fixture(tmp_path,monkeypatch)
    calls=[];published=[]
    def fake_run(issue,reg,root):
        calls.append(issue)
        return {"issue":issue,"kind":"OFFLINE","result":"PASS","completed_utc":"now",
                "log":"a","log_sha256":"b"}
    monkeypatch.setattr(a.dispatch,"offline_once",fake_run)
    monkeypatch.setattr(a.dispatch,"publish_result",lambda r,result:published.append(result["issue"]))
    x=a.one_cycle(path,tmp_path/"state.json",tmp_path/"out",True,2)
    assert calls==[9,10] and published==[9,10]
    assert a.one_cycle(path,tmp_path/"state.json",tmp_path/"out",True,2)["events"][0]["action"]=="SKIPPED_ALREADY_ATTEMPTED"
    assert calls==[9,10]

def test_fail_stays_failed(tmp_path,monkeypatch):
    reg,path=fixture(tmp_path,monkeypatch)
    n=[]
    def fail(issue,reg,root):
        n.append(issue);raise RuntimeError("hardware unavailable (fake)")
    monkeypatch.setattr(a.dispatch,"offline_once",fail)
    with pytest.raises(RuntimeError):
        a.one_cycle(path,tmp_path/"state.json",tmp_path/"out")
    assert a.one_cycle(path,tmp_path/"state.json",tmp_path/"out",max_jobs=1)["events"][0]["action"]=="SKIPPED_ALREADY_ATTEMPTED"
    assert n==[9]  # need skip 9; second may have started on next cycle? adjust below

def test_journal_corruption_blocks(tmp_path):
    p=tmp_path/"state.json";p.write_text('{"wrong":true}')
    with pytest.raises(a.Refused):a.state_load(p)

def test_import_previous_evidence(tmp_path):
    e={"head_sha":"f"*40,"pins":[],"argv":["/usr/bin/true"]}
    p=tmp_path/"issue-9-old";p.mkdir()
    log=p/"offline.log";log.write_text("ok")
    digest=hashlib.sha256(log.read_bytes()).hexdigest()
    (p/"result.json").write_text(json.dumps({"issue":9,"kind":"OFFLINE","commit":e["head_sha"],
        "pins":[],"argv":e["argv"],"result":"PASS","log":str(log),"log_sha256":digest}))
    assert a.existing_evidence(tmp_path,9,e)["result"]=="PASS"
