"""Relative SDR spectral comparison tests; synthetic captures only."""
from pathlib import Path
import json
import subprocess
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_spectrum_replay import measure_capture,RAW_FS


def iq_fixture(*,two_fsk=False,seed=8):
    fs=RAW_FS
    n=int(1.5*fs)
    t=np.arange(n,dtype=np.float64)/fs
    rng=np.random.default_rng(seed)
    switch=np.floor((t-.8)*4800).astype(np.int64)%2
    freq=-280_500 + np.where(two_fsk,1200*(2*switch-1),0.)
    phase=np.cumsum(freq)*2*np.pi/fs
    active=(t>=.79)&(t<1.04)
    amplitude=np.where(active,60.,0.)  # strong, unclipped lab-coupled carrier
    z=amplitude*np.exp(1j*phase)
    z+=.25*(rng.normal(size=n)+1j*rng.normal(size=n))
    raw=np.empty(n*2,dtype=np.uint8)
    raw[::2]=np.clip(np.rint(z.real+127.5),0,255).astype(np.uint8)
    raw[1::2]=np.clip(np.rint(z.imag+127.5),0,255).astype(np.uint8)
    return raw


def test_two_fsk_is_measurably_wider_than_carrier():
    tone=measure_capture(iq_fixture(two_fsk=False))
    fsk=measure_capture(iq_fixture(two_fsk=True))
    assert tone["estimated_99pct_occupied_bandwidth_hz"]<350
    assert fsk["estimated_99pct_occupied_bandwidth_hz"]>2000
    assert fsk["estimated_99pct_occupied_bandwidth_hz"] > tone["estimated_99pct_occupied_bandwidth_hz"]
    assert 0 <= fsk["estimated_signal_power_fraction_outside_plusminus_3khz"] <= 1
    assert not fsk["rf_power_calibrated"]
    assert not fsk["emissions_compliance_verified"]


@pytest.mark.parametrize("bad",[
    np.zeros(200,dtype=np.uint8),
    np.zeros(3_000_000,dtype=np.float32),
    np.zeros((2000,2000),dtype=np.uint8),
    np.zeros(3_000_001,dtype=np.uint8),
])
def test_rejects_invalid_iq(bad):
    with pytest.raises(ValueError,match="interleaved"):
        measure_capture(bad)


def test_empty_rx_without_burst_is_rejected():
    raw=np.zeros(3_100_000,dtype=np.uint8)
    # The huge DC error doesn't become a fake signal after mixer/decimation.
    with pytest.raises(ValueError,match="burst|power"):
        measure_capture(raw)


def test_cli_cannot_overwrite_saved_report(tmp_path):
    source=tmp_path/"iq.npy"
    np.save(source,iq_fixture(two_fsk=True))
    output=tmp_path/"stats.json"
    cmd=[sys.executable,str(ROOT/"tools/k6_fsk_spectrum_replay.py"),
         "--iq",str(source),"--out",str(output)]
    first=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
    assert first.returncode==0,first.stderr
    doc=json.loads(output.read_text())
    assert not doc["real_transmission_initiated"]
    assert len(doc["cases"])==1
    assert len(doc["cases"][0]["iq_npy_sha256"])==64
    again=subprocess.run(cmd,capture_output=True,text=True,timeout=20)
    assert again.returncode!=0
    assert "overwrite" in again.stderr


def test_script_has_no_device_interface():
    text=(ROOT/"tools/k6_fsk_spectrum_replay.py").read_text()
    for bad in ("import serial", "LiveLink(", "RtlCapture(",
                "tx_on(", "run_module(", "ptt_burst("):
        assert bad not in text
