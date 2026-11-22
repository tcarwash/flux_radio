"""Offline-only relative RF activity analysis of archived RTL-SDR I/Q."""
from pathlib import Path
import json
import subprocess
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_rf_activity import (
    analyze,FS_HZ,CENTER_HZ,TARGET_HZ,WINDOW_SAMPLES,
)


def fake_iq(*,strong=False):
    rng=np.random.default_rng(56)
    ns=WINDOW_SAMPLES*60
    t=np.arange(ns)/FS_HZ
    x=(rng.normal(0,1.3,ns)+1j*rng.normal(0,1.3,ns))
    if strong:
        active=(t>.85)&(t<1.3)
        x+=np.where(active,20.0,0.0)*np.exp(
            2j*np.pi*(TARGET_HZ-CENTER_HZ-30000)*t)
    out=np.empty(ns*2,dtype=np.uint8)
    out[::2]=np.clip(np.rint(x.real+127.5),0,255).astype(np.uint8)
    out[1::2]=np.clip(np.rint(x.imag+127.5),0,255).astype(np.uint8)
    return out


def test_noise_does_not_have_rf_activity():
    result=analyze(fake_iq())
    assert result["active_window_count"]==0
    assert result["first_active_window_s"] is None
    assert result["early_quiet_relative"] and result["late_quiet_relative"]
    assert result["calibrated_rf_power_dbm"] is None
    assert result["modulation_identified"] is False


def test_burst_is_detected_and_quiet_before_after():
    result=analyze(fake_iq(strong=True))
    assert result["active_window_count"]>=7
    assert .8 <=result["first_active_window_s"]<=.9
    assert 1.2<=result["last_active_window_s"]<=1.35
    assert result["peak_spectral_contrast_db"]>25
    assert abs(result["apparent_combined_tx_rx_offset_hz"]+30000)<400
    assert result["early_quiet_relative"] and result["late_quiet_relative"]
    assert result["no_transmission_initiated"]


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        analyze(np.zeros(200,dtype=np.uint8))
    with pytest.raises(ValueError):
        analyze(np.zeros(2*WINDOW_SAMPLES*3,dtype=np.float32))
    with pytest.raises(ValueError):
        analyze(fake_iq(),target_hz=433000000)
    with pytest.raises(ValueError):
        analyze(fake_iq(),min_spectral_contrast_db=-1)


def test_cli_reads_npy_and_writes_manifest_without_overwrite(tmp_path):
    source=tmp_path/"iq.npy"
    np.save(source,fake_iq(strong=True))
    output=tmp_path/"report.json"
    args=[sys.executable,str(ROOT/"tools/k6_fsk_rf_activity.py"),
          "--iq",str(source),"--out",str(output)]
    a=subprocess.run(args,capture_output=True,text=True,timeout=20)
    assert a.returncode==0,a.stderr
    doc=json.loads(output.read_text())
    assert doc["active_window_count"]>=7
    assert len(doc["source_npy_sha256"])==64
    assert doc["offline_only"] and doc["no_transmission_initiated"]
    b=subprocess.run(args,capture_output=True,text=True,timeout=20)
    assert b.returncode!=0
    assert "existing" in b.stderr


def test_source_is_offline_and_has_no_radio_control():
    src=(ROOT/"tools/k6_fsk_rf_activity.py").read_text()
    for forbidden in ("import serial", "ctypes.CDLL", "tx_on(",
                      "run_module(", "ptt_hold(", "rtl_sdr_open"):
        assert forbidden not in src
