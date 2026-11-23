"""TX-26 FD6818B 4800-baud 4-FSK (REG_5F sign + REG_70 magnitude): OFFLINE tests.

Runs c/k6/mod/tx26_c4fsk_probe.c on the emulated Cortex-M0 (Unicorn) against a
stateful 4800-baud 0x4829 FSK+REG_70 model, then feeds the emitted register
writes into the independent synthetic FM-discriminator analyzer.
Passing these tests proves offline firmware safety, timing, and decoder logic
ONLY — not physical FD6818B RF behavior.
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
import k6_tx26_c4fsk_analyze as tx26  # noqa: E402

SRC = ROOT / "c/k6/mod/tx26_c4fsk_probe.c"
HZ = 433_500_000
SYM_US = 1e6 / tx26.BAUD


def wrap(
    tmp_path: Path,
    *,
    bench: bool = True,
    rf: bool = False,
    fixture: bool = True,
    watchdog: bool = True,
    hz: int = HZ,
    lag_q: int = 7,
) -> Path:
    p = tmp_path / f"tx26_b{int(bench)}_rf{int(rf)}_l{lag_q}.c"
    p.write_text(
        f"#define TX26_BENCH_ENABLE {int(bench)}\n"
        f"#define TX26_RF_ENABLE {int(rf)}\n"
        f"#define TX26_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX26_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX26_HZ {hz}u\n"
        f"#define TX26_LAG_Q {lag_q}\n"
        f'#include "{SRC}"\n',
        encoding="utf-8",
    )
    return p


class BChipFskModel:
    """Stateful FD6818B FSK + REG_70 model for Cortex-M0 Unicorn tests."""

    def __init__(self):
        self.fifo = []
        self.pkts = []
        self.reg70 = []
        self.ctrl = 0
        self.len5d = 0x3B00
        self.done_us = None

    def key(self, name, us):
        return 0

    def read(self, reg, us):
        if reg == 0x0C:
            return 1 if (self.done_us is not None and us >= self.done_us) else 0
        return None

    def write(self, reg, v, us):
        if reg == 0x5D:
            self.len5d = v
        elif reg == 0x70:
            self.reg70.append((float(us), int(v)))
        elif reg == 0x59:
            if v & 0xC000:
                self.fifo = []
            if (v & 0x0800) and not (self.ctrl & 0x0800):
                nbytes = ((self.len5d >> 8) & 0xFF) + 1
                lead = 8 * (((v >> 4) & 0xF) + 1) + (32 if (v & 8) else 16)
                self.pkts.append({"t_en_us": float(us), "words": list(self.fifo), "lead": lead})
                self.done_us = us + (lead + nbytes * 8) * SYM_US + 300.0
            self.ctrl = v
        elif reg == 0x5F:
            self.fifo.append(int(v))
        elif reg == 0x02:
            self.done_us = None


def baseline_regs() -> dict:
    return {
        **START,
        0x00: 0x4829,
        0x30: 0xBFF1,
        0x3F: 0x0000,
        0x58: 0x0000,
        0x59: 0x0028,
        0x5A: 0x1111,
        0x5B: 0x2222,
        0x5C: 0x5665,
        0x5D: 0x0F00,
        0x67: rssi_reg(-120),
        0x70: 0x0000,
        0x72: 0x3065,
    }


@need_clang
def test_default_build_is_tiny_and_inert():
    code, entry = k6mod.build(str(SRC))
    assert entry == 0 and 0 < len(code) < 256
    tx = {}
    ret, said, _, _ = run(baseline_regs(), level=3, src=str(SRC), rx_hz=HZ, tx=tx)
    assert ret == -9
    assert said[-1].startswith("tx26 inert")
    assert tx.get("w", []) == [] and tx.get("log", []) == []


@need_clang
def test_rf_build_refuses_missing_safety_acks(tmp_path):
    bad = wrap(tmp_path, bench=True, rf=True, fixture=False, watchdog=True)
    with pytest.raises(Exception):
        k6mod.build(str(bad))


@need_clang
@pytest.mark.parametrize(
    "mut,rx_hz,expected_ret",
    [
        ({0x00: 0x4819}, HZ, -6),
        ({}, 144_390_000, -2),
        ({0x67: rssi_reg(-70)}, HZ, -7),
        ({0x3F: 0x2000}, HZ, -8),
    ],
)
def test_runtime_preflight_refusals(tmp_path, mut, rx_hz, expected_ret):
    src = wrap(tmp_path, rf=True)
    regs = {**baseline_regs(), **mut}
    tx = {}
    ret, _, _, _ = run(regs, level=3, src=str(src), rx_hz=rx_hz, tx=tx)
    assert ret == expected_ret
    assert tx.get("w", []) == [] and tx.get("log", []) == []


@need_clang
@pytest.mark.parametrize("rf", [False, True])
def test_tx26_end_to_end_unicorn_and_independent_4fsk_decode(tmp_path, rf):
    src = wrap(tmp_path, rf=rf, lag_q=7)
    code, entry = k6mod.build(str(src))
    assert entry == 0 and len(code) < 4096

    before = baseline_regs()
    fsk = BChipFskModel()
    mmio, rcc, tim = k6_clock(read_ns=250)
    tx = {}
    ret, said, after, _ = run(
        before,
        src=str(src),
        level=3,
        rx_hz=HZ,
        tx=tx,
        fsk=fsk,
        mmio=mmio,
        write_us=117,
        count=200_000_000,
    )
    assert ret == 4, (ret, said)
    assert f"tx26 ok pkts=4" in said[-1] and "late=0" in said[-1] and f"pa={int(rf)}" in said[-1]
    assert tx["log"][0][:2] == ("tx_on", 1800)
    assert tx["log"][-1][0] == "tx_off"
    assert [x[1] for x in tx["log"] if x[0] == "tx_pa"] == ([0, 1, 0] if rf else [0, 0])
    assert len(fsk.pkts) == 4
    assert all(len(p["words"]) == 30 for p in fsk.pkts)
    assert max(t for _, _, t in tx["w"]) < 1_800_000

    # Exact snapshot/restore of all touched RFIC registers and TIM17 clock gate
    for r in (0x3F, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x70, 0x72):
        assert after[r] == before[r], f"reg 0x{r:02x} not restored"
    assert (rcc[0x18] & (1 << 18)) == 0

    # Verify REG_5D is written AFTER REG_59=0x00F8 before every packet's REG_5F writes
    writes = [(r, v) for r, v, _ in tx["w"]]
    for idx, (r, v) in enumerate(writes):
        if r == 0x5F and (idx == 0 or writes[idx - 1][0] != 0x5F):
            assert writes[idx - 1] == (0x5D, 0x3B00), f"preceding write before FIFO: {writes[idx-1]}"

    # Feed real Unicorn-emitted trace into independent synthetic FM discriminator + 4-FSK decoder
    fm, fs = tx26.render_fsk_trace_to_fm(
        fsk.pkts, fsk.reg70, chip_lag_syms=1.75, latched=False, noise_hz=45.0
    )
    res = tx26.analyze_tx26_fm(fm, fs)
    assert res["pass"] is True, res
    assert res["p0"]["sign_errors"] == 0
    assert res["p1"]["monotonic"] is True
    assert res["p2"]["droop_ratio_zero"] > 0.95 and res["p2"]["droop_ratio_one"] > 0.95
    assert res["p3"]["crc_ok"] is True
    assert res["p3"]["payload_exact"] is True
    assert res["p3"]["heldout_symbol_errors"] == 0

    # Negative control: if REG_70 were latched per-packet on 0x4829, analyzer MUST reject
    fm_latched, _ = tx26.render_fsk_trace_to_fm(
        fsk.pkts, fsk.reg70, chip_lag_syms=1.75, latched=True, noise_hz=45.0
    )
    res_latched = tx26.analyze_tx26_fm(fm_latched, fs)
    assert res_latched["pass"] is False
    assert res_latched["latched"] is True


@need_clang
def test_key_abort_restores_all_registers(tmp_path):
    src = wrap(tmp_path, rf=True)
    before = baseline_regs()
    fsk = BChipFskModel()
    mmio, rcc, _ = k6_clock(read_ns=250)
    tx = {}
    ret, _, after, _ = run(
        before,
        src=str(src),
        level=3,
        rx_hz=HZ,
        tx=tx,
        fsk=fsk,
        mmio=mmio,
        write_us=117,
        keys=((250_000, 900_000),),
        count=200_000_000,
    )
    assert ret == -4
    assert tx["log"][-1][0] == "tx_off"
    for r in (0x3F, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x70, 0x72):
        assert after[r] == before[r]
    assert (rcc[0x18] & (1 << 18)) == 0
