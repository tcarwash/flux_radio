#!/usr/bin/env python3
"""Opt-in FD6818B APC envelope research callback, appended module API 27.

Only when K6_APC_TX=1 together with K6_MICIN_TX=1 and the independent
TXDOG. APC target percentages 70/80/90/100 of the existing calibrated
LOW-power TX target. Never increases power or changes the PA driver
configuration, and never persists any calibration data.

This is a firmware-owned lab interface, not a raw REG_36 write.
"""
from pathlib import Path
import os

root=Path(os.environ.get("K6_APC_TX_SOURCE_ROOT",
    str(Path(__file__).parent/"build/aura-fd6818b-port/src")))
pager=root/"app/pager.rs"
s=pager.read_text()


def replace_once(old,new):
    global s
    if new in s:
        return
    if s.count(old)!=1:
        raise SystemExit(f"APC patch anchor count {s.count(old)}: {old[:65]!r}")
    s=s.replace(old,new,1)


anchor='        pub micin_tx: unsafe extern "C" fn(i32) -> i32,'
replace_once(anchor,anchor+
    '\n        // Level 27: calibrated 70/80/90/100% APC envelope targets, FD6818B only'
    '\n        pub apc_set: unsafe extern "C" fn(u32) -> i32,')
anchor='    micin_tx: mod_micin_tx,'
replace_once(anchor,anchor+'\n    apc_set: mod_apc_set,')

anchor=(
    'level: if cfg!(all(k6_micin_tx, k6_live_serial, k6_lean_txdog, '
    'feature = "fd6818b", feature = "spilib")) { 26 }'
)
replace_once(anchor,
    'level: if cfg!(all(k6_apc_tx, k6_micin_tx, k6_live_serial, '
    'k6_lean_txdog, feature = "fd6818b", feature = "spilib")) { 27 } '
    'else if cfg!(all(k6_micin_tx, k6_live_serial, k6_lean_txdog, '
    'feature = "fd6818b", feature = "spilib")) { 26 }'
)

# Baseline APC capture for a SINGLE active tx_on lease. This comes from
# the existing calibrated Low-power PA state, not a hardcoded level.
anchor='const MOD_TX_STATE: u16 = 0xC1FA;'
replace_once(anchor,anchor+'''
#[cfg(k6_apc_tx)]
static mut MOD_APC_ORIGINAL: u16 = 0;''')

anchor='    app.radio.fd6818.reg_write(syst, 0x30, MOD_TX_STATE);'
replace_once(anchor,anchor+'''
    // Capture the exact calibrated Low-power UHF APC target once.
    // Do not infer it from prior flash, another band or a fixed number.
    #[cfg(k6_apc_tx)]
    unsafe {
        let pa = app.radio.fd6818.reg_read(syst, 0x36);
        MOD_APC_ORIGINAL = if (pa & 0x00FF) == 0x00A2 && (pa >> 8) >= 32 {
            pa
        } else { 0 };
    }''')

anchor='    let (app, syst) = unsafe { mod_ctx() };\n    app.radio.enter_rx(syst); // not end_tx: no roger tone'
replace_once(anchor,'''
    #[cfg(k6_apc_tx)]
    unsafe { MOD_APC_ORIGINAL = 0; }
    let (app, syst) = unsafe { mod_ctx() };
    app.radio.enter_rx(syst); // not end_tx: no roger tone''')

anchor='/// Enter Aura\'s sideloaded-module picker from the normal launcher.'
callback=r'''/// Level-27 APC lab: change ONLY the calibrated low-power UHF
/// APC target in REG_36[15:8], retaining REG_36[7:0] exactly 0xA2.
/// Valid settings are 70, 80, 90, 100 percent of the captured target.
/// No power increase over the calibrated baseline; no persistent writes.
/// The PA must already be on in a firmware-leased, watchdog-armed TX.
/// The normal module API continues to refuse arbitrary REG_36 writes.
#[cfg(all(k6_apc_tx, k6_micin_tx, k6_live_serial, k6_lean_txdog,
          feature="fd6818b"))]
unsafe extern "C" fn mod_apc_set(percent: u32) -> i32 {
    mod_tx_guard();
    if percent != 70 && percent != 80 && percent != 90 && percent != 100 {
        return -1;
    }
    if !crate::hal::txdog::ready() { return -1; }
    let (active, hz, tone) = unsafe {
        let c = &*addr_of!(MOD_CTX);
        (!c.app.is_null() && c.tx, c.rx_hz, c.tone_active)
    };
    if !active || hz != 433_500_000 || tone { return -1; }
    let orig = unsafe { MOD_APC_ORIGINAL };
    if (orig & 0x00FF) != 0x00A2 || (orig >> 8) < 32 { return -1; }
    let (app, syst) = unsafe { mod_ctx() };
    if app.radio.fd6818.reg_read(syst, 0) != 0x4829 { return -1; }
    if app.radio.fd6818.reg_read(syst, 0x30) != MOD_TX_STATE { return -1; }
    let before = app.radio.fd6818.reg_read(syst, 0x36);
    if (before & 0x00FF) != 0x00A2 ||
       (before >> 8) > (orig >> 8) || (before >> 8) == 0 {
        return -1;
    }
    let target_byte = (((orig >> 8) as u32 * percent + 50) / 100) as u16;
    let target = (target_byte << 8) | (orig & 0x00FF);
    app.radio.fd6818.reg_write(syst, 0x36, target);
    if app.radio.fd6818.reg_read(syst, 0x36) != target {
        // Unexpected state: immediately release TX, rather than
        // allowing a module to continue into an unverified PA state.
        unsafe { mod_tx_off() };
        return -2;
    }
    0
}
#[cfg(not(all(k6_apc_tx, k6_micin_tx, k6_live_serial, k6_lean_txdog,
              feature="fd6818b")))]
unsafe extern "C" fn mod_apc_set(_percent: u32) -> i32 { -1 }

'''
# This additional, narrower research profile is opt-in and does not widen
# the previously validated four-value API27 contract by default.
if os.environ.get("K6_APC_FINE", "0") == "1":
    callback=callback.replace(
        'if percent != 70 && percent != 80 && percent != 90 && percent != 100 {',
        'if !matches!(percent, 70 | 72 | 74 | 76 | 78 | 80 | 85 | 90 | 100) {')
    if 'matches!(percent, 70 | 72 | 74 | 76 | 78 | 80 | 85 | 90 | 100)' not in callback:
        raise SystemExit('fine profile refusal whitelist patch missing')
replace_once(anchor,callback+anchor)
pager.write_text(s)
print("Applied opt-in level-27 calibrated APC callback")
