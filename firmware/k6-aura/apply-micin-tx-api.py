#!/usr/bin/env python3
"""Opt-in experimental FD6818B MICIN TX callback, API level 26.

Apply only AFTER apply-live-serial.py. Without K6_MICIN_TX=1 the build
script does not call this patch; existing CW/FSK/tone behavior is untouched.
This creates a managed one-bit MIC ADC switch, not raw RF register access.
"""
from pathlib import Path
import os

root = Path(os.environ.get("K6_MICIN_TX_SOURCE_ROOT",
         str(Path(__file__).parent / "build/aura-fd6818b-port/src")))
pager_path = root / "app/pager.rs"
s = pager_path.read_text()

def replace_once(old: str, new: str) -> None:
    global s
    if new in s:
        return
    if s.count(old) != 1:
        raise SystemExit(f"MICIN patch anchor mismatch: {old[:75]!r} count={s.count(old)}")
    s = s.replace(old, new, 1)

api_anchor = '        pub cancel_poll: unsafe extern "C" fn() -> i32,'
replace_once(api_anchor, api_anchor +
    '\n        // level 26: opt-in, bounded MICIN ADC TX state, FD6818B only'
    '\n        pub micin_tx: unsafe extern "C" fn(i32) -> i32,')

init_anchor = '    cancel_poll: mod_cancel_poll,'
replace_once(init_anchor, init_anchor + '\n    micin_tx: mod_micin_tx,')

level_anchor = (
    'level: if cfg!(all(k6_live_serial, feature = "spilib")) { 25 } '
    'else if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} '
    'else if cfg!(feature = "fd6818b") { 23 } else { 9 },'
)
new_level = (
    'level: if cfg!(all(k6_micin_tx, k6_live_serial, k6_lean_txdog, '
    'feature = "fd6818b", feature = "spilib")) { 26 } '
    + 'else ' + level_anchor[len("level: "):]
)
replace_once(level_anchor, new_level)

func_anchor = "/// Enter Aura's sideloaded-module picker from the normal launcher."
func = '''/// Level 26 lab callback: route the MIC ADC only after a guarded
/// tx_on and *verified PA-off*. The normal module TX state (0xC1FA)
/// deliberately disconnects MICIN; the analog path needs bit2 enabled.
/// Never restore the RF state by writing any other value or touching
/// arbitrary RF power controls. mod_tx_off/normal RX cleanup remains
/// responsible for all state transitions.
#[cfg(all(k6_micin_tx, k6_live_serial, k6_lean_txdog, feature="fd6818b"))]
unsafe extern "C" fn mod_micin_tx(enable: i32) -> i32 {
    if enable != 0 && enable != 1 { return -1; }
    mod_tx_guard();
    if !crate::hal::txdog::ready() { return -1; }
    let tx = unsafe {
        let c = &*addr_of!(MOD_CTX);
        !c.app.is_null() && c.tx && !c.tone_active
    };
    if !tx { return -1; }
    let (app, syst) = unsafe { mod_ctx() };
    if app.radio.fd6818.reg_read(syst, 0) != 0x4829 { return -1; }
    let before = app.radio.fd6818.reg_read(syst, 0x30);
    if before != 0xC1FA && before != 0xC1FE { return -1; }
    if enable == 1 {
        // The module must call tx_pa(0) before connecting microphone audio;
        // no new RF state is created by this API while the PA is enabled.
        let pa = app.radio.fd6818.reg_read(syst, 0x36);
        if pa & 0x0080 != 0 { return -1; }
    }
    let target = if enable != 0 { 0xC1FE } else { 0xC1FA };
    app.radio.fd6818.reg_write(syst, 0x30, target);
    if app.radio.fd6818.reg_read(syst, 0x30) != target { return -2; }
    0
}
#[cfg(not(all(k6_micin_tx, k6_live_serial, k6_lean_txdog, feature="fd6818b")))]
unsafe extern "C" fn mod_micin_tx(_enable: i32) -> i32 { -1 }

'''
replace_once(func_anchor, func + func_anchor)
pager_path.write_text(s)
