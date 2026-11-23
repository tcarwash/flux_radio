"""TX-15V OFF/ON oscillator phase hypotheses and heldout isolation controls."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx15v_gate_phase_analyze import (
    FS,TIME_ORIGIN,NOMINAL_AUDIO_HZ,
    score_audio_phase,windows,OFF_S
)


def synthetic_f_audio(*,model:str,noise=15.,seed=117):
    onset=.907
    t=TIME_ORIGIN+np.arange(int(1.90*FS))/FS
    rng=np.random.default_rng(seed)
    f=27+rng.normal(0,noise,size=len(t))
    f0=NOMINAL_AUDIO_HZ-.10
    on,off=windows(onset)
    gap_accum=0.
    for k,(a,b) in enumerate(on):
        if k>0:gap_accum+=OFF_S[k-1]
        seg=(t>=a)&(t<b)
        if model=="free_running":
            phase=2*np.pi*f0*(t[seg]-onset)+.82
        elif model=="frozen_during_off":
            phase=2*np.pi*f0*(t[seg]-onset-gap_accum)+.82
        elif model=="reset_on_enable":
            phase=2*np.pi*f0*(t[seg]-a)+.82
        else:raise ValueError(model)
        f[seg]+=1060*np.sin(phase)
    return f,t,onset


@pytest.mark.parametrize("model",[
    "free_running","frozen_during_off","reset_on_enable"
])
def test_synthetic_on_off_phase_model_discrimination(model):
    f,t,onset=synthetic_f_audio(model=model)
    r=score_audio_phase(f,t,onset)
    assert r["on0_and_on1_only_for_calibration"]
    assert r["audio_on_off_envelope_verified"]
    assert r["phase_model_qualified"],r
    assert r["best_model_exploratory_label"]==model,r
    assert r["models"][model]["heldout_phase_rms_deg"]<8
    assert r["pilot_only_fitted_audio_frequency_hz"]==pytest.approx(
        NOMINAL_AUDIO_HZ-.10,abs=.08)


def test_holdout_audio_cannot_influence_calibrated_source_rate():
    f,t,onset=synthetic_f_audio(model="free_running")
    a=score_audio_phase(f,t,onset)
    changed=f.copy()
    on,off=windows(onset)
    for x,y in on[2:]:
        mask=(t>=x)&(t<y)
        changed[mask]+=850*np.cos(2*np.pi*(NOMINAL_AUDIO_HZ-.10)*(t[mask]-onset)+.82)
    b=score_audio_phase(changed,t,onset)
    assert a["pilot_only_fitted_audio_frequency_hz"]==b[
        "pilot_only_fitted_audio_frequency_hz"]
    assert a["pilot_after_first_gate_global_tone_phase_deg"]==b[
        "pilot_after_first_gate_global_tone_phase_deg"]
    assert b["models"]["free_running"]["heldout_phase_rms_deg"]>a[
        "models"]["free_running"]["heldout_phase_rms_deg"]


def test_analyzer_has_no_radio_hardware_commands():
    src=(ROOT/"tools/k6_tx15v_gate_phase_analyze.py").read_text()
    for forbidden in ("import serial","RtlCapture(","run_module(",
                      "tx_on(","fd_write(","mutate_library("):
        assert forbidden not in src
    assert "independent_carrier_iq_phase_dac_proven=False" in src


@pytest.mark.parametrize("run",["01","02"])
def test_physical_pa_on_if_capture_exists(run):
    p=Path.home()/(
        "flux-bench/tx-injection-20261122/tone-oscillator/"
        f"tx15v-gate-1-run{run}.npy")
    if not p.exists():pytest.skip("TX15V physical recording not yet made")
    from k6_tx15v_gate_phase_analyze import analyze_file
    r=analyze_file(p)
    s=r["gate_phase"]
    assert s["audio_on_off_envelope_verified"]
    assert s["on0_and_on1_only_for_calibration"]
    assert s["pilot_only_fitted_audio_frequency_hz"]>1000
    assert s["phase_model_qualified"]
    assert s["best_model_exploratory_label"]=="free_running"
    assert s["models"]["free_running"]["heldout_phase_rms_deg"]<1
    assert s["models"]["frozen_during_off"]["heldout_phase_rms_deg"]>80
    assert s["models"]["reset_on_enable"]["heldout_phase_rms_deg"]>35
    assert r["raw_rf_iq_sha256"]
