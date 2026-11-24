"""Autonomous one-shot promotion security; no real radio or GitHub mutations."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import k6_experiment_promotion as p
import k6_experiment_builder as b


def test_policy_matches_dummy_loaded_owner():
    x=p.policy()
    assert x["operator"]=="AG7SU" and x["dummy_load"] is True
    assert x["permitted_kinds"]==["RX_ONLY","BOUNDED_TX"]
    assert x["max_sessions_per_day"]<=8
    assert x["rf_session_limit_ms"]<=1900


@pytest.mark.parametrize("change",[
    {"dummy_load":False},
    {"operator":"attacker"},
    {"permitted_kinds":["UNBOUNDED_TX"]},
    {"max_sessions_per_day":999},
    {"rf_session_limit_ms":50000},
    {"exact_api":31},
])
def test_policy_rejects_unsafe_mutations(tmp_path,change):
    data=p.policy()
    data.update(change)
    f=tmp_path/"policy.json"
    f.write_text(json.dumps(data))
    with pytest.raises(p.Refused):
        p.policy(f)


def test_atomic_private_write(tmp_path):
    dest=tmp_path/"secret"/"manifest.json"
    p.atomic(dest,{"a":1})
    assert json.loads(dest.read_text())=={"a":1}
    assert dest.stat().st_mode & 0o777==0o600


def test_unranked_issue_must_not_register_without_owner_queue(monkeypatch):
    monkeypatch.setattr(p,"ranked",lambda reg,n:False)
    with pytest.raises(p.Refused,match="coordination priority"):
        p.ensure_ranked({"repo":"tcarwash/flux_radio"},21,write=False)


def test_generated_local_config_init_is_safe(tmp_path,monkeypatch):
    original=tmp_path/"original.json"
    current=tmp_path/"generated.json"
    standing=tmp_path/"generated-grants.json"
    original.write_text(json.dumps({
       "schema":1,"repo":"tcarwash/flux_radio","queue_issue":11,
       "trusted_author":"tcarwash","manifests":{}}))
    monkeypatch.setattr(p,"HARDWARE",original)
    monkeypatch.setattr(p,"GENERATED",current)
    monkeypatch.setattr(p,"PERMISSIONS",standing)
    monkeypatch.setattr(p,"STANDING",tmp_path/"source-grants.json")
    src={"schema":1,"enabled":True,"trusted_operator":"AG7SU",
         "environment":"dummy_load","approved":{}}
    (tmp_path/"source-grants.json").write_text(json.dumps(src))
    assert p.ensure_generated()["manifests"]=={}
    assert json.loads(standing.read_text())["approved"]=={}


def test_bounded_physical_manifest_is_not_a_code_writing_grant(tmp_path,monkeypatch):
    worktree=tmp_path/"worktree"
    for folder in ("tools","c/k6/mod","c/k6","modem"):
        (worktree/folder).mkdir(parents=True,exist_ok=True)
    c=(worktree/"c/k6/mod/tx28_fastspi_probe.c")
    c.write_text("#define K6_MODULE_LEVEL 2\n")
    script=worktree/"tools/k6_tx28_fastspi_rx_bench.py"
    script.write_text(
        "--preflight-only --verify-only --run --prefix "
        "--dummy-load-confirmed --operator-lease-confirmed "
        "--queue-lease-acknowledged"
    )
    # Only for fake-generated manifest tests: let the real batch validator
    # check schema, while replacing pins of repo tooling not in tmp fixture.
    changed=["tools/k6_tx28_fastspi_rx_bench.py",
             "c/k6/mod/tx28_fastspi_probe.c"]
    record={"worktree":str(worktree),"changed_files":changed,
            "pins":[{"path":x,"sha256":p.sha(worktree/x)} for x in changed],
            "tests_log_sha256":"a"*64}
    def fake_status(path,allowed):
        return changed
    monkeypatch.setattr(b,"candidate_files",fake_status)
    monkeypatch.setattr(p.batch,"check_command_pins",lambda x:None)
    monkeypatch.setattr(p.batch,"_check_pins",lambda x:None)
    monkeypatch.setattr(p.batch,"read_api_config",lambda p=None:({"target_module_api":29},b""))
    monkeypatch.setattr(p.batch,"check_api_compatibility",lambda m,c:[])
    monkeypatch.setattr(p,"SESSION_PROBE",ROOT/"tools/k6_experiment_bench_probe.py")
    result=p.generate(21,b.config_load()["recipes"]["21"],record,p.policy())
    assert result["jobs"][0]["minimum_module_api"]==2
    assert result["port"]=="/dev/ttyUSB0"
    assert result["jobs"][0]["run"]["timeout_seconds"]<=120
    assert "--dummy-load-confirmed" in result["jobs"][0]["run"]["argv"]
    assert result["session_preflight"]["argv"][-2]=="--output"
    assert "session-before.json" in result["session_restore"]["argv"][-1]
    assert result["jobs"][0]["issue_url"].endswith("/21")
    assert len(result["jobs"])==1


def test_no_automatic_grant_without_review(monkeypatch,tmp_path):
    # Confirm grants are *not* inferred from a successful offline build.
    cfg=tmp_path/"cfg.json"
    cfg.write_text(json.dumps({
      "schema":1,"repo":"tcarwash/flux_radio","queue_issue":11,
      "trusted_author":"tcarwash","manifests":{}}))
    grants=tmp_path/"grants.json"
    grants.write_text(json.dumps({
      "schema":1,"enabled":True,"trusted_operator":"AG7SU",
      "environment":"dummy_load","approved":{}}))
    builder_state=tmp_path/"builder.json"
    builder_state.write_text(json.dumps({"schema":1,"jobs":{}}))
    review_state=tmp_path/"reviews.json"
    review_state.write_text(json.dumps({"schema":1,"jobs":{}}))
    hw_state=tmp_path/"hardware.json"
    hw_state.write_text(json.dumps({"schema":1,"jobs":{}}))
    monkeypatch.setattr(p,"ensure_generated",lambda:p.hardware.load_config(cfg))
    got=p.grant_once(builder_state=builder_state,hardware_config=cfg,
                     standing_config=grants,review_state_path=review_state,
                     hardware_state_path=hw_state)
    assert got["events"]==[]
    assert json.loads(grants.read_text())["approved"]=={}
