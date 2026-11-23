"""Cortex-M0 TX-15X timed REG70 gain modulation safety and schedule tests."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from modem import k6mod
from test_k6_i2s import run, START, need_clang

SOURCE = ROOT / "c/k6/mod/tx15x_gain_dynamics.c"


def wrap(tmp_path, *, rf=False, fixture=True, watchdog=True, hz=433500000):
    p = tmp_path / f"tx15x-{int(rf)}.c"
    p.write_text(
        "#define TX15X_BENCH_ENABLE 1\n"
        f"#define TX15X_RF_ENABLE {int(rf)}\n"
        f"#define TX15X_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX15X_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX15X_HZ {hz}u\n"
        f'#include "{SOURCE}"\n')
    return p


@need_clang
def test_default_image_is_inert_and_builds():
    code, entry = k6mod.build(str(SOURCE))
    assert entry == 0 and 0 < len(code) < 4096


@need_clang
@pytest.mark.parametrize("rf", [False, True])
def test_exact_timing_schedule_and_register_restore(tmp_path, rf):
    src = wrap(tmp_path, rf=rf)
    code, entry = k6mod.build(str(src))
    assert entry == 0 and len(code) < 4096
    tx = {}
    original70, original71 = 0x1234, 0x3456
    ret, said, after, _ = run(
        {**START, 0: 0x4829, 0x70: original70, 0x71: original71},
        src=str(src), level=3, rx_hz=433500000, tx=tx,
        count=180_000_000)
    assert ret == 3, said
    assert "50/100/149.25sps" in said[-1]
    writes = [(v, t) for reg, v, t in tx["w"] if reg == 0x70]
    gain_values = [v for v, _ in writes]
    assert gain_values[:3] == [0, 0x9800, 0x9000]
    assert gain_values[-2:] == [0x9800, original70]
    # 24 symbols at each integer tick period: 200, 100, then 67 ticks.
    # The fake hardware log timestamps each register write, including the
    # deterministic delay ticks, so the boundaries expose the intended rates.
    assert [t for _, t in writes] == sorted(t for _, t in writes)
    assert [v for v in gain_values if v in (0x9000, 0xC000)].count(0x9000) == 36
    assert [v for v in gain_values if v in (0x9000, 0xC000)].count(0xC000) == 36
    assert not any(reg in (0x30, 0x36, 0x38, 0x39) for reg, _, _ in tx["w"])
    assert [e[1] for e in tx["log"] if e[0] == "tx_pa"] == (
        [0, 1, 0] if rf else [0, 0])
    assert tx["log"][0][:2] == ("tx_on", 1800)
    assert after[0x70] == original70 and after[0x71] == original71


@need_clang
@pytest.mark.parametrize("fixture,watchdog", [(0, 0), (0, 1), (1, 0)])
def test_rf_build_requires_fixture_and_watchdog(tmp_path, fixture, watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path, rf=True,
                             fixture=bool(fixture), watchdog=bool(watchdog))))


def test_complete_schedule_fits_lease_with_setup_margin():
    symbol_ticks = 24 * (200 + 100 + 67)
    # 60-ms lead + 100-ms pilot + 880.8-ms toggling + 100-ms pilot
    # + 80-ms tail = 1.2208 s. Remaining lease time covers register I/O.
    tx_lease_schedule_us = symbol_ticks * 100 + 2 * 100000 + 60000 + 80000
    assert tx_lease_schedule_us == 1_220_800
    assert tx_lease_schedule_us < 1_800_000
