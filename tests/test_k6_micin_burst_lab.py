"""Offline loopable MICIN 4FSK voltage lab; NO audio playback or radio."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
import wave

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_micin_burst_lab import (
    BAUD, FS, GUARD_MS, PILOT_SYMBOLS, reference_symbols,
    make_cycle, analyze, score_coupling, main,
)


def test_fixed_burst_period_and_100ms_math():
    for pattern in ("stress","balanced"):
        y, ids = make_cycle(pattern=pattern)
        assert len(ids)==48 and len(y)==2112
        assert len(y)/FS==pytest.approx(.044)
        assert np.max(abs(y)) <= .03
        assert len(set(ids))==4
        report,_=analyze(pattern=pattern)
        assert report["one_whole_period_in_gate_guaranteed_ideal_only"] is True
        assert report["max_combined_rf_startup_shutdown_and_playback_interruption_ms_if_unsynchronized"]==pytest.approx(12.)
        assert report["radio_tx_performed"] is False
        assert report["physical_audio_to_rf_sync_demonstrated"] is False
        assert report["physical_micin_connection_verified"] is False
        assert report["independent_watchdog_and_compiled_firmware_hardware_verified"] is False


def test_pilot_is_fixed_and_different_holdout_patterns():
    stress=reference_symbols("stress")
    balanced=reference_symbols("balanced")
    assert len(stress)==len(balanced)==48
    assert np.array_equal(stress[:PILOT_SYMBOLS], balanced[:PILOT_SYMBOLS])
    assert not np.array_equal(stress[PILOT_SYMBOLS:],balanced[PILOT_SYMBOLS:])
    assert np.array_equal(np.bincount(stress[PILOT_SYMBOLS:],minlength=4),[8]*4)
    assert np.array_equal(np.bincount(balanced[PILOT_SYMBOLS:],minlength=4),[8]*4)


def test_illustrative_hpf_distortion_discriminates_dc_stress():
    stress,_=analyze(pattern="stress")
    balanced,_=analyze(pattern="balanced")
    s={x["hypothetical_first_order_highpass_cutoff_hz"]:x
       for x in stress["simulated_coupling"]}
    b={x["hypothetical_first_order_highpass_cutoff_hz"]:x
       for x in balanced["simulated_coupling"]}
    assert s[0.]["heldout_symbol_errors"]==0
    assert b[0.]["heldout_symbol_errors"]==0
    assert s[20.]["heldout_symbol_errors"]>=10
    assert b[20.]["heldout_symbol_errors"]==0
    assert b[300.]["heldout_symbol_errors"]>0
    assert s[20.]["pilot_symbol_errors"]==0
    assert all(x["physical_micin_hpf_measured"] is False for x in s.values())
    assert s[20.]["heldout_normalized_level_rmse"] > s[0.]["heldout_normalized_level_rmse"]


def test_wav_output_deterministic_metadata_and_no_overwrite(tmp_path):
    a=tmp_path/"a"
    b=tmp_path/"b"
    assert main(["--out-dir",str(a),"--repeats","6"])==0
    assert main(["--out-dir",str(b),"--repeats","6"])==0
    xa=(a/"micin-4fsk-loop.wav").read_bytes()
    xb=(b/"micin-4fsk-loop.wav").read_bytes()
    assert xa==xb
    with wave.open(str(a/"micin-4fsk-loop.wav")) as wav:
        assert wav.getnframes()==2112*6
        assert wav.getframerate()==FS
        assert wav.getnchannels()==1
        assert wav.getsampwidth()==2
    j=json.loads((a/"micin-4fsk-loop.json").read_text())
    assert j["wav_sha256"]==hashlib.sha256(xa).hexdigest()
    assert j["heldout_pattern"]=="stress"
    assert j["audio_duration_seconds"]==pytest.approx(.264)
    with pytest.raises(SystemExit):
        main(["--out-dir",str(a),"--repeats","6"])


@pytest.mark.parametrize("pattern",["unknown","","slow"])
def test_reject_unknown_pattern(pattern):
    with pytest.raises(ValueError,match="pattern"):
        reference_symbols(pattern)


@pytest.mark.parametrize("cutoff",[-1,FS/2, float("nan")])
def test_reject_invalid_hpf(cutoff):
    y, ids=make_cycle()
    with pytest.raises(ValueError):
        score_coupling(y,ids,cutoff_hz=cutoff)


def test_no_live_control_or_audio_playback_apis():
    src=(ROOT/"tools/k6_micin_burst_lab.py").read_text()
    for bad in ("import sounddevice","import pyaudio","import serial",
                "tx_on(","run_module(","flash(","fd_write(",
                "subprocess","os.system","socket."):
        assert bad not in src
