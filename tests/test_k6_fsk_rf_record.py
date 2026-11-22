"""Offline and synthetic tests for the strictly receive-only RTL-SDR FSK recorder."""
from pathlib import Path
import json
import subprocess
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_rf_record import (
    CAPTURE_CENTER_HZ,FS_HZ,TEST_FREQUENCY_HZ,
    summarize_raw_iq,save_capture,
)


def pack_iq(z):
    raw=np.empty(len(z)*2,dtype=np.uint8)
    raw[::2]=np.clip(np.rint(z.real+127.5),0,255).astype(np.uint8)
    raw[1::2]=np.clip(np.rint(z.imag+127.5),0,255).astype(np.uint8)
    return raw


def synthetic_iq(*,tone=None,amplitude=10.,seconds=.25,seed=4):
    rng=np.random.default_rng(seed)
    n=int(seconds*FS_HZ)
    z = rng.normal(0,2,n)+1j*rng.normal(0,2,n)
    if tone is not None:
        z += amplitude * np.exp(2j*np.pi*tone*np.arange(n)/FS_HZ)
    return pack_iq(z)


def test_noise_only_does_not_claim_clean_channel_or_fixture():
    info=summarize_raw_iq(synthetic_iq())
    assert info["sample_rate_hz"] == FS_HZ
    assert info["capture_center_hz"] == CAPTURE_CENTER_HZ
    assert info["target_rf_hz"] == TEST_FREQUENCY_HZ
    assert info["potential_occupied_or_overloaded"] is False
    assert info["channel_clear_proven"] is False
    assert info["allows_transmission"] is False
    assert info["dummy_load_verified"] is False
    assert info["attenuation_verified"] is False
    assert info["independent_watchdog_verified"] is False


def test_strong_target_signal_is_flagged():
    raw=synthetic_iq(tone=TEST_FREQUENCY_HZ-CAPTURE_CENTER_HZ,
                     amplitude=40.,seconds=.26)
    info=summarize_raw_iq(raw)
    assert info["potential_occupied_or_overloaded"]
    assert info["target_channel_peak_above_local_median_db"]>18
    assert abs(info["target_channel_peak_hz_estimate"]-TEST_FREQUENCY_HZ)<500
    assert not info["allows_transmission"]


def test_adc_clipping_is_flagged():
    raw=np.full(100000,255,dtype=np.uint8)
    info=summarize_raw_iq(raw)
    assert info["adc_extreme_sample_fraction"] == 1.0
    assert info["potential_occupied_or_overloaded"]


@pytest.mark.parametrize("raw",[
    np.arange(32768,dtype=np.float32),
    np.arange(512,dtype=np.uint8),
    np.arange(32769,dtype=np.uint8),
    np.zeros((512,512),dtype=np.uint8),
])
def test_invalid_capture_rejected(raw):
    with pytest.raises(ValueError):
        summarize_raw_iq(raw)


def test_out_of_band_target_rejected():
    with pytest.raises(ValueError,match="passband"):
        summarize_raw_iq(synthetic_iq(),rf_hz=435000000)


def test_save_preserves_exact_raw_data_and_disallows_overwrites(tmp_path):
    raw=synthetic_iq(seconds=.05)
    summary=summarize_raw_iq(raw)
    prefix=tmp_path/"capture"
    iq,manifest=save_capture(raw,prefix,summary=summary)
    assert np.array_equal(np.load(iq),raw)
    meta=json.loads(manifest.read_text())
    assert meta["capture_only"] is True
    assert meta["rf_transmission_triggered"] is False
    assert meta["allows_transmission"] is False
    assert meta["samples_complex"]==len(raw)//2
    assert len(meta["iq_raw_u8_sha256"])==64
    with pytest.raises(FileExistsError):
        save_capture(raw,prefix,summary=summary)


def test_refuses_existing_output_before_opening_hardware(tmp_path):
    prefix=tmp_path/"record"
    prefix.with_suffix(".json").write_text("{}")
    cmd=[sys.executable,str(ROOT/"tools/k6_fsk_rf_record.py"),
         "--out-prefix",str(prefix),"--seconds","1"]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
    assert result.returncode!=0
    assert "exists" in result.stderr
    assert not prefix.with_suffix(".npy").exists()


def test_no_arbitrary_tx_frequency_and_no_tx_interface(tmp_path):
    cmd=[sys.executable,str(ROOT/"tools/k6_fsk_rf_record.py"),
         "--out-prefix",str(tmp_path/"record"),
         "--target-rf-hz","144390000"]
    result=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
    assert result.returncode!=0
    assert not list(tmp_path.iterdir())
    code=(ROOT/"tools/k6_fsk_rf_record.py").read_text()
    assert "import serial" not in code
    assert "tx_on(" not in code
    assert "ptt_burst" not in code
    assert "run_module" not in code
