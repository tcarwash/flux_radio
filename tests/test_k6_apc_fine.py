"""Opt-in APC fine threshold lab: no hardware invoked by these tests."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod

PATCH=ROOT/"firmware/k6-aura/apply-apc-tx-api.py"
BASE=ROOT/"firmware/k6-aura/build/aura-fd6818b-port/src/app/pager.rs"
MODULE=ROOT/"c/k6/mod/apc_fine_threshold.c"
SEQUENCE=(100,85,80,78,76,74,72,70,72,74,76,80,100)


def test_fine_whitelist_patch_is_explicitly_opted_in(tmp_path):
    src=tmp_path/"src/app"
    src.mkdir(parents=True)
    target=src/"pager.rs"
    target.write_text(BASE.read_text())
    env={**os.environ,"K6_APC_TX_SOURCE_ROOT":str(src.parent),"K6_APC_FINE":"1"}
    p=subprocess.run([sys.executable,str(PATCH)],env=env,capture_output=True,text=True)
    assert p.returncode==0,p.stderr+p.stdout
    result=target.read_text()
    assert "matches!(percent, 70 | 72 | 74 | 76 | 78 | 80 | 85 | 90 | 100)" in result
    assert "if !crate::hal::txdog::ready()" in result
    assert "app.radio.fd6818.reg_write(syst, 0x36, target)" in result
    assert "if !active || hz != 433_500_000 || tone" in result
    assert "MOD_APC_ORIGINAL = 0" in result
    again=subprocess.run([sys.executable,str(PATCH)],env=env,capture_output=True,text=True)
    assert again.returncode==0,again.stderr+again.stdout
    assert result==target.read_text()


def test_default_whitelist_not_widened():
    s=PATCH.read_text()
    assert 'if os.environ.get("K6_APC_FINE", "0") == "1":' in s
    assert "percent != 70 && percent != 80 && percent != 90 && percent != 100" in s
    b=(ROOT/"firmware/k6-aura/build-fd6818b.sh").read_text()
    assert 'K6_APC_FINE:-0' in b and "Fine APC profile requires explicitly enabled APC TX" in b


def test_inert_module_is_api27_and_small():
    code,entry=k6mod.build(str(MODULE))
    assert entry==0 and len(code)<1024
    assert k6mod.source_level(str(MODULE))==27


def test_enabled_module_compiles(tmp_path):
    src=tmp_path/"fine-enabled.c"
    src.write_text(
        "#define K6_MODULE_LEVEL 27\n"
        "#define APC_FINE_ENABLE 1\n"
        "#define APC_FINE_FIXTURE_ACK 1\n"
        "#define APC_FINE_WATCHDOG_ACK 1\n"
        f'#include "{MODULE}"\n')
    code,entry=k6mod.build(str(src))
    assert entry==0 and 0<len(code)<4096
    assert k6mod.source_level(str(src))==27


@pytest.mark.parametrize("load,watchdog",[(0,0),(1,0),(0,1)])
def test_missing_ack_refuses_to_compile(tmp_path,load,watchdog):
    p=tmp_path/"unarmed.c"
    p.write_text("#define APC_FINE_ENABLE 1\n"
                 f"#define APC_FINE_FIXTURE_ACK {load}\n"
                 f"#define APC_FINE_WATCHDOG_ACK {watchdog}\n"
                 f'#include "{MODULE}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(p))


def test_exact_bounds_and_calibrated_target():
    s=MODULE.read_text()
    assert "#define APC_FINE_COUNT 13u" in s
    assert "#define APC_FINE_MS 75u" in s
    assert "#define APC_FINE_TX_LIMIT_MS 1800u" in s
    assert "{100,85,80,78,76,74,72,70,72,74,76,80,100}" in s
    assert "a->apc_set(steps[i])" in s
    assert "a->tx_off()" in s
    assert "a->fd_write" not in s
    assert "km_power_switch_off()" in s
    assert "a->key_down()" in s
    assert len(SEQUENCE)*75<=1100<1800
    assert all((180*x+50)//100<=180 for x in SEQUENCE)
    assert (180*70+50)//100==126
    assert (180*78+50)//100==140
