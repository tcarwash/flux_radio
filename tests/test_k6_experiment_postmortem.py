"""Offline review input verification: all tests use tmp_path; no radio or delegate."""
import importlib.util
import hashlib
import json
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sp=importlib.util.spec_from_file_location("k6_experiment_postmortem",ROOT/"tools/k6_experiment_postmortem.py")
p=importlib.util.module_from_spec(sp);sp.loader.exec_module(p)


def setup_evidence(tmp_path,monkeypatch):
    batch=tmp_path/"batch"
    batch.mkdir()
    monkeypatch.setattr(p,"BATCHES",tmp_path.resolve())
    m={"session_id":"batch","jobs":[{"id":"job","cwd":"/reviewed/local/worktree",
       "issue_url":"https://github.com/tcarwash/flux_radio/issues/9",
       "run":{"argv":["/usr/bin/python3","/pinned.py","--prefix",str(batch/"rf")]}}]}
    raw=json.dumps(m).encode()
    (batch/"manifest.json").write_bytes(raw)
    (batch/"result.json").write_text(json.dumps({"session_id":"batch","result":"BLOCKED",
       "events":[{"kind":"STEP","name":"run","status":"FAIL"}]}))
    (batch/"rf.json").write_text(json.dumps({"hardware_lab_transaction":{
       "tx_module_requests":1,"module_completion":{"result":-14},
       "original_module_restored":True,"original_radio_frequency_restored":True}}))
    (batch/"rf-rx.json").write_text(json.dumps({"frames_found":1,"result":"RF_PAYLOAD_FAIL",
                                      "frames":[{"sync_correlation":.994103}]}))
    digest=hashlib.sha256(raw).hexdigest()
    key=f"issue-9:{digest}"
    attempt={"state":"BLOCKED_NO_AUTO_RETRY","session_id":"batch",
             "evidence":str(batch/"result.json")}
    hw=tmp_path/"hw.json"
    p.journal.state_write(hw,{"schema":1,"jobs":{key:attempt}})
    return hw,key,attempt


def test_collect_preserves_evidence_and_safe_summary(tmp_path,monkeypatch):
    hw,key,attempt=setup_evidence(tmp_path,monkeypatch)
    x=p.collect(key,attempt)
    assert x["metrics"][0]["module_return"]==-14
    assert x["metrics"][0]["rf_frames_found"]==1
    assert x["metrics"][0]["frame_sync_correlations"]==[.9941]
    prompt=p.prompt(x)
    assert "NO HARDWARE COMMANDS" in prompt
    assert "same attempt" in prompt


def test_manifest_drift_is_refused(tmp_path,monkeypatch):
    hw,key,attempt=setup_evidence(tmp_path,monkeypatch)
    (tmp_path/"batch/manifest.json").write_text("{}")
    with pytest.raises(ValueError,match="drift"):
        p.collect(key,attempt)


def test_delegates_once_and_never_hardware(tmp_path,monkeypatch):
    hw,key,attempt=setup_evidence(tmp_path,monkeypatch)
    monkeypatch.setattr(p.delegate,"delegate_readonly",lambda path:"deadbeef0000")
    state=tmp_path/"postmortem.json"
    prompts=tmp_path/"prompts"
    x=p.submit_once(hw,state,prompts)
    assert x["events"][0]["action"]=="POSTMORTEM_DELEGATED"
    import subprocess
    class Ret:
        stdout=json.dumps({"job":{"state":"running","id":"deadbeef0000"},"result":""})
    monkeypatch.setattr(p.subprocess,"run",lambda *a,**k:Ret())
    assert p.submit_once(hw,state,prompts)["events"][0]["action"]=="POSTMORTEM_PENDING"
    assert len(list(prompts.iterdir()))==1
    assert p.journal.state_load(state)["jobs"][key]["state"]=="DELEGATED"


def test_collect_supports_bundled_session_and_rejects_drift(tmp_path,monkeypatch):
    batch=tmp_path/"bundle-20-26"
    batch.mkdir()
    monkeypatch.setattr(p,"BATCHES",tmp_path.resolve())
    m={"session_id":"bundle-20-26","jobs":[
        {"id":"j20","cwd":"/wt/20","issue_url":"https://github.com/tcarwash/flux_radio/issues/20",
         "run":{"argv":["/usr/bin/python3","/p20.py","--prefix",str(batch/"issue-20-j20")]}},
        {"id":"j26","cwd":"/wt/26","issue_url":"https://github.com/tcarwash/flux_radio/issues/26",
         "run":{"argv":["/usr/bin/python3","/p26.py","--prefix",str(batch/"issue-26-j26")]}},
    ]}
    raw=json.dumps(m).encode()
    (batch/"manifest.json").write_bytes(raw)
    (batch/"result.json").write_text(json.dumps({"session_id":"bundle-20-26","result":"PASS",
        "events":[{"kind":"STEP","name":"job02-j26-run","status":"PASS"}]}))
    (batch/"issue-26-j26.json").write_text(json.dumps({"hardware_lab_transaction":{
        "tx_module_requests":2,"module_completion":{"result":0},
        "original_module_restored":True,"original_radio_frequency_restored":True}}))
    bundle_sha=hashlib.sha256(raw).hexdigest()
    key=f"issue-26:{'b'*64}"
    attempt={"state":"PASS","session_id":"bundle-20-26",
             "bundle_manifest_sha256":bundle_sha,
             "evidence":str(batch/"result.json")}
    out=p.collect(key,attempt)
    assert out["issue"]==26
    assert out["reviewed_worktrees"]==["/wt/26"]
    assert out["metrics"][0]["job"]=="j26"
    assert out["metrics"][0]["tx_attempts"]==2
    (batch/"manifest.json").write_text("{}")
    with pytest.raises(ValueError,match="drift"):
        p.collect(key,attempt)

