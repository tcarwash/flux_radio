"""TX-15Y prior-to-RF false-positive and independence tests; NO radio."""
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx15y_dual_tone_analyze import (
    F1_HZ,F2_HZ,DURATIONS,STATES,PILOT,HELDOUT,
    score_audio,pilot_frequency,
)

def fixture(*,broken="none",fs=8000,drift_ppm=50):
    edges=np.r_[0,np.cumsum(DURATIONS)]
    t=np.arange(0,edges[-1],1/fs)
    slot=np.searchsorted(edges[1:],t,side="right")
    noise=np.random.default_rng(17).normal(0,30,len(t))
    f=noise+9.0
    rng=np.random.default_rng(82)
    for i,state in enumerate(STATES):
        use=slot==i
        amp1=1000. if i in (0,1,3,6,7) else 0.
        amp2=750. if i in (2,3,5,7) else 0.
        if broken=="no_tone2":amp2=0.
        if broken=="same_as_t1":
            if i in (2,5):amp1=1000.
            amp2=0.
        if broken=="no_both" and i in (3,7):amp2=0.
        if broken=="tone2_leak" and i in (0,1,4,6,8):
            amp2=750.
        if broken=="unstable_tone2" and i==5:
            amp2=250.
        if broken=="mode_tone1_failure" and i==1:
            amp1=0.
        if not use.any():continue
        local=t[use]-edges[i]
        f[use]+=amp1*np.sin(2*np.pi*F1_HZ*(1+drift_ppm*1e-6)*local+
                             rng.uniform(0,2*np.pi))
        f[use]+=amp2*np.sin(2*np.pi*F2_HZ*(1+drift_ppm*1e-6)*local+
                             rng.uniform(0,2*np.pi))
    return f,t

def test_proposed_9_segment_schedule_and_pilot_holdout():
    assert len(DURATIONS)==len(STATES)==9
    assert PILOT==(0,1)
    assert HELDOUT==(2,3,4,5,6,7,8)
    assert DURATIONS[-1]==.090
    assert abs(sum(DURATIONS)-1.01)<1e-10
    assert abs(F2_HZ-F1_HZ)>400

def test_two_independent_tones_and_both_states_qualify():
    f,t=fixture()
    r=score_audio(f,t,0.)
    assert r["pilot_only_training"]
    assert r["candidate_dual_tone_qualified"]
    assert all(r["conditions"].values())
    assert abs(r["derived_from_pilot_frequencies_hz"][0]-
               F1_HZ*(1+50e-6))<.05
    assert r["derived_from_pilot_frequencies_hz"][1]==pytest.approx(
        F2_HZ*(1+50e-6),abs=.15)
    assert r["state_normalized_amplitudes"]["2"][1]>.6
    assert r["state_normalized_amplitudes"]["4"][1]<.07

@pytest.mark.parametrize("broken,failed_key",[
    ("no_tone2","tone2_heldout_present"),
    ("same_as_t1","tone1_leak_absent_in_tone2"),
    ("no_both","dual_tone_two_lines_both_segments"),
    ("tone2_leak","no_tone2_in_first_two_pilot_controls"),
    ("unstable_tone2","tone2_replay_stable")
])
def test_negative_models_not_mistaken_for_two_oscillators(broken,failed_key):
    f,t=fixture(broken=broken)
    r=score_audio(f,t,0.)
    assert not r["candidate_dual_tone_qualified"]
    assert not r["conditions"][failed_key]

def test_missing_DTMF_tone1_reference_is_rejected():
    f,t=fixture(broken="mode_tone1_failure")
    # The two pilots must independently show the reference tone;
    # a zeroed DTMF workmode pilot invalidates model training.
    # This condition is intentionally captured by the new criterion.
    with pytest.raises(ValueError,match="pilot reference"):
        score_audio(f,t,0.)

def test_no_heldout_can_change_pilot_frequency_or_gain():
    f,t=fixture()
    original=score_audio(f,t,0.)
    altered=f.copy()
    edge=sum(DURATIONS[:2])
    altered[t>=edge]=np.random.default_rng(99).normal(0,2000,sum(t>=edge))
    scored=score_audio(altered,t,0.)
    assert original["derived_from_pilot_frequencies_hz"]==scored[
        "derived_from_pilot_frequencies_hz"]
    assert original["pilot_fm_deviation_reference_hz"]==scored[
        "pilot_fm_deviation_reference_hz"]
    assert not scored["candidate_dual_tone_qualified"]

def test_offline_analyzer_is_read_only():
    src=(ROOT/"tools/k6_tx15y_dual_tone_analyze.py").read_text()
    assert "RtlCapture(" not in src and "import serial" not in src
    assert "run_module(" not in src and "mutate_library(" not in src
    assert "score_audio" in src
