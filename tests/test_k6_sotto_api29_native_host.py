"""Compile the ACTUAL API29 Rust callback and exercise a deterministic RFIC mock.

The Rust harness includes the production .rs.inc verbatim. There is no
serial access, radio, SDR, firmware flash, or RF transmission.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from modem import pager
from tools.k6_sotto_tail_timing import test_vector as known_packet

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests/data/k6/sotto_api29_native_host.rs"
PRODUCTION = ROOT / "firmware/k6-aura/sotto-api29-async-fsk.rs.inc"


@pytest.mark.skipif(shutil.which("rustc") is None, reason="native Rust compiler missing")
def test_unmodified_api29_callback_in_register_and_fifo_fault_mock(tmp_path):
    source = HARNESS.read_text()
    assert 'include!("../../../firmware/k6-aura/sotto-api29-async-fsk.rs.inc");' in source
    assert 'unsafe extern "C" fn mod_sotto_async_fsk(' in PRODUCTION.read_text()
    assert 'SOTTO_API29_KNOWN_HEX' in source

    physical = known_packet()
    assert len(physical) == 66 and physical[-2:] == bytes.fromhex("5aa5")
    profile = pager.profile("L", "1200", depth=1, parity=False, short_last=True)
    assert profile.unpack(physical[:64]) is not None

    exe = tmp_path / "api29-native-test"
    build = subprocess.run(
        ["rustc", "--edition=2021", "--test", "--cfg", "k6_sotto_async_fsk",
         "-A", "warnings", str(HARNESS), "-o", str(exe)],
        capture_output=True, text=True, timeout=60
    )
    assert build.returncode == 0, build.stdout + build.stderr

    run = subprocess.run(
        [str(exe), "--nocapture", "--test-threads=1"],
        env={**os.environ, "SOTTO_API29_KNOWN_HEX": physical.hex()},
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    assert "9 passed; 0 failed" in run.stdout

    pattern = re.compile(
        r"CASE (\w+) rc=(-?\d+) off_ms=(\d+) words=(\d+) bits=(\d+) bytes=(\d+)"
    )
    found = {
        name: tuple(map(int, (rc, off, words, bits, count)))
        for name, rc, off, words, bits, count in pattern.findall(run.stdout)
    }
    assert found == {
        # Return codes are from unchanged mod_sotto_async_fsk(), not a port.
        "nominal": (1, 165, 33, 528, 66),
        "early_done": (1, 145, 33, 439, 54),
        "early_done_jitter": (1, 150, 33, 444, 55),
        "capacity32": (1, 165, 33, 512, 64),
        "ready_drop": (-2, 150, 33, 463, 57),
    }
    assert "CASE explicit_abort rc=0 off_ms=70" in run.stdout
