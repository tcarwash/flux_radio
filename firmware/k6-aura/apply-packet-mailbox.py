#!/usr/bin/env python3
"""Level 27 (opt-in K6_PACKET_MAILBOX=1, requires K6_MODE_REGISTRY=1).

Appends the API27 host-owned V1 packet-mailbox tail to the generated host
(src/app/pager.rs) after apply-mode-registry.py: the u32 mb_version field,
the six mailbox callbacks (stage_packet, poll_result, take_rx,
claim_packet, complete_packet, publish_rx), the host-owned static mailbox
(length 128, generation allocator, TNC/provider phase flag), and the
fail-closed callback bodies. Advertises API level 27 ONLY behind
cfg(k6_packet_mailbox); without the cfg the level-26 ceiling is preserved
byte-for-byte, so the default image is unchanged.

Host-owned static mailbox: the storage lives in firmware .bss (host side),
never in a module scratch/arena, so it survives foreground module
switching; the payload is inline bytes addressed only by generation, never
by pointer. Single TX slot plus a 4-frame RX ring with backpressure. Every
refusal is fail-closed (negative code, no state change, never key).

Phase split (never nested): stage_packet is the TNC phase (called only
while the TNC module runs, tnc phase == true); claim_packet /
complete_packet / publish_rx are the provider phase (called only from the
K6_ACTION(35) provider run after the TNC returned, phase == false);
poll_result / take_rx only observe. The callback bodies mirror the checks
in c/k6/mod/k6_packet_mailbox.h (empty/busy/too-long, nested-phase,
expiry wrap-safe, identity + capability, CRC-16/CCITT-FALSE, generation
consumed exactly once, RX FULL never overwrites, take invalidates).

INCOMPLETE (prominent, by design of this first safe slice): the dispatcher
wiring is NOT part of this overlay and is a separate later task:
(a) the phase-flag toggle (tnc == true only while the TNC module runs,
back to false when the TNC returns -40 / K6MB_TNC_STAGED);
(b) the real per-provider need_caps, host clock (created_ms), and ttl
(currently pinned to TX-only / 0 / u32::MAX placeholders);
(c) the provider held_caps lookup (currently the loopback 0x03 constant);
(d) the expiry-window check against the host clock (currently vacuous).
Until that lands, the phase flag stays false forever, so stage_packet
always refuses (-9 NESTED) and nothing can ever be staged; claims
therefore also refuse (nothing staged). Do NOT claim routed TX or
packet mode works: no dispatcher, no TX path, no nested provider call
is implemented here.

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
        raise SystemExit("packet mailbox: anchor not unique: " + anchor[:60])
    s = s.replace(anchor, addition, 1)


# 1. API table fields: after the level-26 mode_info field when present,
# else after the newest always-present field available.
field_block = (
    "        pub mb_version: u32,\n"
    "        pub stage_packet: unsafe extern \"C\" fn(u16, *const u8, u16) -> i32,\n"
    "        pub poll_result: unsafe extern \"C\" fn(*mut u32, *mut u32, *mut i32) -> i32,\n"
    "        pub take_rx: unsafe extern \"C\" fn(*mut u8, u16, *mut u16, *mut u32) -> i32,\n"
    "        pub claim_packet: unsafe extern \"C\" fn(u16, u32, *mut u8, u16, *mut u32) -> i32,\n"
    "        pub complete_packet: unsafe extern \"C\" fn(u32, i32) -> i32,\n"
    "        pub publish_rx: unsafe extern \"C\" fn(u16, *const u8, u16) -> i32,"
)
if "pub stage_packet: unsafe extern" not in s:
    anchors = [
        "        pub mode_info: unsafe extern \"C\" fn(u32, *mut super::K6ModeInfo) -> i32,",
        "        pub cancel_poll: unsafe extern \"C\" fn() -> i32,",
        "        pub fsk_configured: unsafe extern \"C\" fn(u32, *mut u16, u32, u32) -> i32,",
        '        pub aprs_position_set: unsafe extern "C" fn(i32, i32) -> i32,',
    ]
    hit = [a for a in anchors if s.count(a) == 1]
    if not hit:
        raise SystemExit("packet mailbox: no API field anchor")
    insert(hit[0], hit[0] + "\n" + field_block)

# 2. API table initializer.
init_block = (
    "    mb_version: 1,\n"
    "    stage_packet: mod_mb_stage_packet,\n"
    "    poll_result: mod_mb_poll_result,\n"
    "    take_rx: mod_mb_take_rx,\n"
    "    claim_packet: mod_mb_claim_packet,\n"
    "    complete_packet: mod_mb_complete_packet,\n"
    "    publish_rx: mod_mb_publish_rx,"
)
if "stage_packet: mod_mb_stage_packet," not in s:
    anchors = [
        "    mode_info: mod_mode_info,",
        "    cancel_poll: mod_cancel_poll,",
        "    fsk_configured: mod_fsk_configured,",
        "    aprs_position_set: mod_aprs_position_set,",
    ]
    hit = [a for a in anchors if s.count(a) == 1]
    if not hit:
        raise SystemExit("packet mailbox: no API initializer anchor")
    insert(hit[0], hit[0] + "\n" + init_block)

# 3. Level ceiling: advertise 27 only with cfg(k6_packet_mailbox),
# composing with the level-26 clause when the registry overlay ran first.
new_level = "level: if cfg!(k6_packet_mailbox) { 27 } else "
if new_level not in s:
    anchor = "level: if "
    if s.count(anchor) < 1:
        raise SystemExit("packet mailbox: no API level anchor")
    # Rewrite the first level expression only; sibling overlays each own one.
    i = s.index(anchor)
    tail = s[i + len("level: "):]
    s = s[:i] + "level: if cfg!(k6_packet_mailbox) { 27 } else " + tail

# 4. Host-owned static mailbox, CRC, and fail-closed callbacks.
impl_block = '''/// Level 27 (opt-in K6_PACKET_MAILBOX=1, requires K6_MODE_REGISTRY=1):
/// host-owned V1 packet mailbox. Layout and checks mirror
/// c/k6/mod/k6_packet_mailbox.h, which is authoritative for semantics.
///
/// INCOMPLETE: the dispatcher (phase toggle, real need_caps/clock/ttl,
/// held_caps lookup, expiry check) is a separate later task. Until it
/// lands, K6MB_TNC_PHASE stays false, stage_packet always refuses, and
/// nothing can be staged: do not claim routed TX or packet mode works.
#[cfg(k6_packet_mailbox)]
const K6MB_VERSION_U32: u32 = 1;
#[cfg(k6_packet_mailbox)]
const K6MB_MAX_PAYLOAD: usize = 128;
#[cfg(k6_packet_mailbox)]
const K6MB_RX_DEPTH: usize = 4;
#[cfg(k6_packet_mailbox)]
const K6MB_STAGED: u32 = 1;
#[cfg(k6_packet_mailbox)]
const K6MB_CLAIMED: u32 = 2;
#[cfg(k6_packet_mailbox)]
const K6MB_COMPLETE: u32 = 3;
#[cfg(k6_packet_mailbox)]
const K6MB_FAILED: u32 = 4;
#[cfg(k6_packet_mailbox)]
const K6MB_TX_SENT: i32 = 0;
#[cfg(k6_packet_mailbox)]
const K6MB_TX_FAILED: i32 = 1;
#[cfg(k6_packet_mailbox)]
const K6MB_TX_REFUSED: i32 = 2;

/// Single TX slot: inline payload, no pointers. All fields are u8/u16/u32
/// scalar or inline arrays, so the 32-bit ARM layout is padding-free and
/// matches k6mb_slot_t field order in c/k6/mod/k6_packet_mailbox.h.
#[cfg(k6_packet_mailbox)]
#[repr(C)]
struct K6MbTxSlot {
    version: u8,
    state: u8,
    mode_id: u16,
    len: u16,
    need_caps: u16,
    generation: u32,
    created_ms: u32,
    ttl_ms: u32,
    crc: u16,
    payload: [u8; 128],
    result: i32,
}

#[cfg(k6_packet_mailbox)]
impl Default for K6MbTxSlot {
    fn default() -> Self {
        Self {
            version: 1,
            state: 0,
            mode_id: 0,
            len: 0,
            need_caps: 0,
            generation: 0,
            created_ms: 0,
            ttl_ms: 0,
            crc: 0,
            payload: [0; 128],
            result: 0,
        }
    }
}

/// One RX frame (provider -> TNC direction).
#[cfg(k6_packet_mailbox)]
#[repr(C)]
#[derive(Clone, Copy)]
struct K6MbRxFrame {
    mode_id: u16,
    len: u16,
    generation: u32,
    data: [u8; 128],
}

/// Whole host-owned bus: one TX slot plus the RX ring. Lives in firmware
/// .bss (static mut, host side), never in module scratch/arena.
#[cfg(k6_packet_mailbox)]
#[repr(C)]
struct K6MbBus {
    tnc_phase: bool,
    rx_head: u8,
    rx_count: u8,
    gen: u32,
    tx: K6MbTxSlot,
    rx: [K6MbRxFrame; 4],
}

#[cfg(k6_packet_mailbox)]
static mut K6MB_BUS: K6MbBus = K6MbBus {
    tnc_phase: false,
    rx_head: 0,
    rx_count: 0,
    gen: 0,
    tx: K6MbTxSlot {
        version: 1,
        state: 0,
        mode_id: 0,
        len: 0,
        need_caps: 0,
        generation: 0,
        created_ms: 0,
        ttl_ms: 0,
        crc: 0,
        payload: [0; 128],
        result: 0,
    },
    rx: [K6MbRxFrame {
        mode_id: 0,
        len: 0,
        generation: 0,
        data: [0; 128],
    }; 4],
};

/// CRC-16/CCITT-FALSE step: fold one byte into a running CRC.
/// Must stay identical to k6mb_crc_step in k6_packet_mailbox.h.
#[cfg(k6_packet_mailbox)]
fn k6mb_crc_step(mut c: u16, b: u8) -> u16 {
    c ^= (b as u16) << 8;
    let mut k = 0;
    while k < 8 {
        if c & 0x8000 != 0 {
            c = (c << 1) ^ 0x1021;
        } else {
            c <<= 1;
        }
        k += 1;
    }
    c
}

/// CRC input framing: mode_id LE, len LE, need_caps LE, then payload bytes.
/// Must stay identical to k6mb_slot_crc in k6_packet_mailbox.h.
#[cfg(k6_packet_mailbox)]
fn k6mb_slot_crc(mode_id: u16, len: u16, need_caps: u16, p: &[u8]) -> u16 {
    let hdr = [
        (mode_id & 0xff) as u8,
        ((mode_id >> 8) & 0xff) as u8,
        (len & 0xff) as u8,
        ((len >> 8) & 0xff) as u8,
        (need_caps & 0xff) as u8,
        ((need_caps >> 8) & 0xff) as u8,
    ];
    let mut c: u16 = 0xFFFF;
    let mut i = 0;
    while i < 6 {
        c = k6mb_crc_step(c, hdr[i]);
        i += 1;
    }
    let mut j = 0;
    while j < (len as usize) {
        c = k6mb_crc_step(c, p[j]);
        j += 1;
    }
    c
}

/// Next nonzero generation (wraps safely past zero).
#[cfg(k6_packet_mailbox)]
fn k6mb_next_gen(bus: &mut K6MbBus) -> u32 {
    bus.gen = bus.gen.wrapping_add(1);
    if bus.gen == 0 {
        bus.gen = 1;
    }
    bus.gen
}

/// TNC phase: copy one frame into host storage. Requires the TNC phase
/// flag (set only while the TNC module runs; a separate dispatcher task
/// owns that toggle, so without it this refuses fail-closed). Mirrors
/// k6mb_stage: EMPTY slot, 1..128 bytes, nonzero ttl and mode_id.
unsafe extern "C" fn mod_mb_stage_packet(mode_id: u16, bytes: *const u8, len: u16) -> i32 {
    #[cfg(not(k6_packet_mailbox))]
    {
        let _ = (mode_id, bytes, len);
        return -1;
    }
    #[cfg(k6_packet_mailbox)]
    {
        if bytes.is_null() {
            return -1;
        }
        let bus = unsafe { &mut *core::ptr::addr_of_mut!(K6MB_BUS) };
        if !bus.tnc_phase {
            return -9;
        }
        if bus.tx.state != 0 {
            return -3;
        }
        if len == 0 {
            return -1;
        }
        if (len as usize) > K6MB_MAX_PAYLOAD {
            return -2;
        }
        if mode_id == 0 || mode_id == 0xFFFF {
            return -1;
        }
        let src = unsafe { core::slice::from_raw_parts(bytes, len as usize) };
        // need_caps comes from the TNC's own validated provider record;
        // the dispatcher task fills a real value here. Until it lands,
        // refusing is unreachable anyway (phase gate above), and the
        // constant keeps the CRC framing honest.
        let need_caps: u16 = 0x01;
        bus.tx.version = K6MB_VERSION_U32 as u8;
        bus.tx.state = K6MB_STAGED as u8;
        bus.tx.mode_id = mode_id;
        bus.tx.len = len;
        bus.tx.need_caps = need_caps;
        bus.tx.generation = k6mb_next_gen(bus);
        bus.tx.created_ms = 0;
        bus.tx.ttl_ms = 0xFFFFFFFF;
        bus.tx.payload[..len as usize].copy_from_slice(src);
        bus.tx.crc = k6mb_slot_crc(mode_id, len, need_caps, src);
        bus.tx.result = 0;
        0
    }
}

/// Observe the TX slot without consuming it. Mirrors k6mb_poll.
unsafe extern "C" fn mod_mb_poll_result(
    generation: *mut u32,
    state: *mut u32,
    result: *mut i32,
) -> i32 {
    #[cfg(not(k6_packet_mailbox))]
    {
        let _ = (generation, state, result);
        return -1;
    }
    #[cfg(k6_packet_mailbox)]
    {
        if generation.is_null() || state.is_null() || result.is_null() {
            return -1;
        }
        let bus = unsafe { &*core::ptr::addr_of!(K6MB_BUS) };
        unsafe {
            *generation = bus.tx.generation;
            *state = bus.tx.state as u32;
            *result = bus.tx.result;
        }
        0
    }
}

/// TNC takes the oldest RX frame. Take invalidates (zeroes + advances),
/// so a frame can never be taken twice. Mirrors k6mb_rx_take.
unsafe extern "C" fn mod_mb_take_rx(
    out: *mut u8,
    cap: u16,
    mode_id: *mut u16,
    len: *mut u32,
) -> i32 {
    #[cfg(not(k6_packet_mailbox))]
    {
        let _ = (out, cap, mode_id, len);
        return -1;
    }
    #[cfg(k6_packet_mailbox)]
    {
        if out.is_null() || mode_id.is_null() || len.is_null() {
            return -1;
        }
        let bus = unsafe { &mut *core::ptr::addr_of_mut!(K6MB_BUS) };
        if bus.rx_count == 0 {
            return -11;
        }
        let slot = bus.rx_head as usize;
        let flen = bus.rx[slot].len as usize;
        if (cap as usize) < flen {
            return -1;
        }
        let dst = unsafe { core::slice::from_raw_parts_mut(out, flen) };
        dst.copy_from_slice(&bus.rx[slot].data[..flen]);
        unsafe {
            *mode_id = bus.rx[slot].mode_id;
            *len = bus.rx[slot].len as u32;
        }
        bus.rx[slot].data = [0; 128];
        bus.rx[slot].mode_id = 0;
        bus.rx[slot].len = 0;
        bus.rx[slot].generation = 0;
        bus.rx_head = ((bus.rx_head as usize + 1) % K6MB_RX_DEPTH) as u8;
        bus.rx_count -= 1;
        0
    }
}

/// Provider entry (action 35): claim the staged packet for the exact
/// (mode_id, generation) pair. Requires provider phase (phase == false),
/// STAGED state, live generation, and a valid CRC; any mismatch except
/// the phase gate marks the slot FAILED so it can never replay. Held
/// capability check mirrors k6mb_claim (need_caps must be covered).
unsafe extern "C" fn mod_mb_claim_packet(
    mode_id: u16,
    generation: u32,
    out: *mut u8,
    cap: u16,
    len: *mut u32,
) -> i32 {
    #[cfg(not(k6_packet_mailbox))]
    {
        let _ = (mode_id, generation, out, cap, len);
        return -1;
    }
    #[cfg(k6_packet_mailbox)]
    {
        if out.is_null() || len.is_null() {
            return -1;
        }
        let bus = unsafe { &mut *core::ptr::addr_of_mut!(K6MB_BUS) };
        if bus.tnc_phase {
            return -9;
        }
        if bus.tx.state != K6MB_STAGED as u8 {
            return -4;
        }
        if generation == 0 || generation != bus.tx.generation {
            bus.tx.state = K6MB_FAILED as u8;
            return -8;
        }
        // Held capability of the dispatched provider identity. The
        // dispatcher task fills the real per-provider value here; the
        // loopback provider holds TX+RX (0x03). Until the dispatcher
        // lands, nothing is ever staged (phase gate in stage), so this
        // comparison is unreachable on a live path.
        let held_caps: u16 = 0x03;
        if mode_id != bus.tx.mode_id || (held_caps & bus.tx.need_caps) != bus.tx.need_caps {
            bus.tx.state = K6MB_FAILED as u8;
            return -5;
        }
        if bus.tx.crc
            != k6mb_slot_crc(
                bus.tx.mode_id,
                bus.tx.len,
                bus.tx.need_caps,
                &bus.tx.payload[..bus.tx.len as usize],
            )
        {
            bus.tx.state = K6MB_FAILED as u8;
            return -7;
        }
        if (cap as usize) < bus.tx.len as usize {
            return -1;
        }
        let n = bus.tx.len as usize;
        let dst = unsafe { core::slice::from_raw_parts_mut(out, n) };
        dst.copy_from_slice(&bus.tx.payload[..n]);
        unsafe {
            *len = bus.tx.len as u32;
        }
        bus.tx.state = K6MB_CLAIMED as u8;
        0
    }
}

/// Provider reports its TX result. Only CLAIMED + exact generation.
/// Mirrors k6mb_complete.
unsafe extern "C" fn mod_mb_complete_packet(generation: u32, result: i32) -> i32 {
    #[cfg(not(k6_packet_mailbox))]
    {
        let _ = (generation, result);
        return -1;
    }
    #[cfg(k6_packet_mailbox)]
    {
        let bus = unsafe { &mut *core::ptr::addr_of_mut!(K6MB_BUS) };
        if bus.tnc_phase {
            return -9;
        }
        if bus.tx.state != K6MB_CLAIMED as u8 {
            return -4;
        }
        if generation == 0 || generation != bus.tx.generation {
            return -8;
        }
        if result != K6MB_TX_SENT && result != K6MB_TX_FAILED && result != K6MB_TX_REFUSED {
            return -1;
        }
        bus.tx.result = result;
        bus.tx.state = K6MB_COMPLETE as u8;
        0
    }
}

/// Provider offers one received frame. Backpressure: FULL when the ring
/// holds 4 untaken frames; the mailbox never overwrites. Requires provider
/// phase (never nested in the TNC). Mirrors k6mb_rx_put.
unsafe extern "C" fn mod_mb_publish_rx(mode_id: u16, bytes: *const u8, len: u16) -> i32 {
    #[cfg(not(k6_packet_mailbox))]
    {
        let _ = (mode_id, bytes, len);
        return -1;
    }
    #[cfg(k6_packet_mailbox)]
    {
        if bytes.is_null() {
            return -1;
        }
        let bus = unsafe { &mut *core::ptr::addr_of_mut!(K6MB_BUS) };
        if bus.tnc_phase {
            return -9;
        }
        if bus.rx_count as usize >= K6MB_RX_DEPTH {
            return -10;
        }
        if len == 0 || (len as usize) > K6MB_MAX_PAYLOAD {
            return if len == 0 { -1 } else { -2 };
        }
        let slot = ((bus.rx_head as usize) + (bus.rx_count as usize)) % K6MB_RX_DEPTH;
        let src = unsafe { core::slice::from_raw_parts(bytes, len as usize) };
        bus.rx[slot].mode_id = mode_id;
        bus.rx[slot].len = len;
        bus.rx[slot].generation = k6mb_next_gen(bus);
        bus.rx[slot].data[..len as usize].copy_from_slice(src);
        bus.rx_count += 1;
        0
    }
}

'''
if "unsafe extern \"C\" fn mod_mb_stage_packet(" not in s:
    anchors = [
        "unsafe extern \"C\" fn mod_mode_info(",
        "unsafe extern \"C\" fn mod_cancel_poll()",
        "/// Enter Aura's sideloaded-module picker from the normal launcher.",
    ]
    hit = [a for a in anchors if s.count(a) == 1]
    if not hit:
        raise SystemExit("packet mailbox: no callback insertion anchor")
    insert(hit[0], impl_block + hit[0])

pager_path.write_text(s)
print("packet mailbox API level 27 applied")
