"""Offline tests for FSK-09S-G radio-VFO snapshot preservation."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from tools.k6_fsk_boundary_bench import (
    INITIAL_HZ, SECONDARY_HZ, vfo_baseline_matches,
)


def radio_snapshot():
    return dict(watching=0, transmitting=False, sleeping=False, standby_idle=True,
                vfos=[dict(hz=440_850_000, mode="FM", wide=True, memory=False),
                      dict(hz=144_390_000, mode="FM", wide=True, memory=False)])


def test_actual_vfo_snapshot_accepted_and_distinct():
    assert INITIAL_HZ == 440_850_000
    assert SECONDARY_HZ == 144_390_000
    assert vfo_baseline_matches(radio_snapshot())


def test_old_both_144390_baseline_is_rejected():
    actual=radio_snapshot()
    actual["vfos"][0]["hz"]=144_390_000
    assert not vfo_baseline_matches(actual)


def test_unexpected_tuning_change_blocks_radio():
    for i in (0, 1):
        actual=radio_snapshot()
        actual["vfos"][i]["hz"]+=12_500
        assert not vfo_baseline_matches(actual)


def test_wrong_active_watch_or_mode_or_memory_blocks():
    for field,value in (("wide",False),("mode","AM"),("memory",True)):
        actual=radio_snapshot()
        actual["vfos"][0][field]=value
        assert not vfo_baseline_matches(actual)
    actual=radio_snapshot()
    actual["watching"]=1
    assert not vfo_baseline_matches(actual)


def test_no_other_vfo_or_truncated_snapshot_accepted():
    actual=radio_snapshot()
    actual["vfos"].pop()
    assert not vfo_baseline_matches(actual)
    assert not vfo_baseline_matches({})
