"""Opt-in MICIN ADC TX API source patch: ABI/gating regression tests."""
from pathlib import Path
import os
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT/"firmware/k6-aura/apply-micin-tx-api.py"
CAPI = ROOT/"c/k6/k6_mod.h"
BUILDER = ROOT/"firmware/k6-aura/build-fd6818b.sh"

LIVE_LEVEL = (
    'level: if cfg!(all(k6_live_serial, feature = "spilib")) { 25 } '
    'else if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} '
    'else if cfg!(feature = "fd6818b") { 23 } else { 9 },'
)
FIXTURE = '''
mod ffi {
    pub struct K6Api {
        pub fsk_configured: unsafe extern "C" fn(u32, *mut u16, u32, u32) -> i32,
        pub cancel_poll: unsafe extern "C" fn() -> i32,
    }
}
static MOD_API: ffi::K6Api = ffi::K6Api {
    ''' + LIVE_LEVEL + '''
    fsk_configured: mod_fsk_configured,
    cancel_poll: mod_cancel_poll,
};
/// Enter Aura's sideloaded-module picker from the normal launcher.
'''


def apply(tmp_path, text=FIXTURE):
    root=tmp_path/"src"
    (root/"app").mkdir(parents=True, exist_ok=True)
    p=root/"app/pager.rs"
    p.write_text(text)
    env={**os.environ, "K6_MICIN_TX_SOURCE_ROOT":str(root)}
    run=subprocess.run([sys.executable,str(PATCHER)],env=env,text=True,
                       capture_output=True,timeout=10)
    return run,p,env


def test_adds_append_only_level26_callback_without_touching_old_slots(tmp_path):
    run,p,env=apply(tmp_path)
    assert run.returncode==0,run.stderr
    s=p.read_text()
    assert s.count("pub micin_tx: unsafe extern") == 1
    assert s.index("pub micin_tx:")>s.index("pub cancel_poll:")
    assert s.index("micin_tx: mod_micin_tx")>s.index("cancel_poll: mod_cancel_poll")
    assert "k6_micin_tx" in s and "{ 26 } else if cfg!(all(k6_live_serial" in s
    assert "reg_read(syst, 0x36)" in s and "0x0080" in s
    assert "mod_tx_guard();" in s
    assert "crate::hal::txdog::ready()" in s
    assert "0xC1FA" in s and "0xC1FE" in s
    assert "mod_tx_off" in s
    assert s.count("mod_micin_tx(enable: i32)") == 1
    twice=subprocess.run([sys.executable,str(PATCHER)],env=env,text=True,
                         capture_output=True,timeout=10)
    assert twice.returncode==0,twice.stderr
    assert s==p.read_text()


def test_patcher_fails_closed_on_unknown_upstream_abi(tmp_path):
    run,p,env=apply(tmp_path,FIXTURE.replace(
        "pub cancel_poll: unsafe extern", "pub not_the_api: unsafe extern"))
    assert run.returncode!=0
    assert "anchor mismatch" in (run.stdout+run.stderr)


def test_api_level26_is_tail_only_and_not_generic_raw_register_api():
    s=CAPI.read_text()
    assert "(*cancel_poll)(void);" in s
    assert s.index("(*micin_tx)(int enable);")>s.index("(*cancel_poll)(void);")
    assert re.search(r"#define KM_LEVEL 26",s)
    assert "TX watchdog" in s or "TX watchdog".lower() in s.lower()


def test_build_is_optin_and_enforces_watchdog():
    s=BUILDER.read_text()
    assert s.count('if [ "${K6_MICIN_TX:-0}" = 1 ]; then') == 2
    assert "K6_LEAN_TXDOG" in s and "K6_LIVE_SERIAL" in s
    assert "apply-micin-tx-api.py" in s
    assert "--cfg k6_micin_tx" in s
    assert s.index('apply-live-serial.py')<s.index('apply-micin-tx-api.py')
