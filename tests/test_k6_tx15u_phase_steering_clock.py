"""TX-15U fixed-effect audio-rate estimator: synthetic + archived RF regression.

The prior four TX15T recordings are DEVELOPMENT DATA only.
Fresh run03 control/pulse are independent tests if added after commit.
No test opens serial or transmits.
"""
from pathlib import Path
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/"tools"),str(ROOT)]
from k6_tx15u_phase_steering_clock import (
    estimate_pilot_effective_audio_rate,analyze_file,
    FREQUENCY_CORRECTION_LIMIT_HZ,
)
from k6_tx15t_rf_steering import (
    source_variants,DECISION,CONTROL_HEADER,STEER_HEADER
)
from k6_tx15s_rf_analyze import PILOT_INDICES,FS,TIME_ORIGIN,ANALYSIS_END

BENCH=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"


def synthetically_discriminated_audio(*,variant=0,eps=-.155,noise=15.0):
    words,a,b=source_variants()
    ids=b if variant else a
    freq=words/10.32444
    schedule=.887+np.arange(192)/149.67
    t=TIME_ORIGIN+np.arange(round((ANALYSIS_END-TIME_ORIGIN)*FS))/FS
    loc=np.searchsorted(schedule,t,side="right")-1
    loc=np.clip(loc,0,191)
    true_freq=freq+eps
    prefix=np.r_[0.,np.cumsum(2*np.pi*true_freq[ids[:-1]]*
                             np.diff(schedule))]
    osc_phase=prefix[loc]+2*np.pi*true_freq[ids[loc]]*(
        t-schedule[loc])
    deviation=np.linspace(1200,1000,8)
    f=deviation[ids[loc]]*np.sin(osc_phase+.44)
    f+=np.random.default_rng(221).normal(0,noise,len(t))
    return f,ids,freq,schedule,t


@pytest.mark.parametrize("eps",[-.18,-.155,0.,.145,.25])
def test_pilot_fixed_effect_estimates_known_audio_clock_offset(eps):
    f,ids,freq,sched,t=synthetically_discriminated_audio(eps=eps)
    r=estimate_pilot_effective_audio_rate(f,ids,freq,sched)
    assert r["pilot_only"]
    assert r["no_heldout_or_guard_audio_phase_used"]
    assert r["qualified_pilot_slots"]>=60
    assert abs(r["measured_effective_common_audio_rate_correction_hz"]-eps)<.015
    assert r["residual_pilot_phase_rms_deg"]<3


def test_frequency_estimate_is_identical_after_destroying_heldout_audio():
    f,ids,freq,sched,t=synthetically_discriminated_audio(eps=-.156)
    a=estimate_pilot_effective_audio_rate(f,ids,freq,sched)
    altered=f.copy()
    slot=np.searchsorted(sched,t,side="right")-1
    pilot=np.isin(slot,PILOT_INDICES)
    altered[~pilot]=np.random.default_rng(28).normal(
        0,3000,sum(~pilot))
    b=estimate_pilot_effective_audio_rate(altered,ids,freq,sched)
    assert a==b


def test_source_variant_has_identical_pilot_audio_correction():
    f,ids,freq,sched,t=synthetically_discriminated_audio(eps=-.14)
    words,a,b=source_variants()
    v0=estimate_pilot_effective_audio_rate(f,a,freq,sched)
    v1=estimate_pilot_effective_audio_rate(f,b,freq,sched)
    assert v0==v1


def test_unqualified_audio_clock_correction_rejected():
    f,ids,freq,sched,t=synthetically_discriminated_audio(eps=.8)
    with pytest.raises(ValueError,match="bounds"):
        estimate_pilot_effective_audio_rate(f,ids,freq,sched)


def test_no_usb_radio_or_tx_actions_in_rate_analyzer():
    src=(ROOT/"tools/k6_tx15u_phase_steering_clock.py").read_text()
    for forbidden in ("import serial","RtlCapture(","open_port(",
                      "mutate_library(","run_module(","tx_on("):
        assert forbidden not in src
    assert "first_four_tx15t_captures_are_exploratory_not_independent_validation" in src
    assert "no_heldout_or_guard_audio_phase_used" in src


@pytest.mark.parametrize("variant,run",[
    (0,"01"),(0,"02"),(1,"01"),(1,"02"),
])
def test_posthoc_development_capture_only(variant,run):
    p=BENCH/f"tx15t-phase-steer-{variant}-run{run}.npy"
    if not p.exists():pytest.skip("earlier TX15T developer RF record unavailable")
    r=analyze_file(p,variant)
    assert r["first_four_tx15t_captures_are_exploratory_not_independent_validation"]
    assert r["pilot_only_timebase_rate_and_complex_rf_parameters"]
    assert r["unchanged_tx15t_source_programs"]
    assert r["qualification"]["predeclared_combined_test_pass"]
    assert r["qualification"]["model_advantage_on_steer_active"]>=DECISION[
        "min_correct_model_coherence_advantage_on_steer_active"]
    assert abs(r["primary_prediction"]["pilot_audio_oscillator_rate_correction"][
        "measured_effective_common_audio_rate_correction_hz"])<.4


@pytest.mark.parametrize("variant",[0,1])
def test_independent_future_emission_when_available(variant):
    p=BENCH/f"tx15t-phase-steer-{variant}-run03.npy"
    if not p.exists():pytest.skip("TX15U-independent RF verification not emitted yet")
    r=analyze_file(p,variant)
    assert r["qualification"]["predeclared_combined_test_pass"],r[
        "qualification"]
    assert r["pilot_only_timebase_rate_and_complex_rf_parameters"]
    assert r["source_iq_sha256"]
