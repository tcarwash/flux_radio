"""Offline Flux TX-12D RF receiver regression, fully synthetic and inert."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, resample_poly, sosfilt

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_micin_rf_observe import (
    FS, CYCLE_N, from_file, demodulate, active_window, acquire_and_score,
    main
)
from k6_micin_burst_lab import make_cycle, reference_symbols, PILOT_SYMBOLS


def synthetic_rf(pattern="balanced", *, highpass_hz=0, offset_hz=530.,
                 gain_hz=1350., start_offset_samples=713, noise=0,
                 seed=91):
    cycle,_=make_cycle(pattern=pattern)
    n=int(.18*FS)
    first=int(.030*FS)
    rf_len=int(.100*FS)
    all_audio=np.tile(cycle,20)/.03
    if highpass_hz:
        all_audio=sosfilt(butter(1,highpass_hz,fs=FS,output="sos",btype="highpass"),
                          all_audio)
    x=all_audio[8*CYCLE_N+start_offset_samples:
                8*CYCLE_N+start_offset_samples+rf_len]
    freq=offset_hz+gain_hz*x
    z=np.zeros(n,dtype=np.complex64)
    z[first:first+rf_len]=.6*np.exp(2j*np.pi*np.cumsum(freq)/FS)
    if noise:
        rng=np.random.default_rng(seed)
        z+=noise*(rng.standard_normal(n)+1j*rng.standard_normal(n))
    return z


def score(z,pattern):
    freq,env=demodulate(z)
    start,end,activity=active_window(env)
    report=acquire_and_score(freq,(start,end),pattern=pattern)
    return report, (start,end), freq, activity


@pytest.mark.parametrize("pattern",["stress","balanced"])
def test_unsynchronized_rf_burst_pilot_and_heldout(pattern):
    report,window,_,activity=score(synthetic_rf(pattern),pattern)
    assert .97<report["pilot_correlation"]<=1.
    assert report["holdout_symbol_errors"]==0
    assert len(report["heldout_recovered_symbols"])==32
    assert report["heldout_expected_symbols"]==reference_symbols(pattern)[16:].tolist()
    assert report["data_heldout_from_acquisition"] is True
    assert report["not_full_complex_iq_evm"]
    assert report["pilot_symbol_count"]==PILOT_SYMBOLS
    assert 100.<=activity["activity_duration_ms"]<=103.
    assert 850<report["pilot_only_gain_hz_per_normalized_level"]<1700
    assert abs(report["pilot_only_frequency_offset_hz"]-530)<80
    assert window[0]>int(.015*FS)
    assert window[1]<int(.170*FS)


@pytest.mark.parametrize("pattern,cutoff,minimum_errors,maximum_errors",[
    ("stress",20,10,32), ("stress",100,18,32),
    ("balanced",20,0,0), ("balanced",100,0,0),
    ("balanced",300,3,15),
])
def test_hp_filter_strongly_changes_heldout_behavior(
        pattern,cutoff,minimum_errors,maximum_errors):
    report,_,_,_=score(synthetic_rf(pattern,highpass_hz=cutoff),pattern)
    assert report["pilot_correlation"]>.91
    assert minimum_errors<=report["holdout_symbol_errors"]<=maximum_errors


def test_gain_and_offset_can_only_be_inferred_from_pilot():
    z=synthetic_rf("stress")
    baseline,window,freq,_=score(z,"stress")
    cycle_start=round(baseline["first_cycle_start_s"]*FS)
    # Change heldout data within the selected cycle but NOT the calibration
    # pilot samples. The target frame's pilot coefficients remain frozen.
    changed=freq.copy()
    heldout_begin=cycle_start+round(.002*FS)+16*40
    heldout_end=cycle_start+round(.002*FS)+48*40
    changed[heldout_begin:heldout_end]+=430.
    got=acquire_and_score(changed,window,pattern="stress")
    assert got["first_cycle_start_s"]==baseline["first_cycle_start_s"]
    assert got["pilot_correlation"]==pytest.approx(baseline["pilot_correlation"],abs=1e-10)
    assert got["pilot_only_gain_hz_per_normalized_level"]==pytest.approx(
        baseline["pilot_only_gain_hz_per_normalized_level"],abs=1e-10)
    assert got["pilot_only_frequency_offset_hz"]==pytest.approx(
        baseline["pilot_only_frequency_offset_hz"],abs=1e-10)
    assert got["heldout_frequency_waveform_rmse_hz_frozen_pilot"] >         baseline["heldout_frequency_waveform_rmse_hz_frozen_pilot"]+100


def test_complex_npy_cli_accepts_and_refuses_overwrite(tmp_path):
    path=tmp_path/"offline_complex.npy"
    np.save(path,synthetic_rf("balanced"),allow_pickle=False)
    out=tmp_path/"rx.json"
    assert main(["--iq",str(path),"--sample-rate","48000",
                 "--pattern","balanced","--report",str(out)])==0
    report=json.loads(out.read_text())
    assert report["holdout_symbol_errors"]==0
    assert report["iq_source_provenance_not_attested_by_this_tool"] is True
    assert report["physical_micin_hardware_verified"] is False
    assert report["rf_transmitted_by_this_tool"] is False
    assert report["source_file_sha256"]==hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SystemExit):
        main(["--iq",str(path),"--sample-rate","48000",
              "--pattern","balanced","--report",str(out)])


def test_interleaved_u8_resamples_known_offcenter_synthetic_signal(tmp_path):
    source=synthetic_rf("balanced")
    hi=resample_poly(source,64,3)
    offset=186_000
    mixed=hi*np.exp(2j*np.pi*offset*np.arange(len(hi))/1_024_000)
    scale=60*mixed+127.5
    packed=np.empty(len(mixed)*2,dtype=np.uint8)
    packed[::2]=np.clip(np.rint(scale.real),0,255)
    packed[1::2]=np.clip(np.rint(scale.imag),0,255)
    path=tmp_path/"u8.npy"
    np.save(path,packed)
    iq=from_file(path,rate=1_024_000,channel_offset_hz=offset)
    report,_,_,_=score(iq,"balanced")
    assert report["pilot_correlation"]>.97
    assert report["holdout_symbol_errors"]==0
    with pytest.raises(ValueError):
        from_file(path,rate=48_000,channel_offset_hz=offset)


def test_reject_no_burst_and_single_unkeyed_baseline():
    with pytest.raises(ValueError,match="no RF signal"):
        demodulate(np.zeros(int(.2*FS),complex))
    z=np.exp(2j*np.pi*400*np.arange(int(.18*FS))/FS)
    _,env=demodulate(z)
    with pytest.raises(ValueError,match="baseline"):
        active_window(env)


def test_reject_short_burst_and_wrong_pattern():
    z=synthetic_rf("balanced")
    _,env=demodulate(z)
    with pytest.raises(ValueError):
        active_window(env[:int(.055*FS)])
    got,window,freq,_=score(z,"balanced")
    assert got["holdout_symbol_errors"]==0
    try:
        wrong=acquire_and_score(freq,window,pattern="stress")
    except ValueError:
        pass
    else:
        assert wrong["holdout_symbol_errors"]>=5


def test_reject_wrong_rate_or_float_npy(tmp_path):
    z=synthetic_rf()
    p=tmp_path/"float.npy"
    np.save(p,z.real.astype(np.float32))
    with pytest.raises(ValueError):
        from_file(p,rate=FS)
    p=tmp_path/"complex.npy"
    np.save(p,z)
    with pytest.raises(ValueError):
        from_file(p,rate=1_024_000)
    with pytest.raises(ValueError):
        from_file(p,rate=FS,channel_offset_hz=30_000)


def test_no_device_or_transmitter_apis_in_receiver():
    src=(ROOT/"tools/k6_micin_rf_observe.py").read_text()
    for banned in ("import serial","import sounddevice","import pyaudio",
                   "subprocess","socket.","tx_on(","tx_pa(","run_module(",
                   "flash(","fd_write(","rtlsdr","sdr.read_samples"):
        assert banned not in src
