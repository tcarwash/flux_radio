"""FSK-12: private microphone-originated Sotto RF fixture (NO hardware)."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from modem import k6mod, pager
from tools import k6_fsk12_mic_rf as rf

ROOT = Path(__file__).resolve().parents[1]
PACKETS = Path("/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin")
PRIVATE = Path("/home/tyler/flux-bench/micvoice-20261123/fsk12-mic-rf-private-20261123")
ARCHIVED = Path("/home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-run01.npy")


@pytest.mark.skipif(not PACKETS.exists() or not PRIVATE.exists(), reason="private K6 mic fixture unavailable")
def test_pinned_private_mic_data_compiles_deterministically():
    m = rf.frozen_preflight(PACKETS, PRIVATE)
    assert m["compiled_module_bytes"] == 1196
    assert m["compiled_module_sha256"] == rf.FROZEN_CODE_SHA
    assert m["source_sha256"] == rf.FROZEN_SOURCE_SHA
    src = (PRIVATE / "fsk12_k6mic_private_armed.c").read_text()
    assert "#define FSK_NUM_FRAMES 4u" in src
    assert "#define FSK_MAX_TX_MS 1900u" in src
    assert "for(frame=0;frame<FSK_NUM_FRAMES;frame++)" in src
    assert "W(R_LENGTH,0x3f00u);" in src
    assert "wait_until(" not in src and "fd_read(R_FIFO)" not in src


@pytest.mark.skipif(not PACKETS.exists(), reason="private K6 mic fixture unavailable")
def test_four_actual_mic_packets_validate_crc_sequence_and_voice():
    ps = rf.packets_from_file(PACKETS)
    assert len(ps) == 4
    assert [len(p) for p in ps] == [64]*4
    assert hashlib.sha256(b"".join(ps)).hexdigest() == rf.PACKET_SHA
    assert [pager.profile("L", "1200", depth=1, parity=False, short_last=True).unpack(p)["seq"]
            for p in ps] == list(range(4))


def test_bad_packet_hash_rejected(tmp_path):
    p = tmp_path / "corrupt.bin"
    p.write_bytes(bytes(4*64))
    with pytest.raises(ValueError, match="differs"):
        rf.packets_from_file(p)


def test_tx_refuses_missing_hardware_lease(tmp_path):
    p = subprocess.run(["python3", str(ROOT / "tools/k6_fsk12_mic_rf.py"),
                        "tx", "--packets", str(tmp_path / "absent.bin"),
                        "--private-dir", str(tmp_path / "private"),
                        "--prefix", str(tmp_path / "would-be-test")],
                       cwd=ROOT, capture_output=True, text=True, timeout=15)
    assert p.returncode != 0
    assert "operator lease" in p.stderr.lower()
    assert not (tmp_path / "would-be-test.npy").exists()


@pytest.mark.skipif(not ARCHIVED.exists() or not PACKETS.exists(), reason="archived SDR/private source missing")
def test_different_prior_sotto_rf_does_not_false_pass(tmp_path):
    out = tmp_path / "fsk12-negative-control.json"
    r = rf.analyze(ARCHIVED, out, PACKETS)
    assert out.exists()
    assert r["result"] == "FAIL_OR_INCONCLUSIVE"
    assert not (r["found_packets"] == 4 and r["correct_packets_exact"] == 4)
    assert r["receiver_uses_only_public_preamble_sync"]
    assert r["receiver_rate_hz_frozen"] == 4800
