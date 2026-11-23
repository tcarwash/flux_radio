"""TX-15Q pilot-only clock fit and heldout FM audio phase tests.

Synthetic discriminators are NOT physical RF. Archived real SDR IQ tests
are optional and read files without opening radio, sound or SDR devices.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx15_phase_vector import NEW_HEADER,parse_header,WORD_PER_HZ
from k6_tx15_transition_clock import (
    FS, NOMINAL_PERIOD, analyze_discriminator, analyze_iq,
    find_audio_midpoint_edges,pilot_clock,wrapped_rms_deg,
)
from k6_tx15_phase_rf_analyze import fit_phases,summarize_edges


def fixture_audio(*,ppm=2800,reset_on_change=False,seed=42,
                  initial_time=.8908,time_origin=.7,length_s=.78):
    """Time-controlled continuous-phase oscillator driving an FM discriminator."""
    words,symbols=parse_header(NEW_HEADER)
    hz=words.astype(float)/WORD_PER_HZ
    rng=np.random.default_rng(seed)
    step=NOMINAL_PERIOD*(1+ppm*1e-6)
    count=round(length_s*FS)
    data=rng.normal(0,20,count)
    times=np.arange(count)/FS+time_origin
    carry=1.2
    prev=None
    for i,k in enumerate(symbols):
        begin=initial_time+i*step
        end=begin+step
        loc=np.flatnonzero((times>=begin)&(times<end))
        if reset_on_change and prev is not None and prev!=k:
            carry=0
        data[loc]+=1050*np.sin(carry+2*np.pi*hz[k]*(times[loc]-begin))
        carry=(carry+2*np.pi*hz[k]*step)%(2*np.pi)
        prev=k
    return data,symbols,hz,initial_time,step


def test_clock_can_follow_continuous_synthetic_2800ppm():
    # The Hilbert midpoint is deliberately a real signal-processing
    # operation, so its crossing time includes filter / transition
    # biases. This waveform is a numerical stress test, not ideal
    # proof that a 3-event pilot always identifies clock ppm.
    data,symbols,hz,t0,step=fixture_audio(ppm=2800)
    report=analyze_discriminator(data,symbols,hz,
             start_hint_s=t0+.0001,time_origin_s=.7)
    c=report["clock_training"]
    assert c["acquired_from_pilot_only"]
    assert c["pilot_indices"]==[12,13,15]
    assert abs(c["apparent_schedule_skew_ppm_relative_SDR"]-2800)<550
    assert c["pilot_crossing_rms_us"]<25
    assert report["heldout_clock_test"]["pilot_clock_schedule_median_abs_error_us"]<100
    assert (report["heldout_clock_test"]["pilot_clock_schedule_median_abs_error_us"]<
            report["heldout_clock_test"]["nominal_schedule_median_abs_error_us"]*.25)
    assert report["clock_adjusted_heldout_same_word"]["n"]==28
    assert report["clock_and_pilot_delay_adjusted_heldout_changed_word"]["n"]>=17
    assert report["clock_and_pilot_delay_adjusted_heldout_changed_word"][
        "rms_audio_phase_residual_deg"]<40


def test_resets_on_change_do_not_masquer_as_continuous_phase():
    data,symbols,hz,t0,step=fixture_audio(ppm=2600,reset_on_change=True)
    # This deliberately extreme synthetic switch generates corrupt
    # per-slot sine fits; the analyzer must NOT score it as success.
    with pytest.raises(ValueError,match="too few qualified"):
        analyze_discriminator(data,symbols,hz,
            start_hint_s=t0+.0001,time_origin_s=.7)


def test_pilot_clock_exact_on_known_synthetic_edge_times():
    nominal=NOMINAL_PERIOD
    true_period=nominal*1.0028
    first=.891234
    edges=[]
    for idx in (12,13,15,19,22,25,28,34,40,46,59,63):
        edges.append({
            "region":"pilot" if idx<16 else "heldout",
            "after_symbol_index":idx,
            "crossing_time_s":first+idx*true_period,
        })
    got=pilot_clock(edges)
    assert got["pilot_indices"]==[12,13,15]
    assert got["apparent_schedule_skew_ppm_relative_SDR"]==pytest.approx(2800,abs=1e-5)
    assert got["start_s"]==pytest.approx(first,abs=1e-9)


def test_train_frozen_if_heldout_edge_timing_is_corrupted():
    data,symbols,hz,t0,step=fixture_audio()
    edges=find_audio_midpoint_edges(data,symbols,hz,
        start_hint_s=t0,time_origin_s=.7)
    baseline=pilot_clock(edges)
    # This deliberately changes ONLY observations from the heldout
    # portion, checking that no heldout times feed the clock fit.
    altered=[dict(e) for e in edges]
    for edge in altered:
        if edge["region"]=="heldout":
            edge["crossing_time_s"]+=.0025
    fitted=pilot_clock(altered)
    assert fitted==baseline


def test_silent_discriminator_does_not_pass():
    _,symbols,hz,t0,step=fixture_audio()
    with pytest.raises(ValueError,match="no valid"):
        find_audio_midpoint_edges(np.zeros(round(.78*FS)),symbols,hz,
              start_hint_s=t0,time_origin_s=.7)


def test_phase_clock_helpers_guard_ranges():
    assert wrapped_rms_deg(np.array([0,np.pi/2]))==pytest.approx(
        np.sqrt((90*90)/2))
    assert np.isnan(wrapped_rms_deg(np.array([])))
    w,s=parse_header(NEW_HEADER)
    with pytest.raises(ValueError):
        fit_phases(np.zeros(70000),s,w/WORD_PER_HZ,
                   first_s=.9,time_origin_s=.7,symbol_step_s=.01)
    with pytest.raises(ValueError):
        summarize_edges(np.zeros(64),s,w/WORD_PER_HZ,
            np.ones(64),np.zeros(64),symbol_step_s=.01)


def test_synthetic_midpoint_has_a_known_clock_identifiability_limit():
    # A 3-edge pilot can mis-estimate the true schedule when sharp
    # synthetic transitions create a frequency-dependent Hilbert
    # midpoint bias. Keep that limitation explicit, and require
    # physically archived evidence / model qualification separately.
    data,symbols,hz,t0,step=fixture_audio(ppm=3000)
    got=pilot_clock(find_audio_midpoint_edges(
        data,symbols,hz,start_hint_s=t0+.0001,time_origin_s=.7))
    assert abs(got["apparent_schedule_skew_ppm_relative_SDR"]-3000)>600


def test_analyzer_does_not_open_radio_or_transmitter():
    source=(ROOT/"tools/k6_tx15_transition_clock.py").read_text()
    for forbidden in ("import serial","open_port(","RtlCapture(",
                      "link.run_module(","tx_on(","mutate_library(",
                      "import sounddevice"):
        assert forbidden not in source
    assert "clock_or_delay_derived_from_heldout" in source
    assert "audio_phase_not_rf_carrier_iq" in source


@pytest.mark.parametrize("filename,low,high",[
    ("tx15p-phase150-run01.npy",2500,3400),
    ("tx15p-phase150-run02.npy",1800,2600),
])
def test_saved_physical_tone_clock_and_holdout(filename,low,high):
    p=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"/filename
    if not p.exists():
        pytest.skip("archived conducted RF IQ absent")
    r=analyze_iq(p)
    assert r["clock_or_delay_derived_from_heldout"] is False
    assert r["audio_phase_not_rf_carrier_iq"]
    assert r["heldout_tone_correct"]==48
    assert r["clock_training"]["pilot_indices"]==[12,13,15]
    assert low<r["clock_training"]["apparent_schedule_skew_ppm_relative_SDR"]<high
    assert r["heldout_clock_test"]["heldout_high_contrast_boundaries"]==17
    assert r["heldout_clock_test"]["pilot_clock_schedule_median_abs_error_us"]<50
    assert r["heldout_clock_test"]["nominal_schedule_median_abs_error_us"]>400
    assert r["clock_adjusted_heldout_same_word"]["n"]==28
    assert r["clock_adjusted_heldout_changed_word"]["n"]>=19
    assert r["clock_and_pilot_delay_adjusted_heldout_changed_word"]["n"]>=19
    assert r["clock_and_pilot_delay_adjusted_heldout_changed_word"][
        "rms_audio_phase_residual_deg"]<16
