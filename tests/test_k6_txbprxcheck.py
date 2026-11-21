"""RX-only physical-silicon filter-bypass register roundtrip tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from modem import k6mod
from test_k6_i2s import need_clang, run

SOURCE = ROOT / "c/k6/mod/txbprxcheck.c"
REGS = {0:0x4829, 0x30:0xBFF1, 0x2B:0x0300,
        0x7E:0x303E, 0x40:0x3516, 0x43:0x3028}


@need_clang
def test_module_compiles_for_level2_no_tx():
    code, entry = k6mod.build(str(SOURCE))
    assert entry == 0 and len(code) < 4096
    assert k6mod.source_level(str(SOURCE)) == 2
    src = SOURCE.read_text()
    assert "tx_on(" not in src and "tx_pa(" not in src
    assert "tx_off(" not in src and "fl_prog(" not in src


@need_clang
def test_exact_rx_baseline_write_and_restore():
    ret, lines, regs, writes = run(REGS, src=str(SOURCE), level=2,
                                  rx_hz=146560000, count=2_000_000)
    assert ret == 1
    assert lines == ["txbprx begin RX; only r2b",
                     "txbprx r2b 0300>0307>0300 OK",
                     "txbprx r30 unchanged r7e unchanged"]
    assert regs == REGS
    assert writes == [(0x2B, 0x0307), (0x2B, 0x0300)]


@need_clang
def test_nonmatching_chip_and_freq_refuse_without_writes():
    for patch, hz, expected in (
        ({0:0x4819},146560000,-6),
        ({},146570000,-2),
        ({0x30:0xC1FE},146560000,-7),
        ({0x2B:0x0301},146560000,-8),
    ):
        start = {**REGS, **patch}
        ret, lines, regs, writes = run(start, src=str(SOURCE),
                                      level=2, rx_hz=hz,
                                      count=2_000_000)
        assert ret == expected
        assert regs == start and not writes


@need_clang
def test_key_down_refuses_before_any_rf_register_write():
    ret, lines, regs, writes = run(REGS, src=str(SOURCE), level=2,
                                  rx_hz=146560000, keys=((0, 50000),),
                                  count=2_000_000)
    assert ret == -2
    assert regs == REGS and not writes
