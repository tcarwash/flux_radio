"""Offline saved-SDR MICIN analog-FSK decoder tests.

Synthetic I/Q is manufactured by this test only, never transmitted.
The final negative control runs against a previously archived real RF
PA-off startup pulse and requires it NOT be claimed as signed 4FSK.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_micin_burst_lab import make_cycle
from k6_micin_rf_decode import SDR_FS, analyze, to_baseband, rf_gate_segments


def synthetic_iq(*,pattern="balanced", gate_ms=100.,
                 phase_ms=19., peak_hz=3100., cfo_hz=58.,
                 signal_amp=35., noise_rms=2., seed=1001):
    rng=np.random.default_rng(seed)
    n=round(.58*SDR_FS)
    raw_signal=(rng.normal(size=n)+1j*rng.normal(size=n))*noise_rms
    a=round(.25*SDR_FS)
    count=round(gate_ms*SDR_FS/1000)
    wave,_=make_cycle(pattern=pattern)
    t=np.arange(count)/SDR_FS
    position=np.mod((t+phase_ms/1000)*48000,len(wave))
    whole=np.floor(position).astype(int)
    fraction=position-whole
    base=wave[whole]*(1-fraction)+wave[(whole+1)%len(wave)]*fraction
    freqs=(-280500. + cfo_hz + peak_hz*base/.03)
    ph=np.cumsum(2*np.pi*freqs/SDR_FS)
    raw_signal[a:a+count]+=signal_amp*np.exp(1j*ph)
    clipped_real=np.clip(np.rint(raw_signal.real+127.5),0,255).astype(np.uint8)
    clipped_imag=np.clip(np.rint(raw_signal.imag+127.5),0,255).astype(np.uint8)
    raw=np.empty(n*2,dtype=np.uint8)
    raw[::2]=clipped_real
    raw[1::2]=clipped_imag
    return raw


@pytest.mark.parametrize("pattern",["balanced","stress"])
@pytest.mark.parametrize("phase_ms",[1.,19.,38.])
def test_arbitrary_rf_starting_phase_and_frozen_heldout(pattern,phase_ms):
    raw=synthetic_iq(pattern=pattern,phase_ms=phase_ms)
    r=analyze(raw,pattern=pattern)
    assert r["pilot_qualified_fsk_cycle"],r.get("qualification_failure")
    m=r["measurement"]
    assert m["pilot_score_abs_correlation"]>.96
    assert abs(m["pilot_only_estimated_peak_rf_deviation_hz"]-3100)<250
    assert m["heldout_symbol_count"]==32
    assert m["heldout_symbol_errors"] <= 2,m
    assert m["heldout_normalized_rf_frequency_rmse"]<.12,m
    assert m["pilot_derived_timing_gain_intercept_not_refit_on_holdout"]
    assert r["radio_emission_source_independently_authenticated"] is False
    assert r["micin_connected_or_verified_by_this_tool"] is False
    assert r["rf_tx_initiated"] is False


def test_wrong_reference_pattern_pilot_can_lock_but_holdout_fails():
    # Both source variants deliberately use the SAME known pilot. Do not
    # choose a target based on heldout success after inspecting RF.
    raw=synthetic_iq(pattern="stress",phase_ms=19.)
    result=analyze(raw,pattern="balanced")
    assert result["pilot_qualified_fsk_cycle"]
    assert result["measurement"]["pilot_score_abs_correlation"]>.93
    assert result["measurement"]["heldout_symbol_errors"]>=8


def test_inverted_frequency_response_is_detected():
    r=analyze(synthetic_iq(peak_hz=-2800,phase_ms=14),pattern="balanced")
    assert r["pilot_qualified_fsk_cycle"],r.get("qualification_failure")
    assert r["measurement"]["pilot_only_estimated_frequency_polarity"]=="inverted"
    assert r["measurement"]["heldout_symbol_errors"]<=2


def test_short_startup_pulse_is_not_a_complete_reference():
    raw=synthetic_iq(gate_ms=26,pattern="balanced")
    r=analyze(raw,pattern="balanced")
    assert not r["pilot_qualified_fsk_cycle"]
    assert r["measurement"] is None
    assert "RF gate long enough" in r["qualification_failure"]


def test_unmodulated_carrier_is_not_four_level_frequency_trajectory():
    raw=synthetic_iq(peak_hz=0,pattern="balanced")
    r=analyze(raw,pattern="balanced")
    assert not r["pilot_qualified_fsk_cycle"]


def test_no_rf_does_not_claim_a_modulation_match():
    raw=synthetic_iq(signal_amp=0)
    r=analyze(raw,pattern="balanced")
    assert not r["pilot_qualified_fsk_cycle"]


def test_metadata_and_validation_fail_closed():
    with pytest.raises(ValueError):
        analyze(np.zeros(300,dtype=np.uint8))
    with pytest.raises(ValueError):
        analyze(np.zeros(2*SDR_FS,dtype=np.float32))
    with pytest.raises(ValueError):
        analyze(synthetic_iq(),offset_hz=600000)
    with pytest.raises(ValueError):
        analyze(synthetic_iq(),pattern="badpattern")


def test_no_radio_or_soundcard_interfaces():
    source=(ROOT/"tools/k6_micin_rf_decode.py").read_text()
    for prohibited in ("import serial","import pyrtlsdr","import sounddevice",
                       "import pyaudio","tx_on(", "fd_write(", "run_module(",
                       "subprocess","os.system","socket."):
        assert prohibited not in source


def test_archived_real_paoff_negative_control_if_present():
    p=Path.home()/"flux-bench/modulation-micin-20261121/txmic-paoff-rfcheck-sdr-iq.npy"
    if not p.exists():
        pytest.skip("archived PA-off physical RF capture not present here")
    raw=np.load(p,allow_pickle=False,mmap_mode="r")
    r=analyze(raw,pattern="balanced")
    assert not r["pilot_qualified_fsk_cycle"],r["measurement"]
    assert r["measurement"] is None
    assert len(r["rf_gates"]) == 1
    short = r["rf_gates"][0]
    assert short["start_s"] == pytest.approx(3.040,abs=.004)
    assert short["duration_ms"] == pytest.approx(26.,abs=4.)
    assert short["spectral_local_peak_over_flanks_db"] > 20.
    assert short["sufficient_for_arbitrary_phase_cycle"] is False
