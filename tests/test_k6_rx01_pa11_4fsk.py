"""Offline Cortex-M0 Unicorn + PA11 4-FSK DSP tests for RX-01 (#24)."""
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

_sim_spec = importlib.util.spec_from_file_location(
    "k6_rx01_pa11_4fsk_sim", ROOT / "tools" / "k6_rx01_pa11_4fsk_sim.py"
)
assert _sim_spec and _sim_spec.loader
sim = importlib.util.module_from_spec(_sim_spec)
_sim_spec.loader.exec_module(sim)

BENCH_SRC = ROOT / "c" / "k6" / "mod" / "rx01_pa11_flat_probe.c"

GA_MODER_ORIG = 0x28000500
GA_OSPEED_ORIG = 0x000000A0
GA_PUPD_ORIG = 0x64000000
GA_ODR_ORIG = 0x00000040


def _idle_rx_regs() -> dict[int, int]:
    return {
        0x00: 0x4829,
        0x2B: 0x0000,   # stock filters active (HPF/LPF/de-emphasis NOT bypassed)
        0x30: 0xBFF1,   # idle RX (TX bits 1 & 9 clear)
        0x36: 0x0000,   # PA bias = 0
        0x3F: 0x0000,   # FSK IRQ off
        0x47: 0x6140,   # stock AF select=1, muted
        0x58: 0x0000,   # FSK off
    }


def _bench_source(tmp_path: pathlib.Path, *, w: int = 16, log2_n: int = 5) -> pathlib.Path:
    src = (
        BENCH_SRC.read_text(encoding="utf-8")
        .replace('#include "../k6_mod.h"', '#include "k6_mod.h"')
        .replace("#define RX01_BENCH_ENABLE 0", "#define RX01_BENCH_ENABLE 1")
        .replace("#define RX01_W 90u", f"#define RX01_W {w}u")
        .replace("#define RX01_SAMPLES_LOG2 7u", f"#define RX01_SAMPLES_LOG2 {log2_n}u")
    )
    out = tmp_path / "rx01_bench.c"
    out.write_text(src, encoding="utf-8")
    return out


class Pa11DeltaSigmaModel:
    """Behavioral GPIOA + PA11 capacitor/comparator model for Cortex-M0 Unicorn tests."""

    def __init__(self, regs: dict[int, int], *, power_off_turn: int | None = None):
        self.regs = regs
        self.power_off_turn = power_off_turn
        self.gpioa = {
            0x00: GA_MODER_ORIG,
            0x08: GA_OSPEED_ORIG,
            0x0C: GA_PUPD_ORIG,
            0x14: GA_ODR_ORIG,
        }
        self.v_cap = 0.50
        self.turn = 0
        self.spk_odr = 1 << 2  # PB2 initially ON to verify mute & exact restore
        self.spk_events: list[int] = []
        self.pa11_pulses_while_spk_on = 0

    def ga_read(self, uc, off, size, st):
        if off == 0x10:
            # PA11 comparator output + optional PA15 power switch
            idr = 0
            if self.power_off_turn is not None and self.turn >= self.power_off_turn:
                idr |= 1 << 15
            # Drive synthetic audio current into PA11's 3.9 nF node:
            # stock REG_2B (0x0000) -> small low-freq ripple
            # flat REG_2B (0x0700) -> strong high-frequency wideband discriminator swing
            flat = (self.regs.get(0x2B, 0) & 0x0700) == 0x0700
            af_sel = (self.regs.get(0x47, 0) >> 8) & 0xF
            if flat and af_sel == 4:
                amp = 0.28
                freq = 0.25  # cycles per sample (high-band alternating)
            elif flat:
                amp = 0.22
                freq = 0.25
            else:
                amp = 0.05
                freq = 0.02
            sample_idx = self.turn // 16
            i_audio = amp * np.sin(2.0 * np.pi * freq * sample_idx)
            self.v_cap = float(np.clip(self.v_cap + 0.04 * i_audio, 0.0, 1.0))
            if self.v_cap >= 0.50:
                idr |= 1 << 11
            self.turn += 1
            return idr
        return self.gpioa.get(off, 0)

    def ga_write(self, uc, off, size, val, st):
        if off == 0x18:
            if val & (1 << 11):
                if self.spk_odr & (1 << 2):
                    self.pa11_pulses_while_spk_on += 1
                self.gpioa[0x14] |= 1 << 11
                self.v_cap = min(1.0, self.v_cap + 0.12)
            if val & (1 << (11 + 16)):
                self.gpioa[0x14] &= ~(1 << 11)
                self.v_cap = max(0.0, self.v_cap - 0.12)
            return
        self.gpioa[off] = val & 0xFFFFFFFF

    def gb_write(self, off, val, st):
        if off == 0x18:
            if val & (1 << 2):
                self.spk_odr |= 1 << 2
                self.spk_events.append(1)
            if val & (1 << (2 + 16)):
                self.spk_odr &= ~(1 << 2)
                self.spk_events.append(0)
            return
        raise AssertionError(f"unexpected GPIOB write off={off:#x} val={val:#x}")


@need_clang
def test_default_build_is_tiny_and_inert():
    code, _ = k6mod.build(str(BENCH_SRC))
    assert len(code) < 256
    regs = _idle_rx_regs()
    ret, said, after, writes = run(regs, level=2, src=str(BENCH_SRC))
    assert ret == -9
    assert said == []
    assert writes == []
    assert after == regs


@need_clang
@pytest.mark.parametrize(
    "mut,expected",
    [
        ({0x00: 0x4819}, -6),
        ({0x30: 0xBFF3}, -2),
        ({0x30: 0xC1FE}, -2),
        ({0x36: 0x0080}, -2),
        ({0x58: 0x0001}, -8),
        ({0x3F: 0x4000}, -8),
    ],
)
def test_rx_only_safety_refusals(tmp_path, mut, expected):
    src = _bench_source(tmp_path)
    regs = _idle_rx_regs()
    regs.update(mut)
    ret, said, after, writes = run(regs, level=2, src=str(src))
    assert ret == expected
    assert said == []
    assert writes == []
    assert after == regs


@need_clang
def test_pa11_flat_discriminator_probe_unicorn_and_restore(tmp_path):
    src = _bench_source(tmp_path, w=16, log2_n=5)  # 32 samples/burst
    regs = _idle_rx_regs()
    model = Pa11DeltaSigmaModel(regs)

    # Track GPIOB ODR (PB2 initially ON) by providing a keypad-free mmio on GPIOA and custom gpiob
    # Note: test_k6_i2s.run() returns 0 on GPIOB+0x14 unless keypad is not None;
    # set keypad=[] so st['odr'] tracks PB2 cleanly, and verify PB2 mute/restore via keypad_log!
    ret, said, after, writes = run(
        regs,
        level=2,
        src=str(src),
        mmio=[(0x48000000, 0x400, model.ga_read, model.ga_write)],
        keypad=[],
        count=200_000_000,
    )
    assert ret == 96, (ret, said)
    assert len(said) == 3

    # Verify exact register and GPIOA restoration
    assert after[0x2B] == regs[0x2B]
    assert after[0x47] == regs[0x47]
    assert model.gpioa[0x00] == GA_MODER_ORIG
    assert model.gpioa[0x08] == GA_OSPEED_ORIG
    assert model.gpioa[0x0C] == GA_PUPD_ORIG
    assert model.gpioa[0x14] == GA_ODR_ORIG

    # Verify REG_2B | 0x0700 and REG_47 select=4 were applied during bursts 1 and 2
    wrote_2b = [v for r, v in writes if r == 0x2B]
    wrote_47 = [v for r, v in writes if r == 0x47]
    assert 0x0700 in wrote_2b
    assert 0x6400 in wrote_47

    parsed = {}
    for line in said:
        m = re.match(r"rx01 b=(\d+) r2b=([0-9a-f]{4}) r47=([0-9a-f]{4}) p2p=(\d+) ac=(\d+) hf=(\d+)", line)
        assert m, line
        b = int(m.group(1))
        parsed[b] = {
            "r2b": int(m.group(2), 16),
            "r47": int(m.group(3), 16),
            "p2p": int(m.group(4)),
            "ac": int(m.group(5)),
            "hf": int(m.group(6)),
        }
    assert parsed[0]["r2b"] == 0x0000
    assert parsed[1]["r2b"] == 0x0700
    assert parsed[2]["r47"] == 0x6400
    # Flat discriminator bursts must show strictly stronger high-band diff energy than stock
    assert parsed[1]["hf"] > parsed[0]["hf"], parsed
    assert parsed[2]["hf"] > parsed[0]["hf"], parsed


@need_clang
def test_pa11_probe_key_and_power_switch_abort_restores_all(tmp_path):
    src = _bench_source(tmp_path, w=16, log2_n=5)
    for mode in ("key", "power"):
        regs = _idle_rx_regs()
        model = Pa11DeltaSigmaModel(regs, power_off_turn=100 if mode == "power" else None)
        keys = ((1000, 10_000_000),) if mode == "key" else ()
        ret, said, after, _ = run(
            regs,
            level=2,
            src=str(src),
            mmio=[(0x48000000, 0x400, model.ga_read, model.ga_write)],
            keys=keys,
            keypad=[],
            count=200_000_000,
        )
        assert ret == -4, (mode, ret, said)
        assert after[0x2B] == regs[0x2B]
        assert after[0x47] == regs[0x47]
        assert model.gpioa[0x00] == GA_MODER_ORIG
        assert model.gpioa[0x08] == GA_OSPEED_ORIG
        assert model.gpioa[0x0C] == GA_PUPD_ORIG
        assert model.gpioa[0x14] == GA_ODR_ORIG


def test_pa11_rc_ladder_physics_and_4fsk_equalizer_gain():
    prof = sim.measure_pa11_rc_profile()
    # Coupled 22k/22nF + 4.7k/3.9nF + 4.7k ladder (PA11 virtual AC gnd) places
    # dominant pole near ~1029 Hz (R_th = 24.2k || 9.4k = 6.77k) with ~7.9 dB drop by 2.4 kHz
    assert 800.0 <= prof["dominant_pole_hz"] <= 1300.0, prof
    assert prof["drop_300_to_2400hz_db"] >= 6.0, prof

    res = sim.simulate_4fsk_ser([-118.0, -114.0, -110.0, -104.0, -94.0], n_syms=1200, seed=24)
    s = res["summary"]
    # Unequalized PA11 RC ladder and stock REG_2B should suffer severe ISI even at high SNR,
    # whereas 1-zero fixed-point EQ on flat REG_2B recovers <1% SER with >25 dB margin over GP0 (-79 dBm)
    assert s["eq1_fixed_pa11"]["ser_at_high_snr"] < 0.005, s
    assert s["eq1_fixed_pa11"]["ser_at_high_snr"] < s["raw_pa11"]["ser_at_high_snr"], s
    assert s["eq1_fixed_pa11"]["ser_at_high_snr"] < s["stock_reg2b_eq1"]["ser_at_high_snr"], s
    assert s["eq1_fixed_pa11"]["sens_1pct_dbm"] is not None, s
    assert s["eq1_fixed_pa11"]["gain_vs_gp0_db"] >= 25.0, s
