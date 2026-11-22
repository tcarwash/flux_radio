"""FSK-05 known training lock, with tail held out of acquisition/threshold fit."""
from pathlib import Path
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_lab_vector import build_vector
from k6_fsk_lab_decode import _bit_truth,decode_samples,decode_archive
from k6_fsk_iq_edges import FS
from k6_fsk_legacy_holdout import make_legacy_truth

HEADER,MANIFEST=build_vector()
TRUTH=_bit_truth(MANIFEST)


def synthetic_capture(bits, *, baud=4800, seconds=1.1, start=.30,
                      seed=7, noisy=False):
    rng=np.random.default_rng(seed)
    n=int(max(seconds,start+(len(bits)+8)/baud+.075)*FS)
    t=np.arange(n)/FS
    ix=np.floor((t-start)*baud).astype(int)
    valid=(ix>=0)&(ix<len(bits))
    sym=bits[np.clip(ix,0,len(bits)-1)]
    hz=np.where(valid, np.where(sym>0,5000.,-5000.), 0.0)
    phase=2*np.pi*np.cumsum(hz)/FS
    strength=2.0 if noisy else 30.0
    z=np.where(valid,strength,0.)*np.exp(1j*phase)
    z += (rng.normal(size=n)+1j*rng.normal(size=n))*(3 if noisy else .8)
    return z


def test_known_training_acquires_synthetic_iq_and_checks_held_out_tail():
    raw=synthetic_capture(TRUTH)
    decoded=decode_samples(raw,MANIFEST)
    assert decoded["locked"],decoded
    assert decoded["held_out_tail_evaluated"]
    assert decoded["training_bits"]==512
    assert decoded["held_out_tail_bits"]==512
    assert decoded["held_out_tail_errors"]==0,decoded
    assert decoded["estimated_received_fsk_separation_hz"]==pytest.approx(10000,rel=.09)
    assert abs(decoded["detected_first_payload_bit_center_s"]-.3)<.01


def test_holdout_changes_do_not_change_acquisition_but_are_reported_as_errors():
    base=synthetic_capture(TRUTH)
    changed=TRUTH.copy()
    changed[600:635]^=1
    modified=synthetic_capture(changed)
    first=decode_samples(base,MANIFEST)
    second=decode_samples(modified,MANIFEST)
    assert second["locked"],second
    assert second["held_out_tail_errors"]>=30,second
    assert second["best_training_correlation"]==pytest.approx(first["best_training_correlation"],abs=1e-4)
    assert second["detected_first_payload_bit_center_s"]==first["detected_first_payload_bit_center_s"]


@pytest.mark.parametrize('baud',[1200,2400,3600,4800])
def test_training_and_holdout_decode_at_all_native_baud_rates(baud):
    manifest=dict(MANIFEST,native_baud=baud)
    raw=synthetic_capture(TRUTH,baud=baud)
    result=decode_samples(raw,manifest)
    assert result['locked'],result
    assert result['held_out_tail_bits']==512
    assert result['held_out_tail_errors']==0,result


def test_legacy_truth_matches_existing_decoder_format():
    for baud,nbytes in ((1200,128),(2400,128),(3600,64),(4800,64)):
        manifest=make_legacy_truth(baud,nbytes)
        assert len(_bit_truth(manifest))==nbytes*8
        assert manifest['native_baud']==baud


def test_no_signal_is_not_mistaken_for_payload():
    rng=np.random.default_rng(4)
    noise=rng.normal(size=FS)+1j*rng.normal(size=FS)
    outcome=decode_samples(noise,MANIFEST)
    assert not outcome["locked"]
    assert not outcome["held_out_tail_evaluated"]


def test_invalid_manifest_rejected_and_no_unsafe_eval():
    for key,value in (
        ("payload_bytes_hex","aaaa"),
        ("payload_sha256_le_words","1"*64),
        ("payload_bits",1234),
        ("payload_bits_msb_each_byte_lower_byte_first","foo")
    ):
        corrupted=dict(MANIFEST)
        corrupted[key]=value
        with pytest.raises((ValueError,TypeError)):
            _bit_truth(corrupted)


def test_nonrepresentable_training_length_is_rejected():
    with pytest.raises(ValueError):
        decode_samples(synthetic_capture(TRUTH),MANIFEST,train_bits=len(TRUTH))
    with pytest.raises(ValueError):
        decode_samples(synthetic_capture(TRUTH),MANIFEST,lowpass_hz=FS)


def test_read_only_source_has_no_hardware_control():
    text=(ROOT/"tools/k6_fsk_lab_decode.py").read_text()
    assert "import serial" not in text
    assert "ctypes.CDLL" not in text
    assert "tx_on(" not in text
    assert "run_module" not in text
    assert "ptt_burst" not in text
