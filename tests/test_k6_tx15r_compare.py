"""Read-only between-capture TX-15R comparison tests."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))

from k6_tx15r_compare import compare_runs


def fake_run(tag="a"):
    transitions=[]
    for i in range(48,192,3):
        transitions.append({
            "index":i,"region":"heldout","changed":True,
            "fit_qualified":True,"before":0,"after":7,
            "delta_audio_hz":2017,
            "apparent_audio_phase_jump_rad":.1+.005*(i//3),
        })
    return {
        "experiment":"synthetic stub", "header_sha256":"same",
        "source_iq_sha256":tag,"pilot_symbols":48,"heldout_symbols":144,
        "clock_and_phase_training_uses_heldout":False,
        "audio_transfer_lag":{"pilot_effective_audio_lag_s":0.},
        "phase_edge_events":transitions,
        "tone_decoder":{"heldout_correct":144,"heldout_errors":[]},
        "tone_fit_rms_hz":[50.]*191+[2000.],
        "clock_training_and_holdout":{
            "pilot_strong_events":15,
            "pilot_observed_update_rate_hz":149.66,
            "heldout_midpoint_median_abs_us":20.},
        "audio_phase_heldout":{
            "heldout_same_word":{"rms_deg":.4},
            "heldout_changed_word_clock_and_one_delay":{"rms_deg":6.}},
        "uncalibrated_heldout_tone_deviation_response":[
            {"tone_index":k,"audio_tone_hz":500+k*280,
             "median_rf_discriminator_deviation_hz":1230-30*k}
            for k in range(8)
        ]
    }


def test_comparison_is_reproducible_and_exploratory():
    a,b=fake_run("a"),fake_run("b")
    for j,x in enumerate(b["phase_edge_events"]):
        x["apparent_audio_phase_jump_rad"]+=.0005*j
    r=compare_runs(a,b)
    assert r["common_heldout_tone_change_transitions"]==48
    assert r["read_only"] is True
    assert r["crossrun_comparison_is_exploratory"] is True
    assert r["rf_carrier_phase_or_iq_evm_measured"] is False
    assert r["run1"]["heldout_tones_correct"]==144
    assert r["terminal_last_slot_is_badly_conditioned_in_both_captures"]


@pytest.mark.parametrize("mutation",[
    "same_capture","different_header","unexpected_pilot",
    "heldout_leak","different_pair"
])
def test_comparator_rejects_noncomparable_sources(mutation):
    a,b=fake_run("a"),fake_run("b")
    if mutation=="same_capture":b["source_iq_sha256"]="a"
    elif mutation=="different_header":b["header_sha256"]="different"
    elif mutation=="unexpected_pilot":b["pilot_symbols"]=16
    elif mutation=="heldout_leak":b["clock_and_phase_training_uses_heldout"]=True
    elif mutation=="different_pair":b["phase_edge_events"][0]["before"]=3
    with pytest.raises(ValueError):
        compare_runs(a,b)


def test_no_radio_operations_in_comparison_source():
    txt=(ROOT/"tools/k6_tx15r_compare.py").read_text()
    for unsafe in ("import serial","RtlCapture(", "open_port(",
                   "run_module(", "mutate_library(", "tx_on("):
        assert unsafe not in txt


def test_physical_crossrun_when_both_saved():
    base=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"
    files=[base/f"tx15r-long150-run{i}-phase-v2.json" for i in ("01","02")]
    if not all(p.exists() for p in files):
        pytest.skip("two real physical IQ JSON archives unavailable")
    a,b=[json.loads(p.read_text()) for p in files]
    r=compare_runs(a,b)
    assert r["common_heldout_tone_change_transitions"]==48
    assert r["run1"]["heldout_tones_correct"]==143
    assert r["run2"]["heldout_tones_correct"]==144
    assert .80<r["audio_phase_residual_interrun_pearson_correlation"]<.99
    assert 5<r["audio_phase_residual_interrun_median_abs_difference_deg"]<9
    assert .79<r["first_to_last_tone_deviation_ratio_run1"]<.85
    assert .79<r["first_to_last_tone_deviation_ratio_run2"]<.85
    assert r["terminal_last_slot_is_badly_conditioned_in_both_captures"]
