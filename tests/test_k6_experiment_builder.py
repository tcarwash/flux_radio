"""Builder invariants. All unit tests are device- and network-free."""
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import k6_experiment_builder as b


def test_config_real_and_bounded():
    cfg=b.config_load()
    assert cfg["repo"]=="tcarwash/flux_radio"
    assert cfg["max_daily"]<=16
    assert cfg["recipes"]["21"]["kind"]=="RX_ONLY"


def test_reject_untrusted_or_escape(tmp_path):
    original=b.config_load()
    bad=json.loads(json.dumps(original))
    bad["recipes"]["21"]["allowed_paths"]=["../../dev/ttyUSB0"]
    p=tmp_path/"bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(b.Refused,match="outside bounded scope"):
        b.config_load(p)
    bad=json.loads(json.dumps(original))
    bad["recipes"]["21"]["base_ref"]="origin/main; rm -rf /"
    p.write_text(json.dumps(bad))
    with pytest.raises(b.Refused,match="source branch"):
        b.config_load(p)


def test_sandbox_hides_devices_and_unshares_test_network(tmp_path):
    ws=tmp_path/"ws";ws.mkdir()
    command=b.sandbox("/usr/bin/python3","-c","print(1)",
                      worktree=ws,allow_write=True,allow_net=False)
    assert command[0]=="/usr/bin/bwrap"
    assert ["--dev","/dev"] == command[4:6]
    assert "--unshare-net" in command
    assert str(ws) in command and command[-1]=="print(1)"
    assert "--bind" in command
    assert "PYTHONDONTWRITEBYTECODE" in command


def test_candidate_scope_no_extra_files(tmp_path,monkeypatch):
    ws=tmp_path/"ws";ws.mkdir()
    (ws/"tools").mkdir()
    (ws/"tools"/"candidate.py").write_text("hello")
    monkeypatch.setattr(b,"run",lambda *a,**kw:"?? tools/candidate.py")
    assert b.candidate_files(ws,{"tools/candidate.py"})==["tools/candidate.py"]
    with pytest.raises(b.Refused,match="unapproved path"):
        b.candidate_files(ws,{"other.py"})
    (ws/"tools"/"candidate.py").unlink()
    (ws/"tools"/"candidate.py").symlink_to("/dev/null")
    with pytest.raises(b.Refused,match="invalid source file"):
        b.candidate_files(ws,{"tools/candidate.py"})


def test_prompt_has_static_trusted_scope_not_issue_comments(tmp_path):
    cfg=b.config_load()
    ws=tmp_path/"ws"
    p=tmp_path/"prompt"
    b.make_prompt(21,cfg["recipes"]["21"],ws,p)
    text=p.read_text()
    assert "NO shell execution tool" in text
    assert "The radio is dummy-loaded" in text
    assert "ONLY" not in text or "Only MODIFY" in text
    assert "tools/k6_tx28_fastspi_rx_bench.py" in text
    assert p.stat().st_mode & 0o777 == 0o600


def test_no_triage_avoids_delegate_and_hardware(tmp_path,monkeypatch):
    reg=tmp_path/"recipes.json"
    cfg=b.config_load()
    reg.write_text(json.dumps(cfg))
    state=tmp_path/"state.json"
    intake=tmp_path/"intake.json"
    intake.write_text(json.dumps({"schema":1,"jobs":{}}))
    monkeypatch.setattr(b,"REPO",tmp_path)
    out=b.build_once(reg,state,intake,tmp_path/"out",repo=tmp_path)
    assert out["physical_runs"]==0
    assert out["events"]==[]
    assert not state.exists() or json.loads(state.read_text())["jobs"]=={}


def test_imports_do_not_require_radio():
    assert b.STATE.name=="builder.json"
    assert b.INTAKE.name=="intake.json"



def test_tracked_file_porcelain_leading_space_is_preserved(tmp_path):
    import subprocess
    repo=tmp_path/"repo"
    repo.mkdir()
    subprocess.run(["git","init","-q",str(repo)],check=True)
    (repo/"tools").mkdir()
    file=repo/"tools"/"candidate.py"
    file.write_text("old")
    subprocess.run(["git","-C",str(repo),"add","tools/candidate.py"],check=True)
    subprocess.run(["git","-C",str(repo),"-c","user.name=Test",
                    "-c","user.email=test@localhost",
                    "commit","-qm","baseline"],check=True)
    file.write_text("new")
    assert b.candidate_files(repo,{"tools/candidate.py"})==["tools/candidate.py"]
