"""Offline Cortex-M0 Unicorn + synthetic IQ coexistence tests for TX-32."""
from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modem import k6mod  # noqa: E402
from tests.test_k6_i2s import need_clang, run  # noqa: E402

_an_spec = importlib.util.spec_from_file_location(
    "k6_tx32_coex_analyze", ROOT / "tools" / "k6_tx32_coex_analyze.py"
)
assert _an_spec and _an_spec.loader
an = importlib.util.module_from_spec(_an_spec)
_an_spec.loader.exec_module(an)

BENCH_SRC = ROOT / "c" / "k6" / "mod" / "tx32_live_ptt_coex.c"

GA_MODER_ORIG = 0x28000500
GA_OSPEED_ORIG = 0x000000A0
GA_PUPD_ORIG = 0x64000000
GA_ODR_ORIG = 0x00000040


def _idle_rx_regs() -> dict[int, int]:
    return {
        0x00: 0x4829,
        0x02: 0x0000,
        0x0C: 0x0000,
        0x30: 0xBFF1,   # idle RX (TX/PA off)
        0x36: 0x0000,
        0x3F: 0x0000,
        0x44: 0x1234,
        0x45: 0x5678,
        0x47: 0x6140,
        0x48: 0x82DF,
        0x50: 0x0020,
        0x58: 0x0000,
        0x59: 0x00F8,
        0x5A: 0x1111,
        0x5B: 0x2222,
        0x5C: 0x3333,
        0x5D: 0x0000,
        0x67: 0x0028,   # RSSI ~ -120 dBm (clear channel)
        0x70: 0x0000,
        0x72: 0x3065,
        0x74: 0x0ABC,
        0x7D: 0xE950,
    }


def _bench_source(tmp_path: pathlib.Path, *, n_cap: int = 64, w: int = 16) -> pathlib.Path:
    src = (
        BENCH_SRC.read_text(encoding="utf-8")
        .replace('#include "../k6_mod.h"', '#include "k6_mod.h"')
        .replace("#define TX32_BENCH_ENABLE 0", "#define TX32_BENCH_ENABLE 1")
        .replace("#define TX32_DUMMY_LOAD_ACK 0", "#define TX32_DUMMY_LOAD_ACK 1")
        .replace("#define TX32_WATCHDOG_ACK 0", "#define TX32_WATCHDOG_ACK 1")
        .replace("#define TX32_NO_CABLE_WAIT 0", "#define TX32_NO_CABLE_WAIT 1")
        .replace("#define TX32_W 90u", f"#define TX32_W {w}u")
        .replace("#define TX32_N_CAP 512u", f"#define TX32_N_CAP {n_cap}u")
        .replace("#define TX32_SETTLE_US 40000u", "#define TX32_SETTLE_US 1000u")
        .replace("#define TX32_GAP_US 80000u", "#define TX32_GAP_US 1000u")
        .replace("#define TX32_CARRIER_HOLD_US 200000u", "#define TX32_CARRIER_HOLD_US 1000u")
    )
    out = tmp_path / "tx32_bench.c"
    out.write_text(src, encoding="utf-8")
    return out


class CoexHardwareModel:
    """Behavioral GPIOA PA11 + GPIOB SPI/PB2 + BK4829 FSK TX model."""

    def __init__(self, regs: dict[int, int], *, w: int = 16, power_off_turn: int | None = None):
        self.regs = regs
        self.w = w
        self.power_off_turn = power_off_turn
        self.gpioa = {
            0x00: GA_MODER_ORIG,
            0x08: GA_OSPEED_ORIG,
            0x0C: GA_PUPD_ORIG,
            0x14: GA_ODR_ORIG,
        }
        self.v_cap = 0.50
        self.turn = 0
        self.pa_on = False
        self.keyed = False
        self.fifo_words: list[int] = []
        self.frames_started = 0
        self.reg50_seen: list[int] = []
        self.reg74_seen: list[int] = []
        self.r30_bus_writes: list[int] = []

        # Bit-banged PB3/PB5/PB6 SPI decoder for bus_write_r30()
        self.pb_odr = 1 << 2  # PB2 speaker ON initially
        self.scn = 1
        self.sck = 0
        self.sda = 0
        self.bits = 0
        self.shift = 0

    def ga_read(self, uc, off, size, st):
        if off == 0x10:
            idr = 0
            if self.power_off_turn is not None and self.turn >= self.power_off_turn:
                idr |= 1 << 15
            # Inject clean 1300 Hz sine at 17.8 kHz effective sample grid when mic+afout+sel4 enabled
            r30 = self.regs.get(0x30, 0)
            r47 = self.regs.get(0x47, 0)
            mic_routed = (r30 & 0x0204) == 0x0204 and ((r47 >> 8) & 0xF) == 4
            if mic_routed:
                s_idx = self.turn / float(self.w)
                i_audio = 0.30 * np.sin(2.0 * np.pi * (1300.0 / 17800.0) * s_idx)
            else:
                i_audio = 0.0
            self.v_cap = float(np.clip(self.v_cap + 0.05 * i_audio, 0.0, 1.0))
            if self.v_cap >= 0.50:
                idr |= 1 << 11
            self.turn += 1
            return idr
        return self.gpioa.get(off, 0)

    def ga_write(self, uc, off, size, val, st):
        if off == 0x18:
            if val & (1 << 11):
                self.gpioa[0x14] |= 1 << 11
                self.v_cap = min(1.0, self.v_cap + 0.14)
            if val & (1 << (11 + 16)):
                self.gpioa[0x14] &= ~(1 << 11)
                self.v_cap = max(0.0, self.v_cap - 0.14)
            return
        self.gpioa[off] = val & 0xFFFFFFFF

    def gb_write(self, off, val, st):
        if off in (0x18, 0x28):
            set_ = val & 0xFFFF if off == 0x18 else 0
            clr = (val >> 16) & 0xFFFF if off == 0x18 else val & 0xFFFF
            self.pb_odr = (self.pb_odr | set_) & ~clr & 0xFFFF
            scn_n = (self.pb_odr >> 3) & 1
            sck_n = (self.pb_odr >> 5) & 1
            sda_n = (self.pb_odr >> 6) & 1
            if self.scn == 1 and scn_n == 0:
                self.bits = 0
                self.shift = 0
            elif scn_n == 0 and self.sck == 0 and sck_n == 1:
                self.shift = ((self.shift << 1) | sda_n) & 0xFFFFFF
                self.bits += 1
                if self.bits == 24:
                    reg = (self.shift >> 16) & 0x7F
                    data = self.shift & 0xFFFF
                    self.regs[reg] = data
                    if reg == 0x30:
                        self.r30_bus_writes.append(data)
            self.scn, self.sck, self.sda = scn_n, sck_n, sda_n
            return
        raise AssertionError(f"unexpected GPIOB write off={off:#x} val={val:#x}")

    # FSK / BK4829 register hook for test_k6_i2s.run(fsk=...)
    def read(self, reg, us):
        return self.regs.get(reg, 0)

    def write(self, reg, val, us):
        val &= 0xFFFF
        self.regs[reg] = val
        if reg == 0x50:
            self.reg50_seen.append(val)
        elif reg == 0x74:
            self.reg74_seen.append(val)
        elif reg == 0x5F:
            self.fifo_words.append(val)
        elif reg == 0x59 and (val & 0x0800):
            self.frames_started += 1
            self.regs[0x0C] = self.regs.get(0x0C, 0) | 1
        elif reg == 0x02:
            self.regs[0x0C] = self.regs.get(0x0C, 0) & ~1

    def key(self, name, us):
        if name == "tx_on":
            self.keyed = True
            self.regs[0x30] = 0xC3FA  # STATE_TX_TONE
        elif name == "tx_off":
            self.keyed = False
            self.pa_on = False
            self.regs[0x30] = 0xBFF1  # STATE_RX_ON
        return 0


@need_clang
def test_default_build_is_tiny_and_inert():
    code, _ = k6mod.build(str(BENCH_SRC))
    assert len(code) < 128
    regs = _idle_rx_regs()
    ret, said, after, writes = run(regs, level=13, rx_hz=433_500_000, src=str(BENCH_SRC))
    assert ret == -9
    assert said == []
    assert writes == []
    assert after == regs


@need_clang
@pytest.mark.parametrize(
    "mut,rx_hz,expected",
    [
        ({0x00: 0x4819}, 433_500_000, -6),
        ({}, 440_850_000, -2),
        ({0x30: 0xC1FE}, 433_500_000, -2),
        ({0x36: 0x0080}, 433_500_000, -2),
        ({0x3F: 0x8000}, 433_500_000, -8),
        ({0x67: 0x00D0}, 433_500_000, -7),  # RSSI > -90 dBm busy channel
    ],
)
def test_tx32_safety_refusals(tmp_path, mut, rx_hz, expected):
    src = _bench_source(tmp_path)
    regs = _idle_rx_regs()
    regs.update(mut)
    tx = {"log": [], "w": []}
    ret, said, after, writes = run(regs, level=13, rx_hz=rx_hz, tx=tx, src=str(src))
    assert ret == expected
    assert said == []
    assert tx["log"] == []
    assert writes == []
    assert after == regs


@need_clang
def test_tx32_six_slot_unicorn_run_and_full_restore(tmp_path):
    src = _bench_source(tmp_path, n_cap=64, w=16)
    regs = _idle_rx_regs()
    orig = dict(regs)
    hw = CoexHardwareModel(regs, w=16)
    tx = {"log": [], "w": []}

    ret, said, after, _ = run(
        regs,
        level=13,
        rx_hz=433_500_000,
        tx=tx,
        src=str(src),
        mmio=[(0x48000000, 0x400, hw.ga_read, hw.ga_write)],
        gpiob=hw.gb_write,
        fsk=hw,
        count=200_000_000,
    )
    assert ret == 6, (ret, said)
    assert len(said) == 6
    assert hw.frames_started == 6
    assert len(hw.fifo_words) == 6 * 33

    # Verify REG_30 had micin+afout enabled via bus write (0xC3FE) and restored to 0xC3FA before tx_off
    assert 0xC3FE in hw.r30_bus_writes
    assert hw.r30_bus_writes[-1] == 0xC3FA
    # Verify REG_50[15]=1 mute tested (S2 & S4) and REG_74=0 tested (S5)
    assert (orig[0x50] | 0x8000) in hw.reg50_seen
    assert 0 in hw.reg74_seen
    # Verify tx_on / tx_off balanced and EVERY register & GPIOA restored bit-for-bit
    assert tx["log"][0][0] == "tx_on"
    assert tx["log"][-1][0] == "tx_off"
    for r in (0x30, 0x3F, 0x44, 0x45, 0x47, 0x48, 0x50, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x70, 0x72, 0x74, 0x7D):
        assert regs[r] == orig[r], (hex(r), hex(regs[r]), hex(orig[r]))
    assert hw.gpioa[0x00] == GA_MODER_ORIG
    assert hw.gpioa[0x08] == GA_OSPEED_ORIG
    assert hw.gpioa[0x0C] == GA_PUPD_ORIG
    assert hw.gpioa[0x14] == GA_ODR_ORIG

    # Parse emitted Goertzel lines and confirm strong 1300 Hz contrast on PA11
    parsed = an.parse_k6_log(said)
    assert set(parsed.keys()) == {0, 1, 2, 3, 4, 5}
    for sid in range(6):
        assert parsed[sid]["g13"] > parsed[sid]["gn"], (sid, parsed[sid])


@need_clang
def test_tx32_abort_restores_all_peripherals(tmp_path):
    src = _bench_source(tmp_path, n_cap=64, w=16)
    regs = _idle_rx_regs()
    orig = dict(regs)
    hw = CoexHardwareModel(regs, w=16, power_off_turn=250)
    tx = {"log": [], "w": []}

    ret, _, _, _ = run(
        regs,
        level=13,
        rx_hz=433_500_000,
        tx=tx,
        src=str(src),
        mmio=[(0x48000000, 0x400, hw.ga_read, hw.ga_write)],
        gpiob=hw.gb_write,
        fsk=hw,
        count=200_000_000,
    )
    assert ret == -4
    assert tx["log"][-1][0] == "tx_off"
    for r in (0x30, 0x3F, 0x44, 0x45, 0x47, 0x48, 0x50, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x70, 0x72, 0x74, 0x7D):
        assert regs[r] == orig[r], (hex(r), hex(regs[r]), hex(orig[r]))
    assert hw.gpioa[0x00] == GA_MODER_ORIG
    assert hw.gpioa[0x08] == GA_OSPEED_ORIG
    assert hw.gpioa[0x0C] == GA_PUPD_ORIG
    assert hw.gpioa[0x14] == GA_ODR_ORIG


@pytest.mark.parametrize(
    "scenario,expected_verdict",
    [
        ("direct", "LIVE_PTT_DIRECT"),
        ("reg50_mute", "LIVE_PTT_REG50_MUTE"),
        ("txfilt_mute", "LIVE_PTT_TXFILT_MUTE"),
        ("pa_swamp", "PA_INTERFERENCE_ON_PA11"),
        ("fsk_hijack", "FSK_HIJACKS_AFOUT"),
        ("unmutable", "MIC_UNMUTABLE_ON_FM"),
    ],
)
def test_tx32_analyzer_classifies_all_six_scenarios_blindly(scenario, expected_verdict):
    iq, k6_lines = an.synthesize_scenario(scenario)
    res = an.analyze_coexistence(
        iq,
        fs_hz=240_000.0,
        center_hz=an.DEFAULT_CENTER_HZ,
        rf_hz=an.DEFAULT_RF_HZ,
        k6_lines=k6_lines,
    )
    assert res["bursts_detected"] == 5, res
    assert res["verdict"] == expected_verdict, res
    # On clean FSK scenarios, confirm BOTH offset-0 and displaced offset-2 (0x00F8) frames decoded blindly
    if expected_verdict == "LIVE_PTT_DIRECT":
        frames_s3 = res["rf_slots"][3]["fsk_frames"]
        assert len(frames_s3) == 2
        assert all(f["valid"] for f in frames_s3)
        assert {f["offset_bytes"] for f in frames_s3} == {0, 2}
        assert res["rf_slots"][3]["mic_bleed_suppression_db"] >= 25.0
