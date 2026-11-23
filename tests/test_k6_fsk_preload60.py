"""Prospective FSK-09S-G offline-only control and safe-build checks."""
from __future__ import annotations

import subprocess
from pathlib import Path
from tools.k6_fsk_preload60_bench import SOURCE, frozen_build
from tools.k6_fsk_preload60_analyze import analyze_file
from modem import k6mod
import pytest

ROOT = Path(__file__).resolve().parents[1]
INERT = ROOT / "c/k6/mod/fsk_preload60_control.c"
ARCHIVED_F = Path("/home/tyler/flux-bench/tx-injection-20261122/fsk09s-threebatch/fsk09s-threebatch-run01.npy")


def test_frozen_control_module_compiles_and_has_no_live_fifo():
    meta = frozen_build()
    assert meta["code_bytes"] == 1056
    source = SOURCE.read_text()
    assert "#define FSK_WORDS 60u" in source
    assert "#define FSK_MAX_TX_MS 1900u" in source
    assert "#define FSK_FRAME_TIMEOUT_MS 700u" in source
    assert source.count("W(R_LENGTH,0x7700u);") == 2
    assert source.count("for(i=0;i<FSK_WORDS;i++)") == 1
    assert "W(R_FIFO,word_for(frame,i));" in source
    assert "wait_until" not in source and "for(batch=" not in source
    assert "fd_read(R_FIFO)" not in source
    assert source.count("W(R_CONTROL,0x80f8u)") == 1
    assert source.count("W(R_CONTROL,0x08f8u)") == 1


def test_default_is_inert_and_uses_same_pinned_rf_parameters():
    source = INERT.read_text()
    assert "#define FSK_BOUNDARY_ENABLE 0" in source
    assert "#define FSK_BOUNDARY_FIXTURE_ACK 0" in source
    assert "#define FSK_BOUNDARY_WATCHDOG_ACK 0" in source
    code, entry = k6mod.build(str(INERT))
    assert 0 < len(code) < 4096 and entry == 0
    assert b"\x00" in code  # valid module has binary instructions
    original = (ROOT / "c/k6/mod/fsk_threebatch_append_armed.c").read_text()
    for line in ("#define FSK_FREQUENCY_HZ 433500000u",
                 "#define FSK_GAIN_CODE 21u", "#define FSK_BAUD_CODE 0xc194u",
                 "W(R_SYNC0,0x076bu);", "W(R_SYNC1,0x3e36u);",
                 "W(R_LENGTH,0x7700u);"):
        assert line in original and line in SOURCE.read_text()


def test_bench_refuses_without_explicit_operator_lease():
    p = subprocess.run(["python3", "tools/k6_fsk_preload60_bench.py",
                        "--prefix", "/tmp/not-a-hardware-test"],
                       cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert p.returncode != 0
    assert "operator lease" in p.stderr.lower()


@pytest.mark.skipif(not ARCHIVED_F.exists(), reason="local archived RF IQ not present")
def test_control_receiver_strictly_rejects_archived_f_failure():
    # Its raw payload has 0x00F8 and only 59 expected words; no false PASS.
    result = analyze_file(ARCHIVED_F)
    assert result["result"] == "FAIL_OR_INCONCLUSIVE"
    assert result["frame_candidates"] == 1
    assert result["frames"][0]["first_word_le"] == "f800"
    assert result["frames"][0]["selected_baud"] == 4800
