"""The receive-only PA11 capture probe (c/k6/mod/pa11rx.c) on an emulated Cortex-M0,
against a modelled GPIOA whose PA11 input bit follows a scripted audio source, reusing the
module harness from test_k6_i2s (modem/k6mod build, 60-char say-line limit, refused
registers 0x30/0x33/0x36/0x37).

  muted   the pin alternates every read: every w=90 window holds exactly 45 highs, so every
          sample is identical and the variance is zero
  active  the pin is a deterministic pseudo-random bit stream: window sums wander, so the
          variance is large and the min/max spread is wide
SPDX-License-Identifier: MIT"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_k6_i2s import ROOT, START, need_clang, run  # noqa: E402

sys.path.insert(0, ROOT)
from modem import k6mod  # noqa: E402

SRC = os.path.join(ROOT, "c", "k6", "mod", "pa11rx.c")

MODER0, OSPEED0, ODR0 = 0x28000000, 0x00000000, 0x00000000
REGS = dict(START)
REGS.update({0x47: 0x6440, 0x48: 0xB3BF})


def gpioa(audio):
    """GPIOA with a scripted PA11 input bit; records every write for restore checks."""
    reg = {0x00: MODER0, 0x08: OSPEED0, 0x14: ODR0}
    m = {"reads": 0, "writes": []}

    def r(uc, off, size, st):
        if off == 0x10:
            m["reads"] += 1
            n = m["reads"]
            if audio == "muted":
                bit = n & 1
            else:
                bit = ((n * 1103515245 + 12345) >> 16) & 1
            return bit << 11
        return reg.get(off, 0)

    def w(uc, off, size, value, st):
        m["writes"].append((off, value))
        if off in reg:
            reg[off] = value & 0xFFFFFFFF

    return [(0x48000000, 0x400, r, w)], reg, m


def run_rx(audio, **kw):
    mmio, reg, m = gpioa(audio)
    kw.setdefault("keypad", [])
    ret, said, regs, writes = run(dict(REGS), src=SRC, mmio=mmio, **kw)
    return ret, said, regs, writes, reg, m


def stats(said):
    out = {}
    for ln in said:
        for kv in ln.split()[1:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                try:
                    out[k] = int(v.rstrip("m"))
                except ValueError:
                    pass
    return out


@need_clang
def test_builds_level_2():
    code, entry = k6mod.build(SRC)
    assert entry == 0 and k6mod.source_level(SRC) == 2
    assert 16 + len(code) + 8 <= 4096, len(code)


@need_clang
def test_muted_capture_is_flat_and_active_varies():
    ret, said, regs, writes, reg, m = run_rx("muted")
    assert ret == 32, said
    s = stats(said)
    assert s["n"] == 32 and s["var"] == 0, said
    assert s["lo"] == s["hi"] == s["mean"], said
    ret, said, regs, writes, reg, m = run_rx("active")
    assert ret == 32, said
    s = stats(said)
    assert s["n"] == 32 and s["var"] > 1000, said
    assert s["hi"] - s["lo"] > 10, said


@need_clang
def test_report_lines_and_registers():
    ret, said, regs, writes, reg, m = run_rx("active")
    assert ret == 32
    assert all(len(ln) <= 60 for ln in said), said
    assert said[0] == "pa11rx start n=32 h=3 w=90"
    assert said[1] == "pa11rx r47=25664 r48=46015 af untouched"
    assert said[-1].endswith(" done"), said
    assert said[-1].startswith("pa11rx t="), said
    assert regs[0x47] == 0x6440 and regs[0x48] == 0xB3BF  # read, never written
    assert writes == [], writes  # no FD6818 register written at all


@need_clang
def test_restores_pins_and_speaker():
    ret, said, regs, writes, reg, m = run_rx("muted")
    assert ret == 32
    assert reg[0x00] == MODER0, hex(reg[0x00])  # PA11 back to input
    assert reg[0x08] == OSPEED0, hex(reg[0x08])
    assert reg[0x14] == ODR0, hex(reg[0x14])  # output latch untouched
    assert all(off in (0x00, 0x08, 0x14, 0x18) for off, _ in m["writes"]), m["writes"]
    assert m["reads"] > 32 * 90  # every turn really sampled the pin


@need_clang
def test_key_abort_restores_and_reports():
    ret, said, regs, writes, reg, m = run_rx("muted", keys=[(0, 10 ** 9)])
    assert ret == -2, said
    assert said[-1] == "pa11rx key", said
    assert reg[0x00] == MODER0 and reg[0x08] == OSPEED0 and reg[0x14] == ODR0
    assert writes == []


@need_clang
def test_old_image_returns_without_touching_hardware():
    ret, said, regs, writes, reg, m = run_rx("muted", level=1)
    assert ret == -3, said
    assert said == []
    assert m["reads"] == 0 and m["writes"] == []
    assert writes == []
