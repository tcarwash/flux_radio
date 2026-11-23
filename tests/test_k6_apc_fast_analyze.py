"""Synthetic receiver-IQ validation of APC fast envelope analysis (no hardware)."""
from pathlib import Path
import hashlib
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_apc_fast_analyze import analyze,FS


def make_iq(tmp_path:Path, *, gain_lag=False) -> Path:
    """Synthetic waveform with realistic host callback timing overhead."""
    n=int(2.2*FS)
    raw=np.full(n*2,128,dtype=np.uint8)
    t0=.96
    segs=[(1.,.080)]
    for i in range(8):
        segs.extend(((.80,.0206),(1.,.0206)))
    for i in range(8):
        segs.extend(((.80,.0105),(1.,.0105)))
    for i in range(12):
        segs.extend(((.80,.0055),(1.,.0055)))
    segs.append((1.,.080))
    start=t0
    for level,duration in segs:
        a=int(start*FS)
        b=int((start+duration)*FS)
        tt=np.arange(a,b,dtype=np.float64)/FS
        carrier=2*np.pi*(-280484.375)*tt
        amp=4.2*level
        raw[2*a:2*b:2]=np.clip(np.round(127.5+amp*np.cos(carrier)),0,255).astype(np.uint8)
        raw[2*a+1:2*b:2]=np.clip(np.round(127.5+amp*np.sin(carrier)),0,255).astype(np.uint8)
        start+=duration
    p=tmp_path/"synthetic.npy"
    np.save(p,raw)
    p.with_suffix(".json").write_text(json.dumps({
        "sample_rate_hz":FS,"capture_center_hz":433750000,
        "iq_raw_u8_sha256":hashlib.sha256(raw.tobytes()).hexdigest(),
        "hardware_lab_transaction":{
            "module_completion":{"returned":True,"result":24},
            "original_module_restored":True,
            "original_radio_frequency_restored":True,
        }
    }))
    return p


def test_periodogram_detects_correct_actual_timing_and_full_swing(tmp_path):
    path=make_iq(tmp_path)
    out=analyze(path)
    assert .74<out["duration_seconds"]<.82
    rates=[x["measured_peak_full_cycle_hz"] for x in out["groups"]]
    assert abs(rates[0]-24.27)<2
    assert abs(rates[1]-47.62)<3
    assert abs(rates[2]-90.91)<5
    assert out["baseline_full_carrier_adc"]>3.5
    # Synthetic ideal step response should retain an appreciable 5-ms swing.
    # The actual hardware's ~40% retention therefore is not a simple
    # fitting-window or SDR-only artifact of this analysis.
    assert out["groups"][2]["swing_relative_to_20ms_percent"]>65
    assert out["groups"][0]["equivalent_high_low_amplitude_swing_adc"]>.40


def test_checksum_refusal(tmp_path):
    path=make_iq(tmp_path)
    p=path.with_suffix(".json")
    meta=json.loads(p.read_text())
    meta["iq_raw_u8_sha256"]="0"*64
    p.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="checksum"):
        analyze(path)


def test_unverified_radio_restoration_refused(tmp_path):
    path=make_iq(tmp_path)
    p=path.with_suffix(".json")
    meta=json.loads(p.read_text())
    meta["hardware_lab_transaction"]["original_module_restored"]=False
    p.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="restoration"):
        analyze(path)
