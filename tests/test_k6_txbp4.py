"""Legacy TX-only filter probe cannot exercise MICIN; no enabled build allowed."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from modem import k6mod
from test_k6_i2s import need_clang, run

SRC = ROOT / "c/k6/mod/txbp4.c"
REGS = {0:0x4829, 0x2B:0x0300, 0x7E:0x303E, 0x67:0}


@need_clang
def test_legacy_module_remains_inert():
    code,entry=k6mod.build(str(SRC))
    assert entry==0 and len(code)<4096
    tx={}
    ret,lines,after,writes=run(REGS, src=str(SRC),level=3,tx=tx)
    assert ret==-9 and lines==["txbp4 inert: fixture-gated"]
    assert after==REGS and not writes and not tx.get("log")


@need_clang
@pytest.mark.parametrize("fixture",[0,1])
def test_legacy_enabled_build_is_rejected_even_with_fixture_ack(tmp_path,fixture):
    wrapper=tmp_path/f"legacy_{fixture}.c"
    wrapper.write_text("#define TXBP_ENABLE 1\n"
                       f"#define TXBP_FIXTURE_ACK {fixture}\n"
                       f'#include "{SRC}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(wrapper))


def test_diagnostic_refers_to_new_micin_callback_instead():
    s=SRC.read_text()
    assert 'Legacy TXBP has MICIN ADC disabled' in s
    assert 'use txmic4' in s
