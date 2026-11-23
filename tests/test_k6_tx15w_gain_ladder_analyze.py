"""TX15W pilot-only gain/deviation and tone-phase regression tests."""
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx15w_gain_ladder_analyze import (
  FS,TIME_ORIGIN,GAINS,SLOT_S,FREQ_NOMINAL,score_audio,
  MAX_CONTROL_VARIATION_FRACTION,
)
BENCH=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"

def synthetic(*,monotonic=True,phase_reset=False,seed=234):
    onset=.915
    t=TIME_ORIGIN+np.arange(round(1.9*FS))/FS
    rng=np.random.default_rng(seed)
    f=19+rng.normal(0,28,len(t))
    rate=FREQ_NOMINAL-.08
    for i,g in enumerate(GAINS):
        mask=(t>=onset+i*SLOT_S)&(t<onset+(i+1)*SLOT_S)
        if not mask.any():continue
        amp=42*(g if monotonic else 24)
        # RESET only on repeated control 24 after segment2;
        # their phase residual should expose the defect.
        j=(.9 if phase_reset and i>=4 else 0.)
        f[mask]+=amp*np.sin(2*np.pi*rate*(t[mask]-onset)+.7+j)
    return f,t,onset


def test_known_gain_ladder_with_oscillator_phase_continuity():
    f,t,onset=synthetic()
    s=score_audio(f,t,onset)
    assert s["phase_training_uses_heldout"] is False
    assert s["gain_values_monotonic_with_at_least_5pct_separation"]
    assert s["repeated_gain24_phase_stable"]
    assert s["qualifies_as_phase_preserving_monotonic_gain"]
    assert abs(s["pilot_only_fitted_hz"]-(FREQ_NOMINAL-.08))<.08
    assert s["postpilot_control_phase_rms_deg"]<4
    assert s["gain24_max_min_spread_fraction"]<.03


def test_flat_gain_is_not_classified_as_modulation_control():
    f,t,onset=synthetic(monotonic=False)
    s=score_audio(f,t,onset)
    assert not s["gain_values_monotonic_with_at_least_5pct_separation"]
    assert not s["qualifies_as_phase_preserving_monotonic_gain"]


def test_gain_phase_change_is_not_classified_phase_preserving():
    f,t,onset=synthetic(phase_reset=True)
    s=score_audio(f,t,onset)
    assert s["gain_values_monotonic_with_at_least_5pct_separation"]
    assert not s["repeated_gain24_phase_stable"]
    assert s["postpilot_control_phase_rms_deg"]>20


def test_holdout_changes_cannot_affect_pilot_fitted_oscillator():
    f,t,onset=synthetic()
    a=score_audio(f,t,onset)
    altered=f.copy()
    selected=(t>=onset+6*SLOT_S)&(t<onset+7*SLOT_S)
    altered[selected]+=500*np.cos(
        2*np.pi*(FREQ_NOMINAL-.08)*(t[selected]-onset))
    b=score_audio(altered,t,onset)
    assert a["pilot_only_fitted_hz"]==b["pilot_only_fitted_hz"]
    assert a["pilot_only_phase_reference_deg"]==b[
        "pilot_only_phase_reference_deg"]
    assert b["postpilot_control_phase_rms_deg"]>a[
        "postpilot_control_phase_rms_deg"]+3


def test_offline_tool_never_opens_radio():
    code=(ROOT/"tools/k6_tx15w_gain_ladder_analyze.py").read_text()
    for forbidden in ("import serial","RtlCapture(","open_port(",
                      "run_module(","fd_write(","tx_on(", "mutate_library("):
        assert forbidden not in code


@pytest.mark.parametrize("run",["01","02"])
def test_real_rf_if_recorded(run):
    p=BENCH/f"tx15w-gain-1-run{run}.npy"
    if not p.exists():pytest.skip("physical TX15W not recorded")
    from k6_tx15w_gain_ladder_analyze import analyze_file
    r=analyze_file(p)
    assert r["read_only"] and r["source_iq_sha256"]
    assert len(r["score"]["gain_phase_pairs"])==13
    assert r["score"]["phase_training_uses_heldout"] is False
