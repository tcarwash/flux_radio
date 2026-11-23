"""TX-28 RX-only fast-SPI probe + oversampled RRC 4-FSK shaping: OFFLINE tests.

Verifies c/k6/mod/tx28_fastspi_probe.c on Cortex-M0 (Unicorn) with a bit-level
3-wire SPI slave model on GPIOB (PB3=SCN, PB5=SCK, PB6=SDA), confirming exact
SPI protocol compliance, <10 us fast-SPI write timing, safety refusals, and
RRC pulse-shaping ACPR/EVM gains in tools/k6_tx28_rrc_shaping.py.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")]

from modem import k6mod  # noqa: E402
from test_k6_i2s import START, k6_clock, need_clang, run  # noqa: E402
import k6_tx28_rrc_shaping as rrc  # noqa: E402

SRC = ROOT / "c/k6/mod/tx28_fastspi_probe.c"
MODER_DEFAULT = (1 << 6) | (1 << 10) | (1 << 12)  # PB3, PB5, PB6 = output (0b01)


def wrap(tmp_path: Path, bench: bool = True) -> Path:
    p = tmp_path / f"tx28_b{int(bench)}.c"
    p.write_text(f"#define TX28_BENCH_ENABLE {int(bench)}\n#include \"{SRC}\"\n", encoding="utf-8")
    return p


class BitLevelSpi3Wire:
    """Exact 3-wire SPI slave model attached to GPIOB PB3(SCN)/PB5(SCK)/PB6(SDA)."""

    def __init__(self, regs: dict):
        self.regs = regs
        self.moder = MODER_DEFAULT
        self.odr = 1 << 3  # SCN idle high
        self.scn_low = False
        self.bits_in = []
        self.read_mode = False
        self.read_val = 0
        self.read_idx = 0
        self.sda_out_bit = 0
        self.fast_writes = []
        self.fast_reads = []

    def on_write(self, off: int, val: int, st: dict):
        # Advance simulated time by ~3 cycles per GPIOB MMIO write (at 96 MHz)
        st["ns"] = st.get("ns", 0) + 32
        st["us"] += st["ns"] // 1000
        st["ns"] %= 1000

        if off == 0x00:
            self.moder = val
            return
        if off == 0x18:
            set_m = val & 0xFFFF
            clr_m = (val >> 16) & 0xFFFF
        elif off == 0x28:
            set_m = 0
            clr_m = val & 0xFFFF
        else:
            raise AssertionError(f"unexpected GPIOB write +{off:#x}")

        prev = self.odr
        self.odr = (self.odr | set_m) & ~clr_m & 0xFFFF

        scn_prev = (prev >> 3) & 1
        scn_now = (self.odr >> 3) & 1
        sck_prev = (prev >> 5) & 1
        sck_now = (self.odr >> 5) & 1
        sda_now = (self.odr >> 6) & 1

        if scn_prev == 1 and scn_now == 0:
            self.scn_low = True
            self.bits_in = []
            self.read_mode = False
            self.read_idx = 0
        elif scn_prev == 0 and scn_now == 1:
            if not self.read_mode and len(self.bits_in) == 24:
                word = 0
                for b in self.bits_in:
                    word = (word << 1) | b
                addr = (word >> 16) & 0x7F
                data = word & 0xFFFF
                assert addr == 0x5A, f"fast SPI addressed non-scratch reg 0x{addr:02x}"
                self.regs[addr] = data
                self.fast_writes.append((addr, data))
            self.scn_low = False
        elif self.scn_low and sck_prev == 0 and sck_now == 1:
            if not self.read_mode:
                self.bits_in.append(sda_now)
                if len(self.bits_in) == 8 and self.bits_in[0] == 1:
                    cmd = 0
                    for b in self.bits_in:
                        cmd = (cmd << 1) | b
                    addr = cmd & 0x7F
                    assert addr == 0x5A, f"fast SPI read non-scratch reg 0x{addr:02x}"
                    self.read_mode = True
                    self.read_val = self.regs.get(addr, 0)
                    self.read_idx = 0
                    self.fast_reads.append((addr, self.read_val))
            else:
                # Output MSB-first on SCK rising edge while PB6 is in input mode
                assert ((self.moder >> 12) & 3) == 0, "PB6 must be input during SPI read"
                self.sda_out_bit = (self.read_val >> (15 - self.read_idx)) & 1
                self.read_idx += 1

    def idr(self, st: dict) -> int:
        return 0x10 | (self.sda_out_bit << 6)


def rx_idle_regs() -> dict:
    return {
        **START,
        0x00: 0x4829,
        0x30: 0xBFF1,  # RX on, TX off, padrv off
        0x36: 0x0022,  # PA off
        0x3F: 0x0000,
        0x58: 0x0000,
        0x5A: 0x4242,
    }


@need_clang
def test_default_build_is_tiny_and_inert():
    code, entry = k6mod.build(str(SRC))
    assert entry == 0 and 0 < len(code) < 256
    ret, said, _, writes = run(rx_idle_regs(), moder=MODER_DEFAULT, level=2, src=str(SRC))
    assert ret == -9 and said[-1].startswith("tx28 inert")
    assert writes == []


@need_clang
@pytest.mark.parametrize(
    "mut,expected",
    [
        ({0x00: 0x4819}, -6),
        ({0x30: 0xC1FA}, -2),
        ({0x36: 0x64A2}, -2),
        ({0x58: 0x00E1}, -8),
        ({0x3F: 0x2000}, -8),
    ],
)
def test_rx_only_safety_refusals(tmp_path, mut, expected):
    src = wrap(tmp_path, bench=True)
    regs = {**rx_idle_regs(), **mut}
    ret, _, after, writes = run(regs, moder=MODER_DEFAULT, level=2, src=str(src))
    assert ret == expected
    assert writes == []
    assert after[0x5A] == regs[0x5A]


@need_clang
def test_fastspi_bit_level_unicorn_integrity_and_restore(tmp_path):
    src = wrap(tmp_path, bench=True)
    regs = rx_idle_regs()
    spi = BitLevelSpi3Wire(regs)
    mmio, rcc, _ = k6_clock(read_ns=150)

    # Share live regs dict so stock api->fd_read sees bit-banged writes immediately
    class SharedRegProxy:
        def read(self, reg, us):
            return regs.get(reg, 0)

        def write(self, reg, val, us):
            regs[reg] = val

        def key(self, name, us):
            return 0

    ret, said, after, _ = run(
        regs,
        moder=MODER_DEFAULT,
        level=2,
        src=str(src),
        mmio=mmio,
        gpiob=spi.on_write,
        pb_idr=spi.idr,
        fsk=SharedRegProxy(),
        write_us=117,
        count=200_000_000,
    )
    assert ret == 384, (ret, said)
    assert len(said) == 6
    assert len(spi.fast_writes) == 384
    assert len(spi.fast_reads) == 384
    assert regs[0x5A] == 0x4242 and after[0x5A] == 0x4242
    assert spi.moder == MODER_DEFAULT
    assert (rcc[0x18] & (1 << 18)) == 0

    # Parse every ladder line and verify 0 errors & <10 us fast-SPI write at nop=1
    us10_by_nop = {}
    for line in said:
        m = re.match(r"tx28 nop=(\d+) cyc=(\d+) us10=(\d+) err=(\d+) stk=(\d+)", line)
        assert m, line
        nop, cyc, us10, err, stk = (int(g) for g in m.groups())
        assert err == 0, line
        assert stk >= 100
        us10_by_nop[nop] = us10
    assert us10_by_nop[1] < 100, f"expected <10.0 us at nop=1, got {us10_by_nop[1]/10} us"
    assert us10_by_nop[1] <= us10_by_nop[32]


def test_rrc_shaping_4x_and_8x_quantized_reg70_beats_rectangular():
    res = rrc.run_benchmark(n_syms=512, seed=28)
    rect = res["rect_1x"]
    osr4 = res["rrc_4x_reg70"]
    osr8 = res["rrc_8x_reg70"]
    s4 = res["rrc_4x_signed"]
    s8 = res["rrc_8x_signed"]
    ideal = res["ideal_rrc"]

    # 4x oversampled quantized REG_70 (symbol-locked REG_5F) cuts ACPR by >10 dB and lowers EVM
    assert osr4["acpr_12p5khz_db"] <= rect["acpr_12p5khz_db"] - 10.0, res
    assert osr8["acpr_12p5khz_db"] <= osr4["acpr_12p5khz_db"], res
    assert osr4["rrc_evm_pct"] < rect["rrc_evm_pct"], res
    assert osr8["rrc_evm_pct"] < osr4["rrc_evm_pct"], res

    # Signed 8-bit DAC (no opposite-sign lobe clamping) removes the REG_5F floor at 8x OSR
    assert s8["acpr_12p5khz_db"] <= osr8["acpr_12p5khz_db"] - 2.0, res
    assert s8["rrc_evm_pct"] < s4["rrc_evm_pct"] < osr4["rrc_evm_pct"], res
    assert s8["rrc_evm_pct"] < 1.0, res
    assert ideal["rrc_evm_pct"] < 1.0
