"""TX-15P physical discriminator regression: no device I/O in tests.

The synthetic source is an audio-frequency control waveform, NOT actual RF.
An optional archived negative/positive physical check reads an existing
capture but does not operate transmitter or SDR hardware.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT))

from k6_tx15_phase_vector import NEW_HEADER, parse_header,ideal_audio_phase
from k6_tx15_phase_rf_analyze import (
    fit_phases,summarize_edges,analyze,wrap_phase,
)
from k6_tx15_phase_rf_bench import run_transaction


@pytest.mark.parametrize("mode,expected_same_rms_floor",[
    ("continuous",0.),
    ("reset_on_change",0.),
    ("reset_on_every_write",25.)
])
def test_phase_edge_metric_distinguishes_same_word_reset_policy(mode,expected_same_rms_floor):
    fs=48000;words,symbols=parse_header(NEW_HEADER)
    tones=words/10.32444
    phases=ideal_audio_phase(words,symbols,mode,fs=fs)
    first_s=.05
    signal=np.zeros(round(.55*fs))
    a=round(first_s*fs)
    signal[a:a+len(phases)]=1000*np.sin(phases)
    result=fit_phases(signal,symbols,tones,first_s=first_s,
                      time_origin_s=0.,fs=fs)
    edges=summarize_edges(result["phases_rad"],symbols,tones,
                           result["amplitude_hz"],result["fit_rmse_hz"])
    held=edges["summary"]["heldout_same_word"]
    assert held["n"]==28
    if mode=="reset_on_every_write":
        assert held["rms_apparent_phase_jump_deg"]>expected_same_rms_floor
    else:
        assert held["rms_apparent_phase_jump_deg"]<1.


def test_wrapper_refuses_unarmed_before_reading_source_or_any_port(tmp_path):
    # Deliberately no files, no serial device, no UART or SDR dependency.
    with pytest.raises(ValueError,match="dummy-load-confirmed"):
        run_transaction("/dev/should-not-exist",tmp_path/"unused",
                        tmp_path/"missing.c",tmp_path/"unused.k6m",
                        armed=False)


def test_source_does_not_assume_claim_of_calibrated_rf_iq():
    source=(ROOT/"tools/k6_tx15_phase_rf_analyze.py").read_text()
    for prohibited in ("import serial","open_port(","RtlCapture(",
                       "link.run_module(","tx_on(","mutate_library(",
                       "import sounddevice"):
        assert prohibited not in source
    assert "phase_timing_uses_heldout_data" in source
    assert "phase_domain" in source
    assert "uncalibrated_sdr" in source


def test_phase_fit_validation_and_wrapping():
    assert float(wrap_phase(2*np.pi+.1))==pytest.approx(.1)
    w,s=parse_header(NEW_HEADER)
    with pytest.raises(ValueError):
        fit_phases(np.zeros(20),s,w/10.32444,first_s=.01,time_origin_s=0)
    with pytest.raises(ValueError):
        summarize_edges(np.zeros(3),s,w/10.32444,np.zeros(3),np.zeros(3))


@pytest.mark.parametrize("filename,minimum_pilot",[
    ("tx15p-phase150-run01.npy",16),
    ("tx15p-phase150-run02.npy",15),
])
def test_archived_real_tx15p_sdr_score_if_present(filename,minimum_pilot):
    p=Path.home()/"flux-bench/tx-injection-20261122/tone-oscillator"/filename
    if not p.exists():
        pytest.skip("physical RF IQ archive not present")
    report=analyze(p)
    assert report["rf_transmitted_by_analyzer"] is False
    assert report["acquisition"]["training_correct_tones"]>=minimum_pilot
    assert report["acquisition"]["heldout_correct_tones"]==48
    assert report["acquisition"]["phase_timing_uses_heldout_data"] is False
    same=report["summary"]["heldout_same_word"]
    changed=report["summary"]["heldout_changed_word"]
    assert same["n"]==28 and same["rms_apparent_phase_jump_deg"]<2
    assert 19<=changed["n"]<=20
    assert changed["rms_apparent_phase_jump_deg"]>40
    assert (report["summary"]["heldout_same_word_reset_every_write_hypothesis"]
            ["phase_residual_rms_deg"]>60)
