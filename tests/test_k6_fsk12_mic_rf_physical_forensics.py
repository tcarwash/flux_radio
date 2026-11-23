"""FSK-12 microphone-origin RF negative result and post-hoc prefix proof."""
from pathlib import Path
import json
import pytest

from tools.k6_fsk12_mic_rf_forensic import packet_diagnosis, forensic_report

ROOT=Path("/home/tyler/flux-bench/tx-injection-20261122/fsk12-mic")
IQ=ROOT/"fsk12-mic-run01.npy"
PACKETS=Path("/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin")


def test_prefix_shift_analysis_does_not_claim_pass():
    expected=bytes(range(64))
    got=bytes.fromhex("f800")+expected[:-2]
    d=packet_diagnosis(got,expected)
    assert d["posthoc_control_prefix_bit_errors"]==0
    assert d["posthoc_62_payload_bytes_exact"]
    assert d["last_two_expected_bytes_missing_from_declared_frame"]
    assert d["strict_bit_errors"]>0
    assert d["posthoc_only_not_strict_pass"]


def test_one_error_in_aligned_payload_detected():
    expected=bytes(range(64))
    got=bytearray(bytes.fromhex("f800")+expected[:-2])
    got[7]^=1
    d=packet_diagnosis(bytes(got),expected)
    assert d["posthoc_control_prefix_bit_errors"]==1
    assert not d["posthoc_62_payload_bytes_exact"]


@pytest.mark.skipif(not IQ.exists() or not PACKETS.exists(),reason="private K6 mic RF archive unavailable")
def test_original_four_frames_all_missing_crc_tail():
    a=forensic_report(IQ,PACKETS)
    assert a["prospective_strict_verdict_unchanged"]=="FAIL_OR_INCONCLUSIVE"
    assert a["diagnosis"]=="CONTROL_PREFIX_DISPLACED_CRC"
    assert a["iq_sha256"]=="cba7245ea7cd04f21fd5857a4d68ccbe3df9695039d8bfb6ea99a1c9e030c3d0"
    assert a["frames_detected"]==4
    assert a["crc_valid_as_received"]==0
    assert a["total_strict_bit_errors"]==998
    assert a["total_posthoc_prefix_bit_errors"]==0
    assert [p["strict_bit_errors"] for p in a["frames"]]==[252,248,250,248]
    assert all(p["first_received_word_le"]=="f800" for p in a["frames"])


@pytest.mark.skipif(not IQ.exists(),reason="private K6 mic RF archive unavailable")
def test_signed_slot_and_operator_vfos_restored():
    d=json.loads((ROOT/"fsk12-mic-run01.json").read_text())
    h=d["hardware_lab_transaction"]
    assert h["original_module_restored"] is True
    assert h["original_radio_frequency_restored"] is True
    assert h["module_completion"]["returned"] is True
    assert h["module_completion"]["result"]==128
    s=h["final_radio_status"]
    assert not s["transmitting"] and s["standby_idle"]
    assert [p["hz"] for p in s["vfos"]]==[440850000,144390000]
