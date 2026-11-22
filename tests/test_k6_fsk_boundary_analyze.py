"""Offline RF boundary analysis tests, no device dependency."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_boundary_analyze import (
    FS,TRAIN,BITS,BAUD,compute_boundary,known_frames
)


def _cases(*,t1=.83,nonpayload=.0467187):
    t2=t1+BITS/BAUD+nonpayload
    a=dict(locked=True,training_bits=TRAIN,held_out_tail_bits=BITS-TRAIN,
           held_out_tail_errors=0,best_training_correlation=.997,
           detected_first_payload_bit_center_s=t1)
    b=dict(a,detected_first_payload_bit_center_s=t2)
    return a,b


def _synthetic_carrier(*,pause=False):
    t=np.arange(int(FS*1.7))/FS
    phase=2*np.pi*np.cumsum(np.ones(len(t))*(-1200))/FS
    amp=np.where((t>.76)&(t<1.43),10.,.2)
    if pause:
        amp=np.where((t>1.048)&(t<1.064),.05,amp)
    return amp*np.exp(1j*phase)


def test_known_word_vector_uses_exact_zero_marker():
    a,b=known_frames()
    assert len(a["payload_bits_msb_each_byte_lower_byte_first"])==1024
    assert len(b["payload_bits_msb_each_byte_lower_byte_first"])==1024
    assert a["payload_bits_msb_each_byte_lower_byte_first"][-128:]=="0"*128
    assert b["payload_bits_msb_each_byte_lower_byte_first"][:128]=="0"*128
    assert a["payload_sha256_le_words"]!=b["payload_sha256_le_words"]


def test_calculates_nonpayload_interval_with_unbroken_carrier():
    a,b=_cases()
    result=compute_boundary(a,b,_synthetic_carrier())
    assert result["additional_rearm_and_framing_time_ms"]==pytest.approx(46.719,abs=.005)
    assert result["equivalent_4800_bit_chips_not_payload"]==pytest.approx(
        46.7187e-3*BAUD,abs=.05)
    assert result["first_frame_heldout_bit_errors"]==0
    assert result["second_frame_heldout_bit_errors"]==0
    assert result["minimum_boundary_envelope_fraction_vs_holds"]>.9
    assert result["carrier_absence_established"] is False
    assert result["rf_streaming_established"] is False
    assert result["rf_phase_continuity_established"] is False


def test_detects_real_envelope_dropout_without_claiming_phase():
    a,b=_cases()
    result=compute_boundary(a,b,_synthetic_carrier(pause=True))
    assert result["carrier_absence_established"] is True
    assert result["rf_phase_continuity_established"] is False


@pytest.mark.parametrize("change",[
    lambda a,b:(dict(a,locked=False),b),
    lambda a,b:(dict(a,held_out_tail_errors=8),b),
    lambda a,b:(a,dict(b,best_training_correlation=.4)),
    lambda a,b:(a,dict(b,detected_first_payload_bit_center_s=1.8)),
])
def test_rejects_bad_acquisition(change):
    a,b=change(*_cases())
    with pytest.raises(ValueError):
        compute_boundary(a,b,_synthetic_carrier())


def test_bad_iq_rejected():
    with pytest.raises(ValueError):
        compute_boundary(*_cases(),np.zeros(1024))


def test_source_has_no_device_controls():
    src=(ROOT/"tools/k6_fsk_boundary_analyze.py").read_text()
    for word in ("import serial","from k6_live","run_module(","tx_on(",
                 "mutate_library(","RtlCapture(","ptt_burst("):
        assert word not in src
