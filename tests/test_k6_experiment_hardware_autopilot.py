"""Mocked whole-bench autonomous execution. Absolutely no physical I/O."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
SPEC=importlib.util.spec_from_file_location("k6_experiment_hardware_autopilot",ROOT/"tools/k6_experiment_hardware_autopilot.py")
a=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


def permit():
    return {"schema":1,"enabled":True,"trusted_operator":"AG7SU","environment":"dummy_load",
            "approved":{"9":{
                "manifest_sha256":"a"*64,
                "expected_build":"339e08a2b77c7679",
                "exact_api":29,"expires_utc":"2026-11-25T23:00:00Z",
                "one_shot":True,"dummy_load_confirmed":True,"no_cwid_for_dummy_load":True,
            }}}


def test_invalid_permission_fails_closed(tmp_path):
    p=tmp_path/"perm.json"
    p.write_text(json.dumps(permit()))
    assert a.standing_policy(p)["approved"]["9"]["exact_api"]==29
    for field,value in [("one_shot",False),("dummy_load_confirmed",False),
                        ("exact_api",True),("manifest_sha256","untrusted"),
                        ("expected_build","bogus")]:
        bad=permit();bad["approved"]["9"][field]=value
        p.write_text(json.dumps(bad))
        with pytest.raises(a.batch.Refused):a.standing_policy(p)


def fake_setup(monkeypatch):
    config={"repo":"tcarwash/flux_radio","queue_issue":11,"trusted_author":"tcarwash"}
    monkeypatch.setattr(a.hw,"load_config",lambda _:config)
    monkeypatch.setattr(a,"standing_policy",lambda _:permit())
    monkeypatch.setattr(a.queue,"check_github",lambda *_,**__: [9])
    monkeypatch.setattr(a,"github_identity",lambda _:None)
    monkeypatch.setattr(a,"require_unclaimed_bench",lambda _:None)
    monkeypatch.setattr(a.hw,"ack_comment",lambda *_,**__:None)
    manifest={"session_id":"mocked-lab-session"}
    monkeypatch.setattr(a,"secure_approval",lambda *_:(manifest,b"manifest bytes","d"*64))
    return manifest


def test_one_shot_success_and_durable_dedup(monkeypatch,tmp_path):
    fake_setup(monkeypatch)
    actions=[]
    monkeypatch.setattr(a,"queue_comment",lambda cfg,body:
      actions.append(("comment",body.splitlines()[0])) or "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-999")
    monkeypatch.setattr(a.batch,"run_session",lambda *args,**kwargs:
      actions.append(("hardware","mock")) or {"result":"PASS","session_id":"mocked-lab-session","jobs":[]})
    monkeypatch.setattr(a.hw,"publish",lambda *args:actions.append(("publish","mock")))
    state=tmp_path/"journal.json"
    out=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                    bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert out["events"][0]["result"]=="PASS"
    assert sum(action=="hardware" for action,_ in actions)==1
    assert any("PASS / RELEASE" in info for action,info in actions if action=="comment")
    out2=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                     bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert out2["events"][0]["action"]=="NO_AUTO_RETRY"
    assert sum(action=="hardware" for action,_ in actions)==1


def test_failure_never_auto_repeats_and_holds_bench(monkeypatch,tmp_path):
    fake_setup(monkeypatch)
    actions=[]
    monkeypatch.setattr(a,"queue_comment",lambda cfg,body:
      actions.append(body.splitlines()[0]) or "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-999")
    def fails(*_,**__):
        actions.append("attempted")
        return {"result":"BLOCKED","session_id":"mocked-lab-session","jobs":[]}
    monkeypatch.setattr(a.batch,"run_session",fails)
    state=tmp_path/"journal.json"
    out=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                    bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert out["events"][0]["result"]=="BLOCKED"
    assert any(x.startswith("BLOCKED / HOLD") for x in actions)
    a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert actions.count("attempted")==1


def test_unapproved_delegation_does_not_post_or_touch_bench(monkeypatch,tmp_path):
    fake_setup(monkeypatch)
    monkeypatch.setattr(a,"secure_approval",lambda *args:(_ for _ in ()).throw(a.batch.Refused("review pending")))
    monkeypatch.setattr(a,"queue_comment",lambda *args:pytest.fail("no GitHub writes allowed"))
    monkeypatch.setattr(a.batch,"run_session",lambda *args,**kwargs:pytest.fail("no hardware allowed"))
    out=a.one_cycle(state_path=tmp_path/"state.json",review_path=tmp_path/"review.json",
                    bench_lock=tmp_path/"bench.lock")
    assert out["events"][0]["action"]=="WAITING_FOR_LOCAL_APPROVAL"



def test_failed_experiment_releases_only_after_independent_global_postflight(monkeypatch,tmp_path):
    fake_setup(monkeypatch)
    actions=[]
    monkeypatch.setattr(a,"queue_comment",lambda cfg,body:
        actions.append(body.splitlines()[0]) or
        "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-999")
    verified={"result":"BLOCKED","session_id":"mocked-lab-session","jobs":[],
              "events":[
                {"name":"session_preflight","status":"PASS","returncode":0},
                {"name":"job01-run","status":"FAIL","returncode":1},
                {"name":"session_restore","status":"PASS","returncode":0},
                {"name":"session_verify","status":"PASS","returncode":0}]}
    monkeypatch.setattr(a.batch,"run_session",lambda *_,**__:verified)
    out=a.one_cycle(state_path=tmp_path/"journal.json",
                    review_path=tmp_path/"review.json",
                    bench_lock=tmp_path/"bench.lock",
                    output_root=tmp_path/"records")
    assert out["events"][0]["result"]=="BLOCKED"
    assert a.postflight_verified(verified)
    assert any(x.startswith("BLOCKED / RELEASE") for x in actions)
    assert not any(x.startswith("BLOCKED / HOLD") for x in actions)
    assert not a.postflight_verified({**verified,"events":verified["events"][:-1]})
    assert not a.postflight_verified({
        **verified,"events":[{"name":"session_restore","status":"PASS","returncode":0},
                            {"name":"session_verify","status":"FAIL","returncode":1}]})
    assert not a.postflight_verified({"result":"BLOCKED","events":[]})
