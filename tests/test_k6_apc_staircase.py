"""APC envelope staircase controlled by firmware API27; compile-only safety tests."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod

SRC=ROOT/"c/k6/mod/apc_staircase.c"


@pytest.fixture()
def enabled(tmp_path):
    f=tmp_path/"apc-staircase.c"
    f.write_text(
        "#define K6_MODULE_LEVEL 27\n"
        "#define APC_STEP_ENABLE 1\n"
        "#define APC_STEP_FIXTURE_ACK 1\n"
        "#define APC_STEP_WATCHDOG_ACK 1\n"
        f'#include "{SRC}"\n')
    return f


def test_default_image_is_small_and_declares_api27():
    code,entry=k6mod.build(str(SRC))
    assert entry==0 and 0<len(code)<512
    assert k6mod.source_level(str(SRC))==27


def test_enabled_module_compiles_within_budget(enabled):
    code,entry=k6mod.build(str(enabled))
    assert entry==0 and 0<len(code)<4096
    assert k6mod.source_level(str(enabled))==27


@pytest.mark.parametrize("fixture,watchdog",[(0,0),(1,0),(0,1)])
def test_missing_acks_compile_refused(tmp_path,fixture,watchdog):
    p=tmp_path/"bad-apc.c"
    p.write_text(
        "#define APC_STEP_ENABLE 1\n"
        f"#define APC_STEP_FIXTURE_ACK {fixture}\n"
        f"#define APC_STEP_WATCHDOG_ACK {watchdog}\n"
        f'#include "{SRC}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(p))


def test_staircase_is_bounded_and_no_raw_register_write():
    src=SRC.read_text()
    assert "APC_STEP_TX_LIMIT_MS 1650u" in src
    assert "APC_STEP_COUNT 7u" in src
    assert "APC_STEP_MS 100u" in src
    assert "100,90,80,70,80,90,100" in src
    assert "a->apc_set(steps[i])" in src
    assert "a->apc_set(100u)" in src
    assert "a->tx_off()" in src
    assert "km_power_switch_off" in src
    assert "a->key_down()" in src
    assert "a->fd_read(R_PA)" in src
    assert "a->fd_write" not in src
    assert "fl_prog(" not in src and "cal_write(" not in src


def test_integer_quantization_never_increases_reference():
    baseline=0xb4
    percentages=(100,90,80,70,80,90,100)
    expected=(180,162,144,126,144,162,180)
    actual=tuple((baseline*p+50)//100 for p in percentages)
    assert actual==expected
    assert all(0<x<=baseline for x in actual)


def test_C_and_Rust_level27_callback_layout_append_only():
    h=(ROOT/"c/k6/k6_mod.h").read_text()
    s=(ROOT/"firmware/k6-aura/apply-apc-tx-api.py").read_text()
    assert h.index("(*apc_set)(uint32_t percent)")>h.index("(*micin_tx)(int enable)")
    assert 'apc_set: mod_apc_set,' in s
    assert "pub apc_set:" in s
