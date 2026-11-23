"""FD6818B calibrated APC TX callback patch: ABI, guards, and idempotence."""
from pathlib import Path
import os
import re
import subprocess
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
PATCHER=ROOT/"firmware/k6-aura/apply-apc-tx-api.py"
BASE=ROOT/"firmware/k6-aura/build/aura-fd6818b-port/src/app/pager.rs"
CAPI=ROOT/"c/k6/k6_mod.h"
BUILD=ROOT/"firmware/k6-aura/build-fd6818b.sh"
K6_BUILD=ROOT/"tools/k6_build.sh"
CAPS=ROOT/"firmware/k6-aura/apply-capability-query.py"


def patch_source(tmp_path, *,mutate=None):
    source=BASE.read_text()
    # The existing generated source includes MICIN API 26 and the
    # known anchors; copy it, never modify the current build's file.
    if mutate:
        source=mutate(source)
    root=tmp_path/"src"
    (root/"app").mkdir(parents=True)
    target=root/"app/pager.rs"
    target.write_text(source)
    env={**os.environ,"K6_APC_TX_SOURCE_ROOT":str(root)}
    r=subprocess.run([sys.executable,str(PATCHER)],env=env,
                     capture_output=True,text=True,timeout=10)
    return r,target,env


def test_appends_level_27_and_firmware_owned_only(tmp_path):
    res,p,env=patch_source(tmp_path)
    assert res.returncode==0,res.stderr+res.stdout
    s=p.read_text()
    assert s.count('pub apc_set: unsafe extern "C" fn(u32) -> i32')==1
    assert s.index("pub apc_set:")>s.index("pub micin_tx:")
    assert s.count("apc_set: mod_apc_set,")==1
    assert "feature = \"spilib\")) { 27 }" in s
    assert "crate::hal::txdog::ready()" in s
    assert "percent != 70 && percent != 80 && percent != 90 && percent != 100" in s
    assert "(pa & 0x00FF) == 0x00A2" in s
    assert "MOD_APC_ORIGINAL = 0" in s
    assert "if !active || hz != 433_500_000 || tone" in s
    assert "mod_tx_off()" in s
    assert "app.radio.fd6818.reg_write(syst, 0x36, target)" in s
    assert "app.radio.fd6818.reg_read(syst, 0x36) != target" in s
    assert "k6_apc_tx" in s
    assert "MOD_APC_ORIGINAL" in s
    again=subprocess.run([sys.executable,str(PATCHER)],env=env,
                         capture_output=True,text=True,timeout=10)
    assert again.returncode==0,again.stderr
    assert s==p.read_text()


def test_patcher_refuses_unknown_abi_without_partial_write(tmp_path):
    res,p,env=patch_source(tmp_path,mutate=lambda x:x.replace(
        'pub micin_tx: unsafe extern "C" fn(i32) -> i32,',
        'pub some_unknown_api: unsafe extern "C" fn(i32) -> i32,'))
    assert res.returncode!=0
    assert "APC patch anchor count" in res.stderr+res.stdout


def test_C_api_tail_matches_and_does_not_offer_raw_pa_write():
    s=CAPI.read_text()
    assert s.index("(*apc_set)(uint32_t percent);")>s.index("(*micin_tx)(int enable);")
    assert re.search(r"#define KM_LEVEL 26",s)
    assert "calibrated" in s.lower()
    assert "REG_36" not in s[s.index("(*apc_set)"):s.index("} k6_api_t;")]


def test_optin_build_guard_and_capability_consistency():
    b=BUILD.read_text()
    assert "K6_APC_TX:-0" in b
    assert "apply-apc-tx-api.py" in b
    assert b.index("apply-micin-tx-api.py")<b.index("apply-apc-tx-api.py")
    assert "K6_LEAN_TXDOG" in b and "K6_OTA_SERVICE" in b
    assert "--cfg k6_apc_tx" in b
    k=K6_BUILD.read_text()
    assert 'CFLAGS="$CFLAGS -DKM_LEVEL=27"' in k
    assert "K6_APC_TX" in k
    cap=CAPS.read_text()
    assert '27 if os.environ.get("K6_APC_TX") == "1"' in cap
    assert '26 if os.environ.get("K6_MICIN_TX") == "1"' in cap


@pytest.mark.parametrize("source,needle",[
    (PATCHER,"percent != 70"),
    (PATCHER,"percent != 80"),
    (PATCHER,"percent != 90"),
    (PATCHER,"percent != 100"),
    (PATCHER,"0x00A2"),
    (PATCHER,"0x4829"),
])
def test_safety_contract(source,needle):
    assert needle in source.read_text()


def test_no_unrestricted_persistent_calibration_writes():
    s=PATCHER.read_text()
    for token in ("cal_write(","fl_prog(","fl_erase(","write_flash(",
                  "app.storage.write","REG_19"):
        assert token not in s
