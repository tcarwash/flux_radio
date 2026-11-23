"""TX-24 READ-ONLY FM deviation scorer: synthetic positive/negative tests.

These are not hardware observations; no serial/SDR file access is performed.
"""
import sys
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx24_reg40_deviation_analyze import (
    LOW8,NOMINAL_HZ,PILOT,HELDOUT,STEP_SECONDS,
    score_audio,pilot_fit,
)

def fixture(*,model="monotonic",samp=8000,offset=0.0):
    rng=np.random.default_rng(431)
    t=np.arange(0,len(LOW8)*STEP_SECONDS,1/samp)+offset
    y=rng.normal(0,33,len(t))
    fre=NOMINAL_HZ+.067
    gain={0x00:.92,0x16:1.00,0x40:1.15,0x80:1.38}
    if model=="ignored":
        gain={x:1. for x in gain}
    if model=="reverse":
        gain={0x00:1.5,0x16:1.0,0x40:1.2,0x80:.7}
    if model=="weak":
        gain={x:.001 for x in gain}
    for i,word in enumerate(LOW8):
        m=(t>=offset+i*STEP_SECONDS)&(
            t<offset+(i+1)*STEP_SECONDS)
        a=gain[word]*1250.
        if model=="control_drift" and i>=6 and word==0x16:
            a*=1.4
        if model=="replay_unstable" and i==9:
            a*=.5
        phase=.2
        if model=="phase_jump" and i>=6:
            phase+=1.0
        y[m]+=a*np.sin(2*np.pi*fre*(t[m]-offset)+phase)
    return y,t

def test_all_slots_and_pilot_are_predeclared():
    assert len(LOW8)==13
    assert PILOT==(0,2)
    assert len(HELDOUT)==11
    assert {0x00,0x16,0x40,0x80}==set(LOW8)
    assert LOW8.count(0x16)==7
    assert LOW8.count(0x00)==2
    assert LOW8.count(0x40)==2
    assert LOW8.count(0x80)==2
    assert STEP_SECONDS==.090

def test_synthetic_four_deviation_scales_pass():
    f,t=fixture()
    x=score_audio(f,t,0)
    assert x["candidate_deviation_actuator_qualified"],x["conditions"]
    assert all(x["conditions"].values())
    assert abs(x["pilot_only_frequency_phase_and_amplitude_fit"]["frequency_hz"]
               -(NOMINAL_HZ+.067))<.1
    scale=x["ratios_by_lowbyte"]
    assert abs(scale["0"]-.92)<.035
    assert abs(scale["22"]-1.0)<.035
    assert abs(scale["64"]-1.15)<.035
    assert abs(scale["128"]-1.38)<.035
    assert x["no_heldout_used_for_parameter_fit"]
    assert x["slots"][0]["pilot"] and x["slots"][2]["pilot"]

@pytest.mark.parametrize("failure,key",[
    ("ignored","deviation_amplitude_monotonic"),
    ("reverse","deviation_amplitude_monotonic"),
    ("control_drift","repeated_baseline_stable"),
    ("replay_unstable","repeated_nonbaseline_levels_stable"),
    ("phase_jump","repeated_baseline_phase_stable"),
])
def test_synthetic_negative_controls_fail(failure,key):
    f,t=fixture(model=failure)
    result=score_audio(f,t,0.)
    assert not result["conditions"][key]
    assert not result["candidate_deviation_actuator_qualified"]

def test_weak_pilot_rejected():
    f,t=fixture(model="weak")
    with pytest.raises(ValueError,match="insufficient pilot"):
        score_audio(f,t,0)

def test_pilot_fit_does_not_use_holdout_amplitudes_or_phases():
    f,t=fixture()
    other=f.copy()
    rng=np.random.default_rng(9912)
    # Pilot indices 0 and 2 are the ONLY allowed training windows.
    for i in HELDOUT:
        m=(t>=i*STEP_SECONDS)&(t<(i+1)*STEP_SECONDS)
        other[m]=rng.normal(0,1000,int(m.sum()))
    first=score_audio(f,t,0.)
    second=score_audio(other,t,0.)
    assert first["pilot_only_frequency_phase_and_amplitude_fit"]==(
        second["pilot_only_frequency_phase_and_amplitude_fit"])
    assert not second["candidate_deviation_actuator_qualified"]

def test_scoring_with_nonzero_onset_and_scaled_timestamps():
    f,t=fixture(offset=1.0)
    scored=score_audio(f,t,1.)
    assert scored["candidate_deviation_actuator_qualified"]

def test_truncation_and_mismatched_arrays_raise():
    f,t=fixture()
    with pytest.raises(ValueError,match="lengths differ"):
        score_audio(f[:-1],t,0.)
    with pytest.raises(ValueError,match="missing T1 pilot"):
        score_audio(f[:400],t[:400],0.)

def test_source_is_read_only_analyzer():
    content=(ROOT/"tools/k6_tx24_reg40_deviation_analyze.py").read_text()
    assert "import serial" not in content
    assert "k6_port_lock" not in content
    assert "RtlCapture(" not in content
    assert "score_audio" in content
