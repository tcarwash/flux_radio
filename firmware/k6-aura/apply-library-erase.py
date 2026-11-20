#!/usr/bin/env python3
"""Add authenticated, guarded per-module slot removal to the Blowfang cable session."""
from pathlib import Path

root = Path(__file__).parent / "build/aura-fd6818b-port/src"
pager = root / "app/pager.rs"
text = pager.read_text()

# Keep the shipped image small. These high-volume bring-up traces have served their purpose;
# retain actionable errors and user-facing module output.
for line in (
    '    rl!("K6 LIB read begin loading={} addr={:#x} len={}", unsafe { LIB_LOADING }, addr, n);\n',
    '    rl!("K6 LIB read done loading={} addr={:#x} len={}", unsafe { LIB_LOADING }, addr, n);\n',
    '    rl!("K6 MOD stage gpio-read begin");\n',
    '    rl!("K6 MOD stage gpio-read done value={:#06x}", gpio);\n',
    '    rl!("K6 MOD stage dispatch begin");\n',
    '    rl!("K6 MOD stage dispatch returned ret={}", ret);\n',
    '    rl!("K6 MENU queued slot={}", slot);\n',
    '        rl!("K6 MENU dispatch queued slot={}", slot);\n',
):
    text = text.replace(line, "")

ffi = '        pub fn kl_install(img: *const u8, fl: *const KlFlash) -> i32;'
if 'pub fn kl_remove(slot: i32' not in text:
    if text.count(ffi) != 1:
        raise SystemExit("could not find kl_install FFI anchor")
    text = text.replace(ffi, ffi + '\n        #[cfg(feature = "spilib")]\n        pub fn kl_remove(slot: i32, fl: *const KlFlash) -> i32;', 1)

lib_flag = 'const KM_F_LIB: u8 = 1;'
if 'const KM_F_REMOVE: u8' not in text:
    if text.count(lib_flag) != 1:
        raise SystemExit("could not find KM_F_LIB constant")
    text = text.replace(lib_flag, lib_flag + '\n#[cfg(feature = "spilib")]\nconst KM_F_REMOVE: u8 = 8;', 1)

chunk_anchor = '    (r as i8, st.have, st.count, put)\n}'
remove_dispatch = '''    (r as i8, st.have, st.count, put)
}'''
remove_dispatch += '''

/// A signed image marked as K6ERASE removes exactly one module slot.
#[cfg(feature = "spilib")]
#[inline(never)]
pub(super) fn cable_remove_module(app: &mut App, slot: u8) -> i32 {
    if !(slot <= 8 || (16..32).contains(&slot)) { return -6; }
    unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
    let can_write = !app.is_transmitting();
    unsafe { LIB_WRITE = can_write; }
    let r = if can_write { unsafe { ffi::kl_remove(slot as i32, &LIB_FL) } } else { -4 };
    unsafe { LIB_WRITE = false; }
    rl!("K6 LIB erase slot={} r={}", slot, r);
    r
}'''
if 'pub(super) fn cable_remove_module(app: &mut App, slot: u8)' not in text:
    if text.count(chunk_anchor) != 1:
        raise SystemExit("could not find cable_chunk return anchor")
    text = text.replace(chunk_anchor, remove_dispatch, 1)

# The signed transfer path authenticates the request with the radio's configured module key.
ready_anchor = '''    if r == KM_READY {
        if unsafe { *arena::base().add(13) } & KM_F_LIB != 0 {
            put = lib_install(app);
        }
        unsafe { ffi::km_reset() }; // never armed to run
    }'''
ready_replace = '''    if r == KM_READY {
        let flags = unsafe { *arena::base().add(13) };
        let code_len = unsafe { *arena::base().add(4) as usize | ((*arena::base().add(5) as usize) << 8) };
        if flags & KM_F_REMOVE != 0 {
            if flags == (KM_F_LIB | KM_F_REMOVE) && code_len == 7
                && unsafe { core::slice::from_raw_parts(arena::base().add(16), 7) } == b"K6ERASE" {
                put = cable_remove_module(app, unsafe { *arena::base().add(15) });
            } else {
                put = -2;
            }
        } else if flags & KM_F_LIB != 0 {
            put = lib_install(app);
        }
        unsafe { ffi::km_reset() }; // never armed to run
    }'''
if ready_replace not in text and "else if flags & KM_F_REMOVE != 0 {" not in text:
    if text.count(ready_anchor) != 1:
        raise SystemExit("could not find cable_chunk install dispatch")
    text = text.replace(ready_anchor, ready_replace, 1)
pager.write_text(text)

app_mod = root / "app/mod.rs"
app = app_mod.read_text()
app_anchor = '''    pub fn cable_chunk(&mut self, pkt: &[u8]) -> (i8, u8, u8, i32) {
        pager::cable_chunk(self, pkt)
    }'''
app_add = '''
    /// Process an authenticated module-slot erase request from the cable loader.
    #[cfg(feature = "spilib")]
    pub fn cable_remove_module(&mut self, slot: u8) -> i32 {
        pager::cable_remove_module(self, slot)
    }'''
if 'pub fn cable_remove_module(&mut self, slot: u8)' not in app:
    if app.count(app_anchor) != 1:
        raise SystemExit("could not find App::cable_chunk")
    app = app.replace(app_anchor, app_anchor + app_add, 1)
    app_mod.write_text(app)

# Removal changes the cable capability, so older hosts can refuse before sending any bytes.
cps = root / "cps/mod.rs"
cps_text = cps.read_text()
if "d[4] = if cfg!(k6_user_store)" not in cps_text and "d[4] = 2;" not in cps_text and "d[4] = if cfg!(k6_designer) {3} else {2};" not in cps_text and "d[4] = if cfg!(k6_designer) {4} else {2};" not in cps_text:
    if cps_text.count("d[4] = 1;") != 1:
        raise SystemExit("could not find cable protocol version in info reply")
    cps.write_text(cps_text.replace("d[4] = 1;", "d[4] = 2;", 1))
