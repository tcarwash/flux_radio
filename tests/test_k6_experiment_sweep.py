"""Device-free sweep validates trusted local recipe/terminal outcome invariants."""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
import k6_experiment_sweep as sweep


def test_real_sweep_has_explicit_pinned_local_tests():
    c=sweep.config_load()
    assert c["repo"]=="tcarwash/flux_radio"
    assert c["max_parallel"]>=2
    assert len(c["jobs"])>=10
    assert all(x["ref"].startswith("origin/research/") for x in c["jobs"].values())


@pytest.mark.parametrize("modify",[
    lambda c:c.update(repo="elsewhere/other"),
    lambda c:c["jobs"]["20"].update(ref="origin/main"),
    lambda c:c["jobs"]["20"].update(tests=["../../secrets.py"]),
    lambda c:c.update(max_parallel=999),
    lambda c:c.update(timeout_seconds=3600),
])
def test_untrusted_or_unbounded_recipes_rejected(tmp_path,modify):
    c=sweep.config_load()
    modify(c)
    f=tmp_path/"config.json"
    f.write_text(json.dumps(c))
    with pytest.raises(sweep.Refused):
        sweep.config_load(f)


def test_sweep_has_no_network_or_real_usb(tmp_path):
    argv=sweep.sandbox_args(tmp_path,["tests/test_sample.py"])
    assert "--dev" in argv and argv[argv.index("--dev")+1]=="/dev"
    assert "--unshare-net" in argv
    assert "--tmpfs" in argv and argv[argv.index("--tmpfs")+1]=="/tmp"
    assert argv[-2:]==["-q","tests/test_sample.py"]
    assert "/dev/ttyUSB0" not in " ".join(argv)


def test_initial_journal_and_stable_commit_key(tmp_path):
    p=tmp_path/"records.json"
    assert sweep.load_state(p)=={"schema":1,"jobs":{}}
    data={"schema":1,"jobs":{"issue-19:"+"a"*40:{"result":"PASS"}}}
    sweep.write_state(p,data)
    assert sweep.load_state(p)==data
    assert p.stat().st_mode & 0o777 == 0o600
    assert not p.with_suffix(".pending").exists()


def test_sandbox_existing_fixture_tests_are_never_reported_as_radio_pass(tmp_path):
    import textwrap
    wt=tmp_path/"worktree"
    (wt/"tests").mkdir(parents=True)
    (wt/"tests"/"test_demo.py").write_text("def test_demonstration(): assert 2+2==4\n")
    log=tmp_path/"tests.log"
    result=sweep.run_sandbox(123,"f"*40,wt,["tests/test_demo.py"],30,log)
    assert result["result"]=="PASS"
    assert result["test_passed"]==1
    assert result["rf_transmissions"]==0
    assert result["hardware_operations"]==0
    assert log.is_file()
