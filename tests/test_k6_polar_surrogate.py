"""Synthetic independent-RF captures for offline K6 polar surrogate.

These tests do not open physical radio/SDR/serial and do not synthesize
transmission instructions. Their complex waveforms are generated in memory.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_polar_surrogate import analyze,FS_IN,FS_OUT


def archive(path:Path,baseband:str):
    duration=1.95
    samples=round(duration*FS_IN)
    raw=np.full(samples*2,128,dtype=np.uint8)
    if baseband=="fsk":
        on,off=.78,1.04
    else:
        on,off=.96,1.72
    a=round(on*FS_IN)
    b=round(off*FS_IN)
    ns=b-a
    t=np.arange(ns,dtype=np.float64)/FS_IN
    if baseband=="fsk":
        symbol=(np.floor(t*4800)%2)*2-1
        frequency=-280484.375+1200*symbol
        phase=np.cumsum(frequency)*(2*np.pi/FS_IN)
        amplitude=np.full(ns,5.)
    else:
        phase=-280484.375*2*np.pi*t
        amplitude=np.full(ns,5.)
        for i in range(ns):
            tt=float(t[i])
            if .08<=tt<.40:
                k=int((tt-.08)/.0206)
                amplitude[i]=5*(.8 if k%2==0 else 1.)
            elif .40<=tt<.56:
                k=int((tt-.40)/.0105)
                amplitude[i]=5*(.8 if k%2==0 else 1.)
            elif .56<=tt<.68:
                k=int((tt-.56)/.0055)
                amplitude[i]=5*(.8 if k%2==0 else 1.)
    raw[2*a:2*b:2]=np.clip(np.round(127.5+amplitude*np.cos(phase)),0,255).astype(np.uint8)
    raw[2*a+1:2*b:2]=np.clip(np.round(127.5+amplitude*np.sin(phase)),0,255).astype(np.uint8)
    np.save(path,raw)
    metadata={"sample_rate_hz":FS_IN}
    if baseband=="fsk":
        metadata.update(center_hz=433_750_000,
                        dummy_load_user_confirmed=True,modem_frame_confirmed=True)
    else:
        metadata.update(capture_center_hz=433_750_000,
                        iq_raw_u8_sha256=hashlib.sha256(raw.tobytes()).hexdigest(),
                        hardware_lab_transaction={
                            "module_completion":{"returned":True,"result":24},
                            "original_module_restored":True,
                            "original_radio_frequency_restored":True,
                        })
    path.with_suffix(".json").write_text(json.dumps(metadata))
    return path


@pytest.fixture()
def separate_iq(tmp_path):
    return (archive(tmp_path/"fsk.npy","fsk"),
            archive(tmp_path/"apc.npy","apc"))


def test_purely_offline_counterfactual_shape_and_phase_summary(separate_iq):
    fsk,apc=separate_iq
    report,arrays=analyze(fsk,apc)
    assert report["combined_polar_tx_demonstrated"] is False
    assert report["hardware_combined_iq_evm_measured"] is False
    assert report["duration_seconds"]==pytest.approx(.18)
    assert report["counterfactual_amplitude_only_evm"]<.14
    assert report["counterfactual_amplitude_only_coherent_correlation"]>.98
    assert report["fsk_phase_frequency_percentiles_hz_10_50_90"][0]<-1000
    assert report["fsk_phase_frequency_percentiles_hz_10_50_90"][2]>1000
    assert all(len(v)==int(FS_OUT*.18) for v in arrays.values())
    assert np.allclose(np.unique(arrays["desired_apc_envelope"]),[.8,1.])


def test_invalid_apc_checksum_rejected(separate_iq):
    fsk,apc=separate_iq
    meta_path=apc.with_suffix(".json")
    meta=json.loads(meta_path.read_text())
    meta["iq_raw_u8_sha256"]="bad"
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match="checksum"):
        analyze(fsk,apc)


def test_no_combined_tx_claim_and_no_hardware_interface():
    source=(ROOT/"tools/k6_polar_surrogate.py").read_text()
    for forbidden in ("import serial","import pyrtlsdr","from k6_live",
                      "tx_on(","fd_write(","mutate_library(","run_module(",
                      "flash(","GenUI."):
        assert forbidden not in source
    assert "combined_polar_tx_demonstrated" in source
    assert "counterfactual_amplitude_only_evm" in source
    assert "No joint actuator timing" in source
