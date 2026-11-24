#!/usr/bin/env python3
"""Level 26 (opt-in K6_MODE_REGISTRY=1): read-only installed-provider discovery.

Appends the k6_api_t.mode_info callback to the generated host
(src/app/pager.rs) and advertises API level 26 behind cfg(k6_mode_registry);
without the cfg the previous ceiling is preserved byte-for-byte, so the
default image is unchanged. The callback enumerates, in slot order, only
explicitly validated installed library providers via SPI reads
(ffi::kl_info): the canonical SOTTO TEST voice module (ID 0x0100, VOICE
only, max_payload 0) and, behind cfg(k6_mode_rftest), the experimental
SOTTO RFTEST fixture (ID 0x0101). Anything else yields no entry. The host
never loads or runs module code, asserts no packet TX, and never transmits.
Idempotent: safe to re-run on an already patched checkout.
"""
from pathlib import Path

pager_path = Path(__file__).resolve().parent / "build/aura-fd6818b-port/src/app/pager.rs"
s = pager_path.read_text()


def insert(anchor, addition):
    global s
    if addition in s:
        return
    if s.count(anchor) != 1:
        raise SystemExit("mode registry: anchor not unique: " + anchor[:60])
    s = s.replace(anchor, addition, 1)


# 1. API table field: after cancel_poll on live firmware, else after the
# newest always-present field available.
field = "        pub mode_info: unsafe extern \"C\" fn(u32, *mut K6ModeInfo) -> i32,"
if field not in s:
    anchors = [
        "        pub cancel_poll: unsafe extern \"C\" fn() -> i32,",
        "        pub fsk_configured: unsafe extern \"C\" fn(u32, *mut u16, u32, u32) -> i32,",
        '        pub aprs_position_set: unsafe extern "C" fn(i32, i32) -> i32,',
    ]
    hit = [a for a in anchors if s.count(a) == 1]
    if not hit:
        raise SystemExit("mode registry: no API field anchor")
    insert(hit[0], hit[0] + "\n" + field)

# 2. API table initializer.
init = "    mode_info: mod_mode_info,"
if init not in s:
    anchors = [
        "    cancel_poll: mod_cancel_poll,",
        "    fsk_configured: mod_fsk_configured,",
        "    aprs_position_set: mod_aprs_position_set,",
    ]
    hit = [a for a in anchors if s.count(a) == 1]
    if not hit:
        raise SystemExit("mode registry: no API initializer anchor")
    insert(hit[0], hit[0] + "\n" + init)

# 3. Level ceiling: advertise 26 only with cfg(k6_mode_registry).
new_level = "level: if cfg!(k6_mode_registry) { 26 } else if "
if new_level not in s:
    anchor = "level: if "
    if s.count(anchor) < 1:
        raise SystemExit("mode registry: no API level anchor")
    # Rewrite the first level expression only; sibling overlays each own one.
    i = s.index(anchor)
    tail = s[i + len("level: "):]
    s = s[:i] + "level: if cfg!(k6_mode_registry) { 26 } else " + tail

# 4. Record type, name matcher, and host callback.
impl_block = '''/// Level 26 (opt-in K6_MODE_REGISTRY=1): read-only installed-provider
/// discovery record. Layout matches k6_mode_info_t in c/k6/k6_mod.h.
#[repr(C)]
#[derive(Default, Clone, Copy)]
pub struct K6ModeInfo {
    pub mode_id: u16,
    pub flags: u8,
    pub name_len: u8,
    pub name: [u8; 12],
    pub max_payload: u16,
}

/// Byte-exact 12-byte name compare (kl_info NUL-pads short names; the C
/// info.name array is signed, so compare as unsigned bytes).
#[cfg(k6_mode_registry)]
fn k6mode_name_eq(a: &[u8; 12], b: &[u8; 12]) -> bool {
    let mut i = 0;
    while i < 12 {
        if a[i] != b[i] {
            return false;
        }
        i += 1;
    }
    true
}

/// Level 26: enumerate validated installed library providers in slot order.
/// Returns 1 with *out filled, 0 past the end, -1 on a null pointer, a
/// refused SPI read, or a corrupted library entry. SPI reads only
/// (ffi::kl_info): never loads or runs module code, asserts no packet TX,
/// and never transmits. Safe to call from a running module: it performs no
/// code load, only the same NOR read path the level-5 fl_read callback uses
/// while a module runs.
unsafe extern "C" fn mod_mode_info(index: u32, out: *mut K6ModeInfo) -> i32 {
    #[cfg(not(k6_mode_registry))]
    {
        let _ = (index, out);
        return -1;
    }
    #[cfg(k6_mode_registry)]
    {
        if out.is_null() {
            return -1;
        }
        let c = unsafe { &mut *core::ptr::addr_of_mut!(MOD_CTX) };
        if c.app.is_null() {
            return -1;
        }
        let app = unsafe { &mut *c.app };
        unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
        let mut seen: u32 = 0;
        let mut slot: i32 = 0;
        while slot < KL_SLOTS {
            let mut info = ffi::KlInfo::default();
            let r = unsafe { ffi::kl_info(slot, &LIB_FL, &mut info) };
            if r == 0 {
                let mut id: u16 = 0;
                if info.flags == 1 && info.level > 0 && (info.level as u32) <= 26 {
                    if k6mode_name_eq(&info.name, b"SOTTO TEST\\0\\0") {
                        id = 0x0100;
                    }
                    #[cfg(k6_mode_rftest)]
                    if id == 0 && k6mode_name_eq(&info.name, b"SOTTO RFTEST") {
                        id = 0x0101;
                    }
                }
                if id != 0 {
                    if seen == index {
                        let o = unsafe { &mut *out };
                        o.mode_id = id;
                        o.flags = 4; // VOICE only: never packet data
                        o.max_payload = 0;
                        if id == 0x0100 {
                            o.name_len = 10;
                            o.name = *b"SOTTO TEST\\0\\0";
                        } else {
                            o.name_len = 12;
                            o.name = *b"SOTTO RFTEST";
                        }
                        return 1;
                    }
                    seen += 1;
                }
            } else if r != -1 {
                // KL_EMPTY (-1) means "no image here, keep scanning"; any
                // other error is a busy or corrupted library.
                return -1;
            }
            slot += 1;
        }
        0
    }
}

'''
if "unsafe extern \"C\" fn mod_mode_info(" not in s:
    anchors = [
        "unsafe extern \"C\" fn mod_cancel_poll()",
        "/// Enter Aura's sideloaded-module picker from the normal launcher.",
    ]
    hit = [a for a in anchors if s.count(a) == 1]
    if not hit:
        raise SystemExit("mode registry: no callback insertion anchor")
    insert(hit[0], impl_block + hit[0])

pager_path.write_text(s)
print("installed mode discovery API level 26 applied")
