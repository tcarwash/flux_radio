"""Post-hoc offline reproducibility of FSK-09S-G physical 0x00F8 artifact.

Preserve the separate prospectively frozen analyzer and its strict FAIL.
"""
from pathlib import Path
import hashlib
import json
import pytest
from tools.k6_fsk_lab_vector import make_probe_vector
from tools.k6_fsk_preload60_analyze import analyze_file
from tools.k6_fsk09s_frame_audit import score_payload

BASE = Path("/home/tyler/flux-bench/tx-injection-20261122/fsk09s-preload60")
IQ = BASE / "fsk09s-preload60-run01.npy"


@pytest.mark.skipif(not IQ.exists(), reason="physical local SDR archive unavailable")
def test_control_has_control_word_without_any_post_tx_fifo_writes():
    d = analyze_file(IQ)
    assert d["result"] == "FAIL_OR_INCONCLUSIVE"
    assert d["frame_candidates"] == 1
    assert d["payload_words"] == 60
    assert d["preloaded_before_tx"] == 60
    assert d["after_start_writes"] == 0
    assert d["frames"][0]["first_word_le"] == "f800"
    assert d["frames"][0]["payload_errors"] == 281
    assert d["iq_sha256"] == "2e4f9468f94cf4c29beedfd70120788b2959af663773d74108e3ef7994fbd7b1"
    words, _ = make_probe_vector(word_count=60, seed=0xACE1)
    expected = b"".join(w.to_bytes(2, "little") for w in words)
    received = bytes.fromhex(d["frames"][0]["recovered_payload_hex"])
    s = score_payload(received, expected)
    assert s["strict_complete_frame_pass"] is False
    assert s["posthoc_leading_0x00f8_bit_errors"] == 0
    assert s["posthoc_initial_48_words_exact"]
    assert s["final_declared_word_displaced_by_artifact"]


@pytest.mark.skipif(not IQ.exists(), reason="physical local SDR archive unavailable")
def test_original_module_and_operator_vfos_were_restored():
    report = json.loads((BASE / "fsk09s-preload60-run01.json").read_text())
    t = report["hardware_lab_transaction"]
    assert t["original_module_restored"]
    assert t["original_radio_frequency_restored"]
    assert t["module_completion"]["result"] == 60
    status = t["final_radio_status"]
    assert not status["transmitting"] and status["standby_idle"]
    assert [x["hz"] for x in status["vfos"]] == [440_850_000, 144_390_000]
