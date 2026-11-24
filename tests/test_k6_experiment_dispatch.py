"""Trusted queue and no-hardware dispatcher validation."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location("k6_experiment_dispatch",ROOT/"tools/k6_experiment_dispatch.py")
q=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(q)

def fixture(tmp_path, monkeypatch, bad_author=False):
    repo=tmp_path/"repo";repo.mkdir()
    subprocess.run(["git","init","-q",str(repo)],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.email","test@example.invalid"],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.name","Test"],check=True)
    script=repo/"test.py"
    script.write_text("print('TARGET EMULATOR: PASS')\n")
    subprocess.run(["git","-C",str(repo),"add","test.py"],check=True)
    subprocess.run(["git","-C",str(repo),"commit","-qm","freeze"],check=True)
    head=subprocess.check_output(["git","-C",str(repo),"rev-parse","HEAD"],text=True).strip()
    reg={
      "schema":1,"repo":"tcarwash/flux_radio","queue_issue":11,
      "trusted_author":"tcarwash",
      "experiments":{"9":{"kind":"OFFLINE","worktree":str(repo),"head_sha":head,
      "argv":["/usr/bin/python3","./test.py"],"timeout_seconds":5,
      "pins":[{"path":"test.py","sha256":hashlib.sha256(script.read_bytes()).hexdigest()}]}}
    }
    path=tmp_path/"registry.json";path.write_text(json.dumps(reg))
    calls=[]
    def fake_gh(*args):
        calls.append(args)
        if args[2]=="11":
            return {"author":{"login":"tcarwash"},"state":"OPEN",
            "body":"| P0 | [#9 experiment](https://github.com/tcarwash/flux_radio/issues/9) |"}
        if args[2]=="9":
            return {"author":{"login":"intruder" if bad_author else "tcarwash"},
                    "state":"OPEN","number":9,"title":"Not an instruction; ; rm -rf"}
        raise AssertionError(args)
    monkeypatch.setattr(q,"gh",fake_gh)
    return reg,path,script,calls

def test_dry_run_trusted_discovery_no_execution(tmp_path,monkeypatch,capsys):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    assert q.main(["--registry",str(path),"--issue","9"])==0
    info=json.loads(capsys.readouterr().out)
    assert info["mode"]=="READ_ONLY" and info["selected"]==[9]
    assert not list(tmp_path.glob("output/*"))
    assert calls

def test_execute_locally_pinned_job_and_private_evidence(tmp_path,monkeypatch):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    output=tmp_path/"output"
    assert q.main(["--registry",str(path),"--issue","9","--run-offline","--output-root",str(output)])==0
    logs=list(output.glob("issue-9-*/offline.log"))
    assert len(logs)==1 and "TARGET EMULATOR: PASS" in logs[0].read_text()
    result=json.loads((logs[0].parent/"result.json").read_text())
    assert result["result"]=="PASS" and result["hardware_access"]=="NOT REQUESTED BY DISPATCHER"
    assert logs[0].stat().st_mode & 0o777==0o600
    assert result["log_sha256"]==hashlib.sha256(logs[0].read_bytes()).hexdigest()

def test_untrusted_author_never_executes(tmp_path,monkeypatch,capsys):
    reg,path,script,calls=fixture(tmp_path,monkeypatch,bad_author=True)
    assert q.main(["--registry",str(path),"--issue","9","--run-offline","--output-root",str(tmp_path/"output")])==2
    assert "not open and authored" in capsys.readouterr().err
    assert not (tmp_path/"output").exists()

def test_source_drift_refuses_before_execution(tmp_path,monkeypatch,capsys):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    script.write_text("print('changed')\n")
    assert q.main(["--registry",str(path),"--issue","9","--run-offline","--output-root",str(tmp_path/"output")])==2
    assert not (tmp_path/"output").exists()

def test_unregistered_and_mismatched_queue_link_refuse(tmp_path,monkeypatch,capsys):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    assert q.main(["--registry",str(path),"--issue","999","--run-offline"])==2
    assert "not locally registered" in capsys.readouterr().err
    assert q.ranked_ids("| P0 | [#9](https://github.com/tcarwash/flux_radio/issues/99) |","tcarwash/flux_radio")==[]

def test_bad_pin_and_shell_eval_rejected(tmp_path,monkeypatch):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    reg["experiments"]["9"]["argv"]=["bash","-c","touch /tmp/whatever"]
    path.write_text(json.dumps(reg))
    with pytest.raises(q.Refused,match="absolute argv"): q.load_registry(path)
    reg["experiments"]["9"]["argv"]=["/usr/bin/python3","-c","print(2)"]
    path.write_text(json.dumps(reg))
    with pytest.raises((q.Refused,FileNotFoundError)):
        q.verify_local(9,q.load_registry(path)["experiments"]["9"])

def test_unauthorized_publish_flag_refuses(tmp_path,monkeypatch):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    assert q.main(["--registry",str(path),"--issue","9","--publish"])==2

def test_fake_issue_body_never_supplies_code(tmp_path,monkeypatch):
    reg,path,script,calls=fixture(tmp_path,monkeypatch)
    assert q.ranked_ids("| P0 | [#9](https://github.com/attacker/repo/issues/9) |","tcarwash/flux_radio")==[]
