"""Bench-only TX AF-filter bypass on an emulated Cortex-M0; never keys hardware."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from modem import k6mod
from test_k6_i2s import need_clang, run

SRC = ROOT / "c/k6/mod/txbp4.c"
REGS = {0: 0x4829, 0x2b: 0x0300, 0x7e: 0x303e, 0x67: 0}


@pytest.fixture(scope="module")
def opted_in(tmp_path_factory):
    wrapper = tmp_path_factory.mktemp("txbp4") / "enable.c"
    wrapper.write_text(
        "#define K6_MODULE_LEVEL 3\n"
        "#define TXBP_ENABLE 1\n"
        "#define TXBP_FIXTURE_ACK 1\n"
        f'#include "{SRC}"\n'
    )
    return wrapper


@need_clang
def test_default_module_is_inert_and_has_no_rf_access():
    code, entry = k6mod.build(str(SRC))
    assert len(code) < 4096 and entry == 0
    tx = {}
    ret, lines, after, writes = run(REGS, src=str(SRC), level=3, tx=tx)
    assert ret == -9
    assert lines == ["txbp4 inert: fixture-gated"]
    assert after == REGS and not writes
    assert not tx.get("log")


@need_clang
def test_opted_in_variant_needs_fixture_and_is_small(opted_in):
    code, entry = k6mod.build(str(opted_in))
    assert len(code) < 4096 and entry == 0


@need_clang
def test_opted_in_module_preserves_rx_and_dcc_fields(opted_in):
    tx = {}
    ret, lines, after, writes = run(REGS, src=str(opted_in),
                                    level=3, rx_hz=146560000, tx=tx)
    assert ret == 0
    assert lines == ["txbp4 bypass set no PCM yet", "txbp4 done restored"]
    assert after == REGS
    assert writes == [(0x2b, 0x0307), (0x2b, 0x0300)]
    assert [t[0] for t in tx["log"]][0] == "tx_on"
    assert [t[0] for t in tx["log"]][-1] == "tx_off"
    assert "tx_pa" in [t[0] for t in tx["log"]]


@need_clang
def test_wrong_chip_or_channel_never_keys(opted_in):
    for regs, hz, error in (
        ({**REGS, 0:0x4819}, 146560000, -6),
        (REGS, 146570000, -2),
        ({**REGS, 0x67: 500}, 146560000, -7),
    ):
        tx = {}
        ret, lines, after, writes = run(regs, src=str(opted_in),
                                        level=3, rx_hz=hz, tx=tx)
        assert ret == error
        assert after == regs and not writes
        assert not tx.get("log")


@need_clang
def test_key_abort_unkeys_and_restores_filters(opted_in):
    tx = {}
    ret, lines, after, writes = run(REGS, src=str(opted_in), level=3,
                                    rx_hz=146560000, tx=tx,
                                    keys=((90000,200000),))
    assert ret == -4
    assert after == REGS
    assert writes[-1] == (0x2b,0x0300)
    assert tx["log"][-1][0] == "tx_off"


@need_clang
def test_core_tx_refusal_does_not_modify_filters(opted_in):
    tx = {"on_ret": -1}
    ret, lines, after, writes = run(REGS, src=str(opted_in), level=3,
                                    rx_hz=146560000, tx=tx)
    assert ret == -5
    assert after == REGS and not writes
    assert [x[0] for x in tx["log"]] == ["tx_on"]
