"""TX-15R long RF discriminator design/analysis tests, all offline.

Physically captured SDR IQ is optional: synthetic training exercises clocks,
pilot isolation, short/long vector schema and reset-phase negative control.
"""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx15r_rf_analyze import (
    HEADER,N,PILOT,FS,T,TIME_ORIGIN,read_vector, pilot_start,
    find_strong_edges,fit_pilot_clock,slot_audio_fit,phase_edges,
    fit_phase_lag,pair_residuals,evaluate_phases,score_symbols,
    analyze_file,
)

def synth_audio(*,rate=149.62,continuous=True,noise=40.,seed=981):
    """Synthetic audio delivered by FM discriminator, NOT RF carrier IQ."""
    words,ids=read_vector()
    freq=np.array(words,dtype=float)/10.32444
    rng=np.random.default_rng(seed)
    first=.891
    interval=1/rate
    signal=rng.normal(0,noise,round((2.55-TIME_ORIGIN)*FS))
    all_t=np.arange(len(signal))/FS+TIME_ORIGIN
    accumulator=1.7
    prev=-1
    for i,k in enumerate(ids):
        if not continuous and int(k)!=prev:
            accumulator=0.
        l=round((first+i*interval-TIME_ORIGIN)*FS)
        r=round((first+(i+1)*interval-TIME_ORIGIN)*FS)
        t=all_t[l:r]-(first+i*interval)
        signal[l:r]+=1050*np.sin(accumulator+2*np.pi*freq[k]*t)
        accumulator=(accumulator+2*np.pi*freq[k]*interval)%(2*np.pi)
        prev=int(k)
    return signal,ids,freq,first,interval


def test_long_source_header_matches_expected_fixed_192():
    words,s=read_vector()
    assert len(words)==8 and len(s)==N
    assert PILOT==48
    pilot=s[:PILOT]
    assert len(set(pilot))==8
    strong=[i for i in range(1,PILOT)
            if abs((words[s[i]]-words[s[i-1]])/10.32444)>450]
    assert strong==list(range(3,48,3))
    assert len(s[PILOT:])==144
    assert len(set(s[PILOT:]))==8


def test_all_pilot_training_resists_arbitrary_heldout_edits():
    signal,s,f,t0,step=synth_audio()
    a=pilot_start(signal,s,f)
    assert .88<a["start_hint_s"]<.90
    edges=find_strong_edges(signal,s,f,a["start_hint_s"])
    clock=fit_pilot_clock(edges)
    assert clock["pilot_strong_events"]>=12
    changed=[dict(e) for e in edges]
    for e in changed:
        if not e["pilot"] and e["found"]:
            e["midpoint_time_s"]+=.00175
    other=fit_pilot_clock(changed)
    for field in ("pilot_start_s","pilot_period_s","pilot_strong_events",
                  "pilot_relative_sdr_schedule_ppm"):
        assert clock[field]==other[field]
    # The heldout *errors* must change, proving that heldout is actually
    # evaluated, but never used for training.
    assert other["heldout_midpoint_rms_us"]>clock["heldout_midpoint_rms_us"]


def test_synthetic_source_is_detectable_and_clock_frozen():
    signal,s,f,t0,step=synth_audio()
    anchor=pilot_start(signal,s,f)
    events=find_strong_edges(signal,s,f,anchor["start_hint_s"])
    clock=fit_pilot_clock(events)
    assert clock["pilot_clock_fits_heldout"] is False
    assert abs(clock["pilot_period_s"]-step)<.000015
    assert clock["heldout_strong_events"]>=35
    assert clock["heldout_midpoint_median_abs_us"]<150
    obs=slot_audio_fit(signal,s,f,clock["pilot_start_s"],clock["pilot_period_s"])
    phase=phase_edges(*obs,s,f,clock["pilot_period_s"])
    lag=fit_phase_lag(phase)
    paired=pair_residuals(phase,lag["pilot_effective_audio_lag_s"])
    result=evaluate_phases(phase,lag["pilot_effective_audio_lag_s"],paired)
    assert result["heldout_changed_word_clock_and_one_delay"]["n"]>=30
    ids=score_symbols(signal,s,f,clock["pilot_start_s"],clock["pilot_period_s"])
    assert ids["heldout_correct"]>=139
    assert ids["heldout_total"]==144


def test_long_clock_negative_reset_on_change_is_not_phase_continuity():
    sig,s,f,t0,step=synth_audio(continuous=False)
    anchor=pilot_start(sig,s,f)
    edges=find_strong_edges(sig,s,f,anchor["start_hint_s"])
    clock=fit_pilot_clock(edges)
    obs=slot_audio_fit(sig,s,f,clock["pilot_start_s"],clock["pilot_period_s"])
    phases=phase_edges(*obs,s,f,clock["pilot_period_s"])
    try:
        lag=fit_phase_lag(phases)
    except ValueError:
        return  # Invalid pilot transfer is itself a negative result.
    metric=evaluate_phases(phases,lag["pilot_effective_audio_lag_s"],
                           pair_residuals(phases,lag["pilot_effective_audio_lag_s"]))
    assert metric["heldout_changed_word_clock_and_one_delay"]["rms_deg"]>35


def test_inert_analyzer_never_opens_radio_or_sdr():
    txt=(ROOT/"tools/k6_tx15r_rf_analyze.py").read_text()
    for prohibited in ("import serial", "open_port(", "RtlCapture(",
                       "run_module(", "mutate_library(", "tx_on("):
        assert prohibited not in txt
    assert "clock_and_phase_training_uses_heldout" in txt
    assert "audio_tone_phase_not_rf_carrier_phase" in txt


def test_missing_or_mismatched_long_header(tmp_path):
    q=tmp_path/"short.h"
    q.write_text("#define TX8_NS 64u")
    with pytest.raises(ValueError):
        read_vector(q)


@pytest.mark.parametrize("run,minimum_correct",[
    ("01",143),("02",144)
])
def test_archived_v3_if_available(run,minimum_correct):
    p=Path.home()/(
        "flux-bench/tx-injection-20261122/tone-oscillator/"
        f"tx15r-long150-run{run}.npy")
    if not p.exists():pytest.skip("no physical TX-15R capture yet")
    report=analyze_file(p)
    assert report["clock_and_phase_training_uses_heldout"] is False
    assert report["audio_tone_phase_not_rf_carrier_phase"]
    assert report["tone_decoder"]["heldout_total"]==144
    assert report["tone_decoder"]["heldout_correct"]>=minimum_correct
    assert report["clock_training_and_holdout"]["pilot_strong_events"]==15
    assert report["clock_training_and_holdout"]["heldout_strong_events"]>=43
    assert report["audio_phase_heldout"]["heldout_same_word"]["n"]==95
    assert report["audio_phase_heldout"][
        "heldout_changed_word_clock_and_one_delay"]["n"]==48
    assert report["tone_fit_rms_hz"][-1]>1000
