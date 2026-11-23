"""Offline guard for the real operator's two distinct K6 VFO settings."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))

from tools.k6_fsk_boundary_bench import (
    INITIAL_HZ, SECONDARY_HZ, vfo_baseline_matches,
)


def actual_state():
    return dict(watching=0, transmitting=False, standby_idle=True,
                vfos=[dict(hz=440_850_000, mode="FM", wide=True, memory=False),
                      dict(hz=144_390_000, mode="FM", wide=True, memory=False)])


def test_operator_vfos_are_both_preserved():
    assert (INITIAL_HZ, SECONDARY_HZ) == (440_850_000,144_390_000)
    assert vfo_baseline_matches(actual_state())


def test_other_frequency_or_watch_is_rejected():
    for i in (0,1):
        a=actual_state()
        a["vfos"][i]["hz"]+=25000
        assert not vfo_baseline_matches(a)
    a=actual_state()
    a["watching"]=1
    assert not vfo_baseline_matches(a)


def test_other_mode_or_memory_is_rejected():
    for field,value in (("mode","AM"),("wide",False),("memory",True)):
        a=actual_state()
        a["vfos"][0][field]=value
        assert not vfo_baseline_matches(a)


def test_missing_or_unexpected_radio_state_is_rejected():
    a=actual_state()
    a["vfos"].pop()
    assert not vfo_baseline_matches(a)
    assert not vfo_baseline_matches({})
