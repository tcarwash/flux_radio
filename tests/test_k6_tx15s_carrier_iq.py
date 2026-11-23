"""TX-15S offline real RF-IQ pilot-frozen phase prediction regressions.

No SDR access or radio commands. The RF-IQ test reconstructs complex
samples from immutable saved files if available. Synthetic test uses
true RF complex-phase trajectories and independently adds phase noise.
"""
from pathlib import Path
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))

from k6_tx15s_carrier_iq import (
    FS,TIME_ORIGIN,ANALYSIS_END,continuous_audio_quadratures,
    training_and_holdout_masks,linear_fit_predict,analyze_file,
    pilot_tone_response,
)
from k6_tx15s_rf_analyze import read_vector,N,PILOT_INDICES,region

BENCH=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"


def synth_rf_problem(*,broken_phase=False):
    words,ids=read_vector()
    freq=words/10.32444
    schedule=.892+np.arange(N)*(1/149.66)
    time=TIME_ORIGIN+np.arange(round((ANALYSIS_END-TIME_ORIGIN)*FS))/FS
    amp=np.interp(freq,[freq[0],freq[-1]],[1230,1000])
    c,s=continuous_audio_quadratures(time,schedule,ids,freq,amp)
    x=time-1.47
    actual=1.5+2*np.pi*14*x-2*np.pi*6*x*x/2
    actual+=np.sin(1.03)*c+np.cos(1.03)*s
    if broken_phase:
        # An independently chosen different source program changes phase
        # in the holdout; pilots remain the planned waveform. The false
        # sequence should not score like a faithful emitter.
        fakes=ids.copy()
        for i in range(N):
            if region(i)=="heldout":
                fakes[i]=(fakes[i]+3)%8
        cf,sf=continuous_audio_quadratures(time,schedule,fakes,freq,amp)
        actual=1.5+2*np.pi*14*x-2*np.pi*6*x*x/2
        actual+=np.sin(1.03)*cf+np.cos(1.03)*sf
    obs=actual+np.random.default_rng(785).normal(0,.055,len(time))
    pilot,hold=training_and_holdout_masks(time,schedule,carrier_mag=np.full(len(time),4.0))
    return obs,time,c,s,pilot,hold


def test_four_pilot_windows_train_quadratic_cfo_rf_iq_model_without_heldout():
    obs,t,c,s,pilot,hold=synth_rf_problem()
    assert pilot.sum()>1500 and hold.sum()>3000
    fm=linear_fit_predict(obs,t,[c,s],pilot,hold,carrier_poly_degree=2)
    null=linear_fit_predict(obs,t,[],pilot,hold,carrier_poly_degree=2)
    assert fm["fitted_pilot_only"]
    assert fm["heldout"]["circular_coherence"]>.995
    assert fm["heldout"]["phase_rms_deg"]<6
    assert fm["fm_quadrature_norm"]==pytest.approx(1.,abs=.04)
    assert fm["rf_cfo_drift_hz_per_s"]==pytest.approx(-6.,abs=.2)
    assert null["heldout"]["circular_coherence"]<.8
    assert fm["inverted_modulation_heldout_diagnostic"]["circular_coherence"]<.8


def test_quadratic_fit_coefficients_do_not_change_when_heldout_is_changed():
    obs,t,c,s,pilot,hold=synth_rf_problem()
    a=linear_fit_predict(obs,t,[c,s],pilot,hold,carrier_poly_degree=2)
    altered=obs.copy()
    altered[hold]+=2.0
    b=linear_fit_predict(altered,t,[c,s],pilot,hold,carrier_poly_degree=2)
    assert a["coefficients"]==b["coefficients"]
    assert b["heldout"]["phase_rms_deg"]>a["heldout"]["phase_rms_deg"]+50


def test_different_transmitted_tone_source_loses_rf_phase_predictability():
    obs,t,c,s,pilot,hold=synth_rf_problem(broken_phase=True)
    fm=linear_fit_predict(obs,t,[c,s],pilot,hold,carrier_poly_degree=2)
    assert fm["heldout"]["circular_coherence"]<.8


def test_unknown_rf_carrier_drift_model_rejected():
    obs,t,c,s,pilot,hold=synth_rf_problem()
    with pytest.raises(ValueError):
        linear_fit_predict(obs,t,[c,s],pilot,hold,carrier_poly_degree=5)


def test_no_rf_transmit_or_device_actions_in_offline_carrier_iq_analyzer():
    src=(ROOT/"tools/k6_tx15s_carrier_iq.py").read_text()
    for unsafe in ("import serial", "open_port(", "run_module(", "tx_on(",
                   "mutate_library(", "RtlCapture(", "subprocess.run("):
        assert unsafe not in src
    assert "actual_rf_iq_phase_measurement" in src
    assert "independently_commandable_arbitrary_rf_iq" in src


@pytest.mark.parametrize("run",["01","02","03"])
def test_physical_carrier_phase_capture_if_present(run):
    iq=BENCH/f"tx15s-dist150-run{run}.npy"
    if not iq.exists():
        pytest.skip("not all independent RF captures present yet")
    r=analyze_file(iq)
    assert r["actual_rf_iq_phase_measurement"]
    assert r["pilot_only_training"]
    assert not r["independently_commandable_arbitrary_rf_iq"]
    assert r["heldout_words"]==114
    for name,models in r["model_comparison"].items():
        fm=models["rf_iq_programmed_continuous_fm_quadratic_cfo"]
        null=models["rf_iq_carrier_only_null_quadratic_cfo"]
        assert fm["heldout"]["n_samples"]>4000
        assert fm["carrier_poly_degree"]==2
        assert abs(fm["estimated_rf_cfo_hz"])<1000
        assert fm["fm_quadrature_norm"]<3
        assert fm["heldout"]["circular_coherence"]>null["heldout"]["circular_coherence"]
        assert fm["heldout"]["circular_coherence"]>fm[
            "inverted_modulation_heldout_diagnostic"]["circular_coherence"]
