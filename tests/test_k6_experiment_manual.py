"""Manual takeover does not bypass the shared supervised RF controller."""
from pathlib import Path
import json
from types import SimpleNamespace
import sys
import subprocess

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import k6_experiment_manual as manual


def test_takeover_select_issue_run_and_resume(monkeypatch,tmp_path):
    timers={x:True for x in manual.TIMERS}
    calls=[]
    def fake_systemctl(*argv):
        if argv[0]=="show":
            unit=argv[-1]
            if unit in timers:
                return "active" if timers[unit] else "inactive"
            return "inactive"
        calls.append(argv)
        action,unit=argv
        timers[unit]=(action=="start")
        return ""
    monkeypatch.setattr(manual,"systemctl",fake_systemctl)
    def fake_run(argv,**kw):
        assert argv[0]=="/usr/bin/python3"
        assert "k6_experiment_hardware_autopilot.py" in argv[1]
        assert argv[-2:]==["--issue","29"]
        return subprocess.CompletedProcess(argv,0)
    monkeypatch.setattr(manual.subprocess,"run",fake_run)
    state=tmp_path/"manual.json"
    args=SimpleNamespace(command="takeover",owner="research-agent",state=state)
    takeover=manual.command(args)
    assert takeover["mode"]=="MANUAL"
    assert not any(timers.values())
    assert json.loads(state.read_text())["owner"]=="research-agent"
    run=manual.command(SimpleNamespace(command="run",stage="hardware",issue=29,
                         lease=takeover["lease"],state=state))
    assert run["exit_code"]==0 and run["stage"]=="hardware"
    assert not any(timers.values())
    release=manual.command(SimpleNamespace(command="release",lease=takeover["lease"],state=state))
    assert release["mode"]=="SCHEDULED"
    assert all(timers.values())
    assert not state.exists()


def test_only_lease_holder_can_trigger_manual_run(monkeypatch,tmp_path):
    timers={x:False for x in manual.TIMERS}
    def fake_sys(*args):
        if args[0]=="show":return "inactive"
        return ""
    monkeypatch.setattr(manual,"systemctl",fake_sys)
    state=tmp_path/"owner.json"
    manual.write_owner({"schema":1,"owner":"agent-1","lease":"secret",
                        "original_timers":timers},state)
    with pytest.raises(manual.Refused,match="lease mismatch"):
        manual.command(SimpleNamespace(command="run",stage="hardware",issue=20,
                                       lease="other",state=state))
    with pytest.raises(manual.Refused,match="lease mismatch"):
        manual.command(SimpleNamespace(command="release",lease="other",state=state))


def test_takeover_refuses_when_worker_active(monkeypatch,tmp_path):
    def fake_sys(*a):
        if a[0]=="show":
            return "activating" if a[-1]==manual.SERVICES[0] else "inactive"
        raise AssertionError("must not stop timer while a service is running")
    monkeypatch.setattr(manual,"systemctl",fake_sys)
    with pytest.raises(manual.Refused,match="still running"):
        manual.command(SimpleNamespace(command="takeover",owner="someone",
                                      state=tmp_path/"state.json"))


def test_manual_unsafe_arguments_are_rejected(monkeypatch,tmp_path):
    monkeypatch.setattr(manual,"systemctl",
        lambda *args: "inactive" if args[0]=="show" else "")
    state=tmp_path/"manual.json"
    manual.write_owner({"schema":1,"owner":"a","lease":"key",
                        "original_timers":{}},state)
    with pytest.raises(manual.Refused,match="only applies"):
        manual.command(SimpleNamespace(command="run",stage="review",issue=9,
                                       lease="key",state=state))
