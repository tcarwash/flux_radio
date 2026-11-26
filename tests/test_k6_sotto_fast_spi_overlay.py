"""Opt-in Sotto SPI streaming patch: reversible, isolated and fail-closed contracts.

These source/overlay tests do *not* simulate the KD32F328 SPI peripheral.
The actual Thumb firmware image must also compile before any hardware review.
"""
from pathlib import Path
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "firmware/k6-aura/apply-sotto-fast-spi.py"


def _fixture(tmp_path):
    work = tmp_path / "aura"
    spi = work / "src/hal/spi.rs"
    nor = work / "src/drivers/norflash.rs"
    pager = work / "src/app/pager.rs"
    for p in (spi, nor, pager):
        p.parent.mkdir(parents=True, exist_ok=True)
    spi.write_text(
        "impl<'a> SpiBus<'a> {\n"
        "    pub fn try_transfer_byte(&mut self, byte:u8)->Option<u8>{None}\n"
        "    /// A bounded transfer for operations that must recover from a wedged peripheral.\n"
        "    fn other() {}\n}\n"
    )
    nor.write_text(
        "impl<'a> NorFlash<'a>{\n"
        "    pub fn read_bytes_checked(&mut self,addr:u32,dst:&mut[u8])->bool{false}\n"
        "    /// `addr` and `data` together must not cross a page boundary.\n"
        "    fn other() {}\n}\n"
    )
    pager.write_text(
        (ROOT / "firmware/k6-aura/sotto-api28-resources.rs.inc").read_text()
    )
    return work, (spi, nor, pager)


def _run(work, enabled, *, success=True):
    env = dict(os.environ, K6_RX4800_WORK=str(work),
               K6_SOTTO_FAST_SPI=str(int(enabled)))
    p = subprocess.run([sys.executable, str(PATCH)], env=env,
                       capture_output=True, text=True, timeout=10)
    if success:
        assert p.returncode == 0, p.stderr + p.stdout
    else:
        assert p.returncode != 0
    return p


def test_fast_path_reversible_and_idempotent(tmp_path):
    work, paths = _fixture(tmp_path)
    original = tuple(p.read_bytes() for p in paths)
    _run(work, True)
    first = tuple(p.read_bytes() for p in paths)
    assert first != original
    assert all(b"SOTTO_FAST_SPI_" in x for x in first)
    _run(work, True)
    assert tuple(p.read_bytes() for p in paths) == first
    spi, nor, pager = (p.read_text() for p in paths)
    # The stock checked byte loop and all non-Sotto callers must stay intact.
    assert "pub fn try_transfer_byte(" in spi
    assert "pub fn read_bytes_checked(" in nor
    assert "read_bytes_checked(base,&mut h)" in pager
    assert "read_bytes_stream_checked(" in pager
    assert "read_bytes_stream_checked(" in nor
    # A Sotto read still validates kind/address/length before reaching SPI.
    assert "n>256" in pager and "n>len-off" in pager
    assert "addr>=0x200000" in pager and "n>0x200000-addr" in pager
    _run(work, False)
    assert tuple(p.read_bytes() for p in paths) == original
    _run(work, False)
    assert tuple(p.read_bytes() for p in paths) == original
    _run(work, True)
    assert tuple(p.read_bytes() for p in paths) == first


def test_stream_checks_each_rxne_and_only_final_busy(tmp_path):
    work, paths = _fixture(tmp_path)
    _run(work, True)
    spi = paths[0].read_text()
    nor = paths[1].read_text()
    segment = spi.split("// SOTTO_FAST_SPI_STREAM_BEGIN")[1].split(
        "// SOTTO_FAST_SPI_STREAM_END")[0]
    byte, stream = segment.split("pub fn try_transfer_stream(")
    assert "txe().bit_is_set()" in byte
    assert "rxne().bit_is_set()" in byte
    assert "bsy()" not in byte
    assert "write_volatile(" in byte and "as *mut u8" in byte
    assert "read_volatile(" in byte and "as *const u8" in byte
    assert stream.count("bsy().bit_is_clear()") == 1
    assert "for _ in 0..TRANSFER_SPIN_LIMIT" in stream
    assert "if self.try_stream_byte(byte).is_none() {return false;}" in stream
    assert "let Some(rx) = self.try_stream_byte(0x00) else {return false;};" in stream
    assert "return true;" in stream and stream.rstrip().endswith("}")
    # The chip-select line must be deasserted for success AND failure.
    read = nor.split("// SOTTO_FAST_SPI_NOR_BEGIN")[1].split(
        "// SOTTO_FAST_SPI_NOR_END")[0]
    assert read.index("self.cs_low()") < read.index("try_transfer_stream")
    assert read.index("try_transfer_stream") < read.index("self.cs_high()")
    assert read.index("self.cs_high()") < read.index("ok\n")


def test_fast_spi_refuses_missing_api28_without_patch(tmp_path):
    work, paths = _fixture(tmp_path)
    paths[2].write_text("no Sotto API28 service\n")
    before = tuple(p.read_bytes() for p in paths)
    p = _run(work, True, success=False)
    assert "requires API28" in p.stderr or "requires API28" in p.stdout
    assert tuple(p.read_bytes() for p in paths) == before


def test_invalid_toggle_is_rejected(tmp_path):
    work, paths = _fixture(tmp_path)
    env = dict(os.environ, K6_RX4800_WORK=str(work),
               K6_SOTTO_FAST_SPI="maybe")
    p = subprocess.run([sys.executable,str(PATCH)],env=env,
                       capture_output=True,text=True,timeout=10)
    assert p.returncode != 0
    assert "must be 0 or 1" in p.stderr+p.stdout
