"""Offline unit tests for Flux APC RF-IQ envelope analysis (NO transmitter)."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_apc_rf_analyze import FS, analyze_capture, group_events, STAGES


def test_contiguous_fade_event_clusters():
    inds = np.array([2, 3, 5, 8, 9, 10, 17], dtype=np.int64)
    assert group_events(inds) == [(2, 3), (5, 5), (8, 10), (17, 17)]
    assert group_events(np.array([], dtype=np.int64)) == []


def write_synthetic_capture(tmp_path: Path, *, fades: bool=False) -> Path:
    """Seven ideal 100-ms amplitude plateaus, with optional injected RF fades."""
    path=tmp_path/"synth.npy"
    samples=int(5.12*FS)
    raw=np.full(samples*2,128,dtype=np.uint8)
    t0=.98
    f0=-280_484.375
    for j,pct in enumerate(STAGES):
        begin=int((t0+j*.1)*FS)
        stop=int((t0+(j+1)*.1)*FS)
        t=np.arange(begin,stop,dtype=np.float64)/FS
        amp=np.full(len(t), 5*pct/100.,dtype=np.float64)
        if fades and pct==70:
            # Identical ~8ms-spaced dropouts inside central 50ms.
            for k in range(7):
                z=t0+j*.1+.029+k*.008
                amp[(t>=z)&(t<z+.0015)]=0.
        phase=2*np.pi*f0*t
        raw[begin*2:stop*2:2] = np.clip(np.round(127.5+amp*np.cos(phase)),0,255).astype(np.uint8)
        raw[begin*2+1:stop*2:2] = np.clip(np.round(127.5+amp*np.sin(phase)),0,255).astype(np.uint8)
    np.save(path,raw)
    manifest={
        "sample_rate_hz":FS,
        "capture_center_hz":433_750_000,
        "iq_raw_u8_sha256":hashlib.sha256(raw.tobytes()).hexdigest(),
        "manual_gain_tenth_db":0,
        "hardware_lab_transaction":{
            "module_completion":{"returned":True,"result":7},
            "original_module_restored":True,
            "original_radio_frequency_restored":True,
        },
    }
    path.with_suffix(".json").write_text(json.dumps(manifest))
    return path


@pytest.mark.parametrize("with_fades",[False,True])
def test_synthetic_staircase_recovers_relative_levels(tmp_path,with_fades):
    path=write_synthetic_capture(tmp_path,fades=with_fades)
    out=analyze_capture(path)
    assert out["event_duration_seconds"] == pytest.approx(.7,abs=.015)
    levels=[s["carrier_amplitude_relative_to_full_percent"] for s in out["stages"]]
    assert np.allclose(levels,STAGES,atol=5), levels
    assert all(s["deep_fade_bins_1ms"] == 0 for i,s in enumerate(out["stages"]) if i!=3)
    assert (out["stages"][3]["deep_fade_events"] > 0) == with_fades


def test_reject_bad_sha_before_processing(tmp_path):
    path=write_synthetic_capture(tmp_path)
    meta=path.with_suffix(".json")
    obj=json.loads(meta.read_text())
    obj["iq_raw_u8_sha256"]="0"*64
    meta.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match="checksum"):
        analyze_capture(path)


def test_requires_verified_tx_and_restoration(tmp_path):
    path=write_synthetic_capture(tmp_path)
    meta=path.with_suffix(".json")
    obj=json.loads(meta.read_text())
    obj["hardware_lab_transaction"]["original_module_restored"]=False
    meta.write_text(json.dumps(obj))
    with pytest.raises(ValueError,match="completed seven-step"):
        analyze_capture(path)
