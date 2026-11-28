"""Opt-in full compiled ARM firmware and independent virtual IQ qualification.

Requires explicitly paired current ARM ELF and binary generated offline by
K6_FSK4800_RX=1. No hardware actions or implicit recompilation.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tools.k6_sotto_rx4800_firmware_gate import matching_image,run

ROOT=Path(__file__).resolve().parents[1]


def test_firmware_gate_rejects_mismatched_image_before_sim(tmp_path):
    binary=tmp_path/"wrong.bin"
    binary.write_bytes(b"unsafe incomplete radio image")
    elf=tmp_path/"wrong.elf"
    elf.write_bytes(b"not an ELF")
    # llvm-objcopy refuses malformed ELF or image mismatch.
    with pytest.raises(subprocess.CalledProcessError):
        matching_image(elf,binary,tmp_path)


def test_real_linked_arm_firmware_rx31_soapyfang_iq(tmp_path):
    image=os.environ.get("K6_SIM_SOTTO_RX31_IMAGE")
    elf=os.environ.get("K6_SIM_SOTTO_RX31_ELF")
    if not image or not elf:
        pytest.skip("explicit paired K6_SIM_SOTTO_RX31_IMAGE and ELF required")
    broken=bytearray(Path(image).read_bytes())
    broken[-10]^=1
    stale=tmp_path/"altered-firmware.bin"
    stale.write_bytes(broken)
    with pytest.raises(RuntimeError,match="mismatch"):
        matching_image(Path(elf),stale,tmp_path)
    report=run(Path(image),Path(elf),ROOT)
    assert report["result"]=="PASS"
    assert report["actual_api_table_verified"]
    assert report["real_arm_tx"] and report["real_arm_firmware_rx"]
    assert report["received_rf_packets"]==report["expected_rf_packets"]==6
    assert report["codec_frames"]==30
    assert report["rx_overruns"]==report["rx_underflows"]==0
    assert all(ms<1.2 for ms in report["fast_rearm_ms"])
    assert report["ptt_timeout_chipid_cleanup"]
    assert not report["physical_rf"] and report["uses_debug_context_injection"]
