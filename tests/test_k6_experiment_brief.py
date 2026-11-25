"""An owner-authored paragraph can bootstrap isolated candidate generation."""
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import k6_experiment_brief as brief
import k6_experiment_builder as builder


def issue(n=81,owner="tcarwash",state="OPEN",title="[Flux RF][EXPERIMENT] Observe tone gain",
          body=None):
    if body is None:
        body=(
            "Goal: Determine whether changing REG40 visibly alters FM deviation on B-chip.\n"
            "Method: Compare two finite dummy-loaded FSK bursts using SDR traces.\n"
            "Firmware API: 29\nPlacement: loadable module\n"
            "Expected: One burst has a repeatable lower deviation than the other."
        )
    return {"number":n,"author":{"login":owner},"state":state,
            "title":title,"body":body,"labels":[]}


def test_owner_plain_text_brief_creates_bounded_recipe():
    doc=brief.discover(lambda:[issue()])
    assert "81" in doc["recipes"]
    b=doc["briefs"]["81"]
    r=doc["recipes"]["81"]
    assert b["api"]==29 and b["placement"]=="LOADABLE_MODULE"
    assert b["state"]=="CANDIDATE_READY"
    assert r["base_ref"]=="origin/main"
    assert r["kind"]=="BOUNDED_TX"
    assert "c/k6/mod/k6_exp_81_probe.c" in r["allowed_paths"]
    assert r["tests"]==["tests/test_k6_exp_81_bench.py"]
    assert "EXPECTED:" in r["objective"]


def test_only_owner_open_explicit_brief_discovered():
    rows=[issue(31,owner="other"),issue(32,state="CLOSED"),
          issue(33,title="[Flux RF][OFFLINE] test offline"),
          issue(34,title="[Flux RF][HARDWARE—DEFERRED] old deferred"),
          issue(35,title="[Flux RF][HARDWARE] New bench task")]
    out=brief.discover(lambda:rows)
    assert set(out["recipes"])=={"35"}


def test_flash_only_brief_kept_for_agent_without_auto_radio():
    b=issue(91,body=(
       "Goal: Test an internal firmware interrupt dispatch pathway.\n"
       "Method: compile on-flash firmware image, simulate interrupt vector.\n"
       "Firmware API: 29\nPlacement: on-flash firmware image\n"
       "Expected: interrupted code resumes RX state reliably."
    ))
    out=brief.discover(lambda:[b])
    assert out["briefs"]["91"]["state"]=="FIRMWARE_REVIEW_REQUIRED"
    assert "91" not in out["recipes"]


def test_missing_api_or_format_does_not_reject_owner_paragraph():
    b=issue(99,body=(
       "We want to observe how quickly this register settles after a small "
       "radio transmit event in the simulator; an agent should choose how "
       "to measure it. We expect a finite stable trace."
    ))
    out=brief.discover(lambda:[b])
    assert out["briefs"]["99"]["api"] is None
    assert out["recipes"]["99"]["tests"]


def test_sync_and_builder_merge_without_overriding_static(tmp_path):
    src=issue()
    out=tmp_path/"briefs.json"
    status=brief.sync(out,fetch=lambda:[src])
    assert status["ready_for_device_free_builder"]==1
    cfg=tmp_path/"builder.json"
    cfg.write_text(json.dumps({
      "schema":1,"enabled":True,"repo":brief.REPO,"max_daily":12,
      "recipes":{"21":builder.config_load(path=builder.CONFIG,brief_path=tmp_path/"none")["recipes"]["21"]}
    }))
    both=builder.config_load(path=cfg,brief_path=out)
    assert "81" in both["recipes"] and "21" in both["recipes"]
    assert both["recipes"]["81"]["base_ref"]=="origin/main"
    assert both["recipes"]["21"]["kind"]=="RX_ONLY"


def test_brief_reaches_builder_without_preexisting_static_recipe(monkeypatch,tmp_path):
    registry=tmp_path/"briefs.json"
    brief.sync(registry,fetch=lambda:[issue(93)])
    monkeypatch.setattr(builder,"BRIEF_RECIPES",registry)
    monkeypatch.setattr(builder,"WORKTREE_BASE",tmp_path)
    intake=tmp_path/"intake.json"
    intake.write_text(json.dumps({"schema":1,"jobs":{}}))
    state=tmp_path/"builder.json"
    calls=[]
    def fake_run(*args,**kwargs):
        calls.append(args)
        return "a"*40 if "rev-parse" in args else ""
    monkeypatch.setattr(builder,"run",fake_run)
    monkeypatch.setattr(builder,"supervised",lambda *args,**kwargs:17)
    result=builder.build_once(state_path=state,intake_path=intake,
                   output_root=tmp_path/"out",repo=tmp_path)
    assert result["events"][0]["issue"]==93
    assert result["physical_runs"]==0
    assert json.loads(state.read_text())["jobs"]["issue-93"]["state"]=="BLOCKED_NO_AUTO_RETRY"
    assert any("origin/main" in a for a in calls)


def test_invalid_api_and_short_body_stay_unenrolled():
    invalid=issue(87,body="Goal: hello")
    badapi=issue(88,body=("Goal: Check module compatibility with a very old "
                   "radio version in an offline emulator.\nFirmware API: 200\n"
                   "Method: compile and simulate. Expected: error."))
    result=brief.discover(lambda:[invalid,badapi])
    assert result["recipes"]=={}
    assert all(x["state"]=="BRIEF_NEEDS_DETAIL" for x in result["briefs"].values())
