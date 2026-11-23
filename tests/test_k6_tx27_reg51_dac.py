"""TX-27 FD6818B REG_51 signed 8-bit DC frequency DAC: OFFLINE tests.

Executes c/k6/mod/tx27_reg51_dac.c on the emulated Cortex-M0 (Unicorn), checks
all safety/restoration invariants across clock/bandwidth variants, and feeds
the emitted REG_51 register trace into the independent synthetic FM-discriminator
analyzer (including a negative control where REG_51[6:0] gain is ignored in DCS).
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests"), str(ROOT / "tools")]

from modem import k6mod  # noqa: E402
from test_k6_i2s import START, k6_clock, need_clang, rssi_reg, run  # noqa: E402
import k6_tx27_reg51_dac_analyze as tx27  # noqa: E402

SRC = ROOT / "c/k6/mod/tx27_reg51_dac.c"
HZ = 433_500_000


def wrap(
    tmp_path: Path,
    *,
    bench: bool = True,
    rf: bool = False,
    fixture: bool = True,
    watchdog: bool = True,
    clk_word: int = 0x0AD7,
    bw_disable: int = 0,
    hz: int = HZ,
) -> Path:
    p = tmp_path / f"tx27_b{int(bench)}_rf{int(rf)}_c{clk_word:04x}_bw{bw_disable}.c"
    p.write_text(
        f"#define TX27_BENCH_ENABLE {int(bench)}\n"
        f"#define TX27_RF_ENABLE {int(rf)}\n"
        f"#define TX27_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX27_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX27_CLK_WORD 0x{clk_word:04X}u\n"
        f"#define TX27_BW_DISABLE {bw_disable}\n"
        f"#define TX27_HZ {hz}u\n"
        f'#include "{SRC}"\n',
        encoding="utf-8",
    )
    return p


def baseline_regs() -> dict:
    return {
        **START,
        0x00: 0x4829,
        0x30: 0xBFF1,
        0x51: 0x0000,
        0x52: 0x0292,
        0x67: rssi_reg(-120),
        0x70: 0x0000,
        0x71: 0x1234,
    }


@need_clang
def test_default_build_is_tiny_and_inert():
    code, entry = k6mod.build(str(SRC))
    assert entry == 0 and 0 < len(code) < 256
    tx = {}
    ret, said, _, _ = run(baseline_regs(), level=3, src=str(SRC), rx_hz=HZ, tx=tx)
    assert ret == -9
    assert said[-1].startswith("tx27 inert")
    assert tx.get("w", []) == [] and tx.get("log", []) == []


@need_clang
def test_rf_build_refuses_missing_acks(tmp_path):
    bad = wrap(tmp_path, bench=True, rf=True, fixture=True, watchdog=False)
    with pytest.raises(Exception):
        k6mod.build(str(bad))


@need_clang
@pytest.mark.parametrize(
    "mut,rx_hz,expected_ret",
    [
        ({0x00: 0x4819}, HZ, -6),
        ({}, 144_390_000, -2),
        ({0x67: rssi_reg(-75)}, HZ, -7),
        ({0x51: 0x9037}, HZ, -13),
    ],
)
def test_runtime_preflight_refusals(tmp_path, mut, rx_hz, expected_ret):
    src = wrap(tmp_path, rf=True)
    regs = {**baseline_regs(), **mut}
    tx = {}
    ret, _, after, _ = run(regs, level=3, src=str(src), rx_hz=rx_hz, tx=tx)
    assert ret == expected_ret
    assert after[0x51] == regs[0x51]


@need_clang
@pytest.mark.parametrize("rf,clk_word,bw_dis", [(False, 0x0AD7, 0), (True, 0x0AD7, 0), (True, 0x1FFF, 1)])
def test_tx27_end_to_end_unicorn_and_signed_4fsk_decode(tmp_path, rf, clk_word, bw_dis):
    src = wrap(tmp_path, rf=rf, clk_word=clk_word, bw_disable=bw_dis)
    code, entry = k6mod.build(str(src))
    assert entry == 0 and len(code) < 4096

    before = baseline_regs()
    mmio, rcc, _ = k6_clock(read_ns=250)
    tx = {}
    ret, said, after, _ = run(
        before,
        src=str(src),
        level=3,
        rx_hz=HZ,
        tx=tx,
        mmio=mmio,
        write_us=117,
        count=200_000_000,
    )
    assert ret == 90, (ret, said)
    assert f"tx27 ok w=121 late=0 bw={bw_dis} pa={int(rf)}" == said[-1]
    assert tx["log"][0][:2] == ("tx_on", 1800)
    assert tx["log"][-1][0] == "tx_off"
    assert [x[1] for x in tx["log"] if x[0] == "tx_pa"] == ([0, 1, 0] if rf else [0, 0])
    assert max(t for _, _, t in tx["w"]) < 1_800_000

    # Exact restore of non-banked registers and TIM17 clock
    for r in (0x51, 0x52, 0x70, 0x71):
        assert after[r] == before[r], f"reg 0x{r:02x} not restored"
    assert (rcc[0x18] & (1 << 18)) == 0

    # Verify REG_07 clock word and all-ones REG_08 setup, and never touch FSK regs
    touched = {r for r, _, _ in tx["w"]}
    assert 0x59 not in touched and 0x5F not in touched
    assert (0x07, clk_word) in [(r, v) for r, v, _ in tx["w"]]
    assert (0x08, 0x0FFF) in [(r, v) for r, v, _ in tx["w"]]
    assert (0x08, 0x87FF) in [(r, v) for r, v, _ in tx["w"]]

    # Verify REG_51[9] bandwidth-disable bit matches build variant on all nonzero writes
    reg51_events = [(float(t), int(v)) for r, v, t in tx["w"] if r == 0x51]
    nonzero_r51 = [v for _, v in reg51_events if v != 0]
    assert len(nonzero_r51) > 100
    assert all(((v >> 9) & 1) == bw_dis for v in nonzero_r51)
    assert all((v & 0x1000) == 0 for v in nonzero_r51), "must remain in DCS mode (bit12=0)"

    # Render emitted REG_51 stream into FM discriminator and score independently
    fm, fs = tx27.render_reg51_trace_to_fm(
        reg51_events, lpf_cutoff_hz=350.0, ignore_gain_in_dcs=False, noise_hz=15.0
    )
    res = tx27.analyze_tx27_fm(fm, fs, sym_us=5000.0)
    assert res["pass"] is True, res
    assert res["staircase"]["monotonic"] is True
    assert res["payload_4fsk"]["crc_ok"] is True
    assert res["payload_4fsk"]["payload_exact"] is True
    assert res["payload_4fsk"]["decoded_ascii"] == "FLUX TX27 DAC!"
    assert res["payload_4fsk"]["heldout_symbol_errors"] == 0
    assert res["edge_2p5ms"]["retention_vs_static"] > 0.75

    # Negative control: if REG_51[6:0] gain were ignored in DCS mode (binary only),
    # analyzer MUST fail the staircase and 4-FSK separation gates
    fm_bin, _ = tx27.render_reg51_trace_to_fm(
        reg51_events, lpf_cutoff_hz=350.0, ignore_gain_in_dcs=True, noise_hz=15.0
    )
    res_bin = tx27.analyze_tx27_fm(fm_bin, fs, sym_us=5000.0)
    assert res_bin["pass"] is False
    assert res_bin["staircase"]["monotonic"] is False


@need_clang
def test_key_abort_restores_all_registers(tmp_path):
    src = wrap(tmp_path, rf=True)
    before = baseline_regs()
    mmio, rcc, _ = k6_clock(read_ns=250)
    tx = {}
    ret, _, after, _ = run(
        before,
        src=str(src),
        level=3,
        rx_hz=HZ,
        tx=tx,
        mmio=mmio,
        write_us=117,
        keys=((300_000, 900_000),),
        count=200_000_000,
    )
    assert ret == -4
    assert tx["log"][-1][0] == "tx_off"
    for r in (0x51, 0x52, 0x70, 0x71):
        assert after[r] == before[r]
    assert (rcc[0x18] & (1 << 18)) == 0
