"""Hardware handoff proof with fake devices; NEVER operates real serial/SDR."""
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
SPEC=importlib.util.spec_from_file_location("k6_experiment_hardware",ROOT/"tools/k6_experiment_hardware.py")
h=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)

def setup(tmp_path,monkeypatch):
    source=tmp_path/"source.py";source.write_text("print('nothing touches hardware')\n")
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    pin=[{"path":str(source),"sha256":sha}]
    s={"argv":["/usr/bin/true"],"timeout_seconds":5}
    future=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=20)).isoformat()
    manifest={"version":1,"session_id":"test-session","port":"/dev/ttyUSB0",
      "queue_issue_url":"https://github.com/tcarwash/flux_radio/issues/11",
      "lease_expires_utc":future,"session_pins":pin,"session_preflight":s,
      "session_restore":s,"session_verify":s,
      "jobs":[{"id":"j9","issue_url":"https://github.com/tcarwash/flux_radio/issues/9",
      "cwd":str(tmp_path),"pins":pin,"minimum_module_api":3,
      "preflight":s,"run":s,"restore":s,"verify":s}]}
    raw=json.dumps(manifest).encode()
    path=tmp_path/"session.json";path.write_bytes(raw)
    cfg={"schema":1,"repo":"tcarwash/flux_radio","queue_issue":11,
         "trusted_author":"tcarwash",
         "manifests":{"9":{"path":str(path),"sha256":hashlib.sha256(raw).hexdigest()}}}
    cfgpath=tmp_path/"hardware.json";cfgpath.write_text(json.dumps(cfg))
    api,apibytes=h.batch.read_api_config(h.batch.API_CONFIG)
    approval=h.digest_for(raw,apibytes)
    url="https://github.com/tcarwash/flux_radio/issues/11#issuecomment-12345"
    monkeypatch.setattr(h.queue,"check_github",lambda *args:[9])
    return cfgpath,path,manifest,approval,url

def fake_ack(tmp_path,monkeypatch,manifest,approval,url,author="tcarwash",claims=False):
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    body=(f"BENCH ACK | issue=9 | session={manifest['session_id']} | "
          f"approval_sha256={approval} | operator=AG7SU | scope=whole-bench")
    ack={"id":12345,"issue_url":"https://api.github.com/repos/tcarwash/flux_radio/issues/11",
         "user":{"login":author},"created_at":now,"body":body}
    comments=[ack]
    if claims: comments.append({"id":12346,"body":"CLAIM | agent2 | all bench"})
    def api(route,paginate=False):
        return [comments] if paginate else ack
    monkeypatch.setattr(h,"gh_api",api)
    return ack

def test_preview_never_runs_steps(tmp_path,monkeypatch,capsys):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(h.batch,"run_session",lambda *a,**k:pytest.fail("executed on preview"))
    assert h.main(["--config",str(cfgpath),"--issue","9"])==0
    d=json.loads(capsys.readouterr().out)
    assert d["mode"]=="HARDWARE_DRY_RUN_NO_COMMANDS"
    assert d["approval_sha256"]==approval

def test_no_ack_blocks_before_commands(tmp_path,monkeypatch):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(h.batch,"run_session",lambda *a,**k:pytest.fail("executed without ACK"))
    assert h.main(["--config",str(cfgpath),"--issue","9","--execute",
       "--approve-digest",approval,"--operator","AG7SU","--dummy-load-confirmed"])==2

def test_invalid_ack_author_blocks(tmp_path,monkeypatch):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    fake_ack(tmp_path,monkeypatch,m,approval,url,author="other-person")
    monkeypatch.setattr(h.batch,"run_session",lambda *a,**k:pytest.fail("executed untrusted"))
    assert h.main(["--config",str(cfgpath),"--issue","9","--execute",
       "--approve-digest",approval,"--operator","AG7SU","--dummy-load-confirmed",
       "--ack-url",url])==2

def test_valid_ack_handoff_uses_locked_reviewed_batch(tmp_path,monkeypatch,capsys):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    fake_ack(tmp_path,monkeypatch,m,approval,url)
    seen=[]
    def fake_run(manifest,raw,digest,**kwargs):
        seen.append((manifest["session_id"],digest,kwargs["dummy_load_confirmed"]))
        return {"result":"PASS","session_id":manifest["session_id"],"jobs":[{"id":"j9","result":"PASS"}]}
    monkeypatch.setattr(h.batch,"run_session",fake_run)
    assert h.main(["--config",str(cfgpath),"--issue","9","--execute",
       "--approve-digest",approval,"--operator","AG7SU","--dummy-load-confirmed",
       "--ack-url",url,"--bench-lock",str(tmp_path/"bench.lock"),
       "--output-root",str(tmp_path/"out")])==0
    assert seen==[("test-session",approval,True)]
    assert json.loads(capsys.readouterr().out)["result"]=="PASS"

def test_new_conflicting_claim_blocks_hardware(tmp_path,monkeypatch):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    fake_ack(tmp_path,monkeypatch,m,approval,url,claims=True)
    monkeypatch.setattr(h.batch,"run_session",lambda *a,**k:pytest.fail("executed during claim"))
    assert h.main(["--config",str(cfgpath),"--issue","9","--execute",
       "--approve-digest",approval,"--operator","AG7SU","--dummy-load-confirmed",
       "--ack-url",url])==2

def test_manifest_hash_mismatch_blocks(tmp_path,monkeypatch):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    path.write_text(path.read_text()+" ")
    with pytest.raises(h.batch.Refused,match="changed since review"):
        h.load_registered_manifest(h.load_config(cfgpath),9)

def test_wrong_job_issue_blocks(tmp_path,monkeypatch):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    m["jobs"][0]["issue_url"]="https://github.com/tcarwash/flux_radio/issues/29"
    path.write_text(json.dumps(m))
    c=json.loads(cfgpath.read_text())
    c["manifests"]["9"]["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    cfgpath.write_text(json.dumps(c))
    with pytest.raises(h.batch.Refused,match="job issue mismatch"):
        h.load_registered_manifest(h.load_config(cfgpath),9)


def test_old_github_cli_paginated_history(monkeypatch):
    class Completed:
        stdout='[{"id":1}]\n[{"id":2},{"id":3}]\n'
    def run(argv,**kwargs):
        assert "--paginate" in argv and "--slurp" not in argv
        return Completed()
    monkeypatch.setattr(h.subprocess,"run",run)
    assert h.gh_api("repos/test/repo/issues/11/comments?per_page=100",paginate=True)==[
        [{"id":1}],[{"id":2},{"id":3}]]


def test_ack_comment_accepts_bundled_issues(tmp_path,monkeypatch):
    cfgpath,path,m,approval,url=setup(tmp_path,monkeypatch)
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    body=h.ack_line([20,26],m["session_id"],approval,"AG7SU")
    assert "issues=20,26" in body
    ack={"id":12345,"issue_url":"https://api.github.com/repos/tcarwash/flux_radio/issues/11",
         "user":{"login":"tcarwash"},"created_at":now,"body":body}
    monkeypatch.setattr(h,"gh_api",lambda route,paginate=False:[[ack]] if paginate else ack)
    cfg=h.load_config(cfgpath)
    assert h.ack_comment(cfg,[20,26],m,approval,url,"AG7SU")["id"]==12345
    with pytest.raises(h.batch.Refused,match="exact issue"):
        h.ack_comment(cfg,[20,30],m,approval,url,"AG7SU")


