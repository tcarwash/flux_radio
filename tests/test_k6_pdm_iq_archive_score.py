"""Archived-IQ scoring regression; source data is synthetic, never RF."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import resample_poly

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
import k6_pdm_iq_archive_score as subject
from k6_polar_known_target import make_target

MANIFEST=json.loads((ROOT/"docs/k6-fsk-pdm300-vector.json").read_text())


def test_pdm_manifest_constant_envelope_and_distinct_payload():
    iq,meta=make_target(MANIFEST,amplitude_pattern="constant")
    assert len(iq)==1024*20
    assert meta["source_payload_sha256"]==MANIFEST["payload_sha256_le_words"]
    assert meta["amplitude_pattern"]=="constant"
    assert np.allclose(np.abs(iq),1,atol=1e-6)
    assert meta["nominal_amplitude_transition_count"]==0
    iq2,meta2=make_target(MANIFEST,amplitude_pattern="steps")
    assert not np.allclose(np.abs(iq2),1)
    assert meta2["nominal_amplitude_transition_count"]>0


def test_archive_metadata_rejects_wrong_source(tmp_path):
    p=tmp_path/"capture.npy"
    # Deliberately synthetic but properly shaped raw U8 I/Q.
    np.save(p,np.full(2_000_000,127,dtype=np.uint8))
    valid={"sample_rate_hz":1024000, "target_hz":433500000,
           "center_hz":433750000,"dummy_load_user_confirmed":True,
           "modem_frame_confirmed":True,"tx_requests":1,"vfo_restored":True,
           "module_result":{"returned":True,"result":64},
           "restored":{"transmitting":False}}
    p.with_suffix(".json").write_text(json.dumps(valid))
    _,_,digest=subject._read_archive(p)
    assert len(digest)==64
    for key,value in (("sample_rate_hz",1000000),
                      ("dummy_load_user_confirmed",False),
                      ("tx_requests",2),("vfo_restored",False),
                      ("center_hz",433500000)):
        changed={**valid,key:value}
        p.with_suffix(".json").write_text(json.dumps(changed))
        with pytest.raises(ValueError,match="metadata"):
            subject._read_archive(p)


def test_archive_pilot_fit_is_locked_and_holdout_not_reoptimized(monkeypatch):
    # Independent ideal constant-envelope target and simulated received IQ.
    target,_=make_target(MANIFEST,amplitude_pattern="constant",
                         deviation_hz=1205.23)
    pos=int(round((.820-.680)*subject.FS))
    n=int(round(.77*subject.FS))
    measured=np.zeros(n,dtype=np.complex128)
    t=np.arange(len(target))/subject.FS
    measured[pos:pos+len(target)]=.7*target*np.exp(1j*(.42+2*np.pi*22*t))
    iq128=resample_poly(measured,4,3)
    def fake_decode(raw,manifest):
        return {"known_pilot_acquired":True,"training_only_acquisition":True,
                "native_baud_estimate":4800,"pilot_correlation":.999,
                "first_fifo_bit_time_s":.820,"native_heldout_bit_errors":0,
                "native_heldout_bits":448,"calibration_half_separation_hz":1205.}
    monkeypatch.setattr(subject,"decode",fake_decode)
    monkeypatch.setattr(subject,"_rf_to_baseband",lambda raw:(iq128,.680))
    report,_=subject.score_capture(np.zeros(6,dtype=np.uint8),MANIFEST)
    score=report["complex_iq_result"]
    assert score["pilot_only_estimated_capture_offset_samples"]==int(subject.PRE_ROLL_S*subject.FS)
    assert score["pilot_only_estimated_carrier_offset_hz"]==pytest.approx(22,abs=.5)
    assert score["heldout_metrics"]["normalized_evm"]<.01
    assert report["physical_joint_fsk_apc_demonstrated"] is False
    assert report["source_rf_provenance_verifiable_from_iq_alone"] is False
    assert report["apc_amplitude_modulation_in_this_capture"] is False


def test_no_live_transmitter_or_sdr_dependencies():
    src=(ROOT/"tools/k6_pdm_iq_archive_score.py").read_text()
    for forbidden in ("import serial","import pyrtlsdr","tx_on(",
                      "fd_write(","run_module(","flash(","apc_set("):
        assert forbidden not in src
