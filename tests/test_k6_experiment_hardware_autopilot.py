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


def test_manual_issue_filter_never_runs_other_issue(monkeypatch,tmp_path):
    fake_setup(monkeypatch)
    monkeypatch.setattr(a.batch,"run_session",lambda *args,**kwargs:pytest.fail("hardware not approved"))
    r=a.one_cycle(state_path=tmp_path/"hardware.json",
                  review_path=tmp_path/"review.json",
                  bench_lock=tmp_path/"bench.lock",
                  issue_filter=29)
    assert r["mode"]=="HARDWARE_ISSUE_NOT_APPROVED"
    assert r["issue"]==29


def test_attempt_limit_blocks_new_manifest_on_same_issue(monkeypatch,tmp_path):
    fake_setup(monkeypatch)
    monkeypatch.setattr(a.batch,"run_session",lambda *args,**kwargs:pytest.fail("limit bypassed"))
    jobs={f"issue-9:{n:064x}":{"state":"BLOCKED_NO_AUTO_RETRY","when":f"2026-11-24T0{n}:00Z"}
          for n in range(1,4)}
    state=tmp_path/"hardware.json"
    state.write_text(json.dumps({"schema":1,"jobs":jobs}))
    r=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                  bench_lock=tmp_path/"bench.lock")
    assert r["events"]==[{"issue":9,"action":"ATTEMPT_LIMIT_REACHED",
                         "attempts":3,"max_attempts":3}]


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


def _sample_manifest(tmp_path,issue,expires="2026-11-25T23:00:00+00:00"):
    pin_file=tmp_path/f"script_{issue}.py"
    pin_file.write_text(f"# issue {issue}\n")
    pin_sha=hashlib.sha256(pin_file.read_bytes()).hexdigest()
    probe_file=tmp_path/"probe.py"
    if not probe_file.exists():
        probe_file.write_text("# probe\n")
    probe_sha=hashlib.sha256(probe_file.read_bytes()).hexdigest()
    before=str(tmp_path/f"single-{issue}"/"session-before.json")
    prefix=str(tmp_path/f"single-{issue}"/"auto-bench")
    base=["/usr/bin/python3",str(probe_file),"--expected-build","339e08a2b77c7679",
          "--exact-api","29","--include-slots"]
    return {
        "version":1,
        "session_id":f"auto-{issue}-20261124T230000Z",
        "port":"/dev/ttyUSB0",
        "queue_issue_url":"https://github.com/tcarwash/flux_radio/issues/11",
        "lease_expires_utc":expires,
        "session_pins":[{"path":str(probe_file),"sha256":probe_sha}],
        "session_preflight":{"argv":base+["--output",before],"timeout_seconds":60},
        "session_restore":{"argv":base+["--baseline",before],"timeout_seconds":60},
        "session_verify":{"argv":base+["--baseline",before],"timeout_seconds":60},
        "jobs":[{
            "id":f"auto-{issue}-bounded-one-shot",
            "issue_url":f"https://github.com/tcarwash/flux_radio/issues/{issue}",
            "minimum_module_api":2,
            "cwd":str(tmp_path),
            "pins":[{"path":str(pin_file),"sha256":pin_sha}],
            "preflight":{"argv":["/usr/bin/python3",str(pin_file),"--preflight-only"],"timeout_seconds":30},
            "run":{"argv":["/usr/bin/python3",str(pin_file),"--run","--prefix",prefix],"timeout_seconds":60},
            "restore":{"argv":["/usr/bin/python3",str(pin_file),"--verify-only","--prefix",prefix],"timeout_seconds":30},
            "verify":{"argv":["/usr/bin/python3",str(pin_file),"--verify-only","--prefix",prefix],"timeout_seconds":30},
        }],
    }


def _setup_multi(monkeypatch,tmp_path,issues):
    config={"repo":"tcarwash/flux_radio","queue_issue":11,"trusted_author":"tcarwash"}
    manifests={}
    approved={}
    for idx,n in enumerate(issues):
        m=_sample_manifest(tmp_path,n,expires=f"2026-11-25T2{idx}:00:00+00:00")
        raw=json.dumps(m).encode()
        sha=hashlib.sha256(raw).hexdigest()
        manifests[n]=(m,raw,f"{n:064x}")
        approved[str(n)]={
            "manifest_sha256":sha,
            "expected_build":"339e08a2b77c7679",
            "exact_api":29,"expires_utc":"2026-11-25T23:00:00Z",
            "one_shot":True,"dummy_load_confirmed":True,"no_cwid_for_dummy_load":True,
        }
    monkeypatch.setattr(a.hw,"load_config",lambda _:config)
    monkeypatch.setattr(a,"standing_policy",lambda _:{
        "schema":1,"enabled":True,"trusted_operator":"AG7SU",
        "environment":"dummy_load","approved":approved})
    monkeypatch.setattr(a.queue,"check_github",lambda *_,**__:list(issues))
    monkeypatch.setattr(a,"github_identity",lambda _:None)
    monkeypatch.setattr(a,"require_unclaimed_bench",lambda _:None)
    monkeypatch.setattr(a.hw,"ack_comment",lambda *_,**__:None)
    monkeypatch.setattr(a,"secure_approval",lambda cfg,n,grant,rev:manifests[n])
    return approved


def test_bundles_multiple_approved_experiments_into_one_bench_reserve(monkeypatch,tmp_path):
    approved=_setup_multi(monkeypatch,tmp_path,[20,26])
    comments=[]
    published=[]
    runs=[]
    monkeypatch.setattr(a,"queue_comment",lambda cfg,body:
        comments.append(body.splitlines()[0]) or
        "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-777")
    monkeypatch.setattr(a.hw,"publish",lambda cfg,n,res,app:published.append((n,res["result"])))
    def fake_run(m,raw,digest,**kwargs):
        runs.append(m)
        return {"result":"PASS","session_id":m["session_id"],
                "jobs":[{"id":j["id"],"issue_url":j["issue_url"],"result":"PASS"} for j in m["jobs"]],
                "events":[{"name":"session_restore","status":"PASS","returncode":0},
                          {"name":"session_verify","status":"PASS","returncode":0}]}
    monkeypatch.setattr(a.batch,"run_session",fake_run)
    state=tmp_path/"journal.json"
    out=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                    bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert len(runs)==1
    bundled=runs[0]
    assert bundled["session_id"].startswith("bundle-20-26-")
    assert [j["id"] for j in bundled["jobs"]]==["auto-20-bounded-one-shot","auto-26-bounded-one-shot"]
    assert bundled["lease_expires_utc"]=="2026-11-25T20:00:00+00:00"
    # Baseline and prefixes are rewritten inside the bundle directory.
    bundle_dir=str(tmp_path/"records"/bundled["session_id"])
    assert bundled["session_preflight"]["argv"][-1]==f"{bundle_dir}/session-before.json"
    assert bundled["jobs"][0]["run"]["argv"][-1].startswith(f"{bundle_dir}/issue-20-")
    assert bundled["jobs"][1]["run"]["argv"][-1].startswith(f"{bundle_dir}/issue-26-")
    assert any(c.startswith("BENCH ACK | issues=20,26 |") for c in comments)
    assert any(c.startswith("PASS / RELEASE") and "#20" in c and "#26" in c for c in comments)
    assert published==[(20,"PASS"),(26,"PASS")]
    assert [e["result"] for e in out["events"]]==["PASS","PASS"]
    saved=json.loads(state.read_text())["jobs"]
    for n in (20,26):
        rec=saved[f"issue-{n}:{approved[str(n)]['manifest_sha256']}"]
        assert rec["state"]=="PASS"
        assert rec["bundled_issues"]==[20,26]
        assert len(rec["bundle_manifest_sha256"])==64
    # Second cycle must not retry either issue.
    out2=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                     bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert len(runs)==1
    assert all(e["action"]=="NO_AUTO_RETRY" for e in out2["events"])


def test_bundle_mid_failure_verified_restore_preserves_pass_and_releases_unstarted(monkeypatch,tmp_path):
    approved=_setup_multi(monkeypatch,tmp_path,[20,26,30])
    comments=[]
    published=[]
    monkeypatch.setattr(a,"queue_comment",lambda cfg,body:
        comments.append(body.splitlines()[0]) or
        "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-778")
    monkeypatch.setattr(a.hw,"publish",lambda cfg,n,res,app:published.append((n,res["result"])))
    def fake_run(m,raw,digest,**kwargs):
        return {"result":"BLOCKED","session_id":m["session_id"],
                "jobs":[
                    {"id":m["jobs"][0]["id"],"issue_url":m["jobs"][0]["issue_url"],"result":"PASS"},
                    {"id":m["jobs"][1]["id"],"issue_url":m["jobs"][1]["issue_url"],"result":"BLOCKED"},
                ],
                "events":[{"name":"session_restore","status":"PASS","returncode":0},
                          {"name":"session_verify","status":"PASS","returncode":0}]}
    monkeypatch.setattr(a.batch,"run_session",fake_run)
    state=tmp_path/"journal.json"
    out=a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                    bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert any(c.startswith("BLOCKED / RELEASE") for c in comments)
    assert published==[(20,"PASS")]
    saved=json.loads(state.read_text())["jobs"]
    assert saved[f"issue-20:{approved['20']['manifest_sha256']}"]["state"]=="PASS"
    assert saved[f"issue-26:{approved['26']['manifest_sha256']}"]["state"]=="BLOCKED_NO_AUTO_RETRY"
    # Issue 30 never touched hardware and session restore+verify passed, so it stays eligible.
    assert f"issue-30:{approved['30']['manifest_sha256']}" not in saved
    assert out["events"][2]["action"]=="SKIPPED_AFTER_EARLIER_FAILURE"


def test_bundle_unverified_restore_holds_bench_and_blocks_all(monkeypatch,tmp_path):
    approved=_setup_multi(monkeypatch,tmp_path,[20,26])
    comments=[]
    monkeypatch.setattr(a,"queue_comment",lambda cfg,body:
        comments.append(body.splitlines()[0]) or
        "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-779")
    monkeypatch.setattr(a.hw,"publish",lambda *_:pytest.fail("must not publish on unverified hold"))
    monkeypatch.setattr(a.batch,"run_session",lambda m,raw,digest,**kwargs:{
        "result":"BLOCKED","session_id":m["session_id"],
        "jobs":[{"id":m["jobs"][0]["id"],"issue_url":m["jobs"][0]["issue_url"],"result":"PASS"}],
        "events":[{"name":"session_restore","status":"FAIL","returncode":1}]})
    state=tmp_path/"journal.json"
    a.one_cycle(state_path=state,review_path=tmp_path/"review.json",
                bench_lock=tmp_path/"bench.lock",output_root=tmp_path/"records")
    assert any(c.startswith("BLOCKED / HOLD") for c in comments)
    saved=json.loads(state.read_text())["jobs"]
    assert saved[f"issue-20:{approved['20']['manifest_sha256']}"]["state"]=="BLOCKED_NO_AUTO_RETRY"
    assert saved[f"issue-26:{approved['26']['manifest_sha256']}"]["state"]=="BLOCKED_NO_AUTO_RETRY"

