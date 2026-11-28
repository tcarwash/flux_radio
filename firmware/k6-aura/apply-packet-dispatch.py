#!/usr/bin/env python3
"""Level 27 host dispatcher (opt-in K6_PACKET_MAILBOX=1 + spilib): FIRST
NO-RF, NON-NESTED installed-provider handoff for LOOPBACK-EMU only.

Runs after apply-mode-registry.py and apply-packet-mailbox.py. Appends a
fail-closed host dispatcher to the generated host (src/app/pager.rs):

- tnc phase toggle: set K6MB_BUS.tnc_phase = true only around a run of a
  VERIFIED Flux TNC slot (kl_info: flags == KM_F_LIB, level == 27 exactly,
  byte-exact name "FLUX TNC"), cleared the moment run_module returns.
  Anything else (voice-only, unknown, old-API, unverified) leaves the
  phase false, so stage_packet keeps refusing (-9) and nothing stages.
- stage hardening: only selected mode 0x0200 (LOOPBACK-EMU) may stage;
  voice-only / missing / unknown / old-API modes refuse. Real host clock
  (uptime::now, ms) into created_ms and a finite TTL (30 s), never 0/MAX.
- claim hardening: wrap-safe expiry check against the host clock; an
  expired slot fails CLOSED (-6, FAILED) and can never replay.
- post-TNC dispatch in lib_run (never nested in run_module): after a TNC
  run, if exactly one packet is STAGED, lib_run locates the one known
  packet provider (kl_info scan: exact "LOOPBACK-EMU", flags LIB,
  level == 27 exactly), authenticates it with kl_load_action(slot, 35)
  (SipHash tag + K6_ACTION registration), and runs it exactly once via
  run_module with the provider phase (tnc_phase == false). No retry loop.
  The provider itself claims mode 0x0200, publishes RX, completes SENT.
- radio safety: the provider image is RAM loopback (no tx/fsk/afsk/tune
  symbols allowed in the dispatch path); run_module already dekeys,
  restores GPIO/frequency and re-arms after every run.
- KISS resume is UNSOLVED and fail-closed: the TNC session is NOT
  auto-resumed after dispatch (queued UART bytes may be lost; generic
  KISS has no ACK to prove otherwise). Completion is observed with
  poll_result / take_rx on a later TNC session. No RF-capable modem is
  staged or dispatched here: LOOPBACK-EMU only until UART/flow-control
  resumption is proven.

Default (non-mailbox) build is behavior-identical: every lib_run change
is a cfg-split whose not(mailbox) arm keeps the original lines.
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
        raise SystemExit("packet dispatch: anchor not unique: " + anchor[:60])
    s = s.replace(anchor, addition, 1)


# 0. Ordering: mailbox core must be present first.
for need in ["unsafe extern \"C\" fn mod_mb_stage_packet(",
             "unsafe extern \"C\" fn mod_mb_claim_packet(",
             "static mut K6MB_BUS",
             "tnc_phase: false"]:
    if need not in s:
        raise SystemExit("packet dispatch: run apply-packet-mailbox.py first, missing: " + need)

# 1. ffi: authenticated per-action load (tag + K6_ACTION check in C).
ffi_anchor = "        pub fn kl_info(slot: i32, fl: *const KlFlash, out: *mut KlInfo) -> i32;"
ffi_add = ("        #[cfg(all(feature = \"spilib\", k6_packet_mailbox))]\n"
           "        pub fn kl_load_action(slot: i32, fl: *const KlFlash, dst: *mut u8, cap: u32, action: u32) -> i32;")
if "pub fn kl_load_action(slot:" not in s:
    # The pinned production patch and synthetic overlay fixture differ only
    # in indentation of the foreign-function declaration.
    aliases = [ffi_anchor, ffi_anchor.replace("        pub fn", "    pub fn")]
    found = [a for a in aliases if s.count(a) == 1]
    if not found:
        raise SystemExit("packet dispatch: no verified kl_info FFI anchor")
    insert(found[0], found[0] + "\n" + ffi_add)

# 2. Finite TTL constant next to the other mailbox constants.
ttl_anchor = "#[cfg(k6_packet_mailbox)]\nconst K6MB_TX_REFUSED: i32 = 2;"
insert(ttl_anchor, ttl_anchor + "\n#[cfg(k6_packet_mailbox)]\nconst K6MB_TTL_MS: u32 = 30_000;")

# 3. Stage hardening: LOOPBACK-EMU (0x0200) only; real clock + finite TTL.
stage_anchor = "        if mode_id == 0 || mode_id == 0xFFFF {"
stage_guard = (
    "        // LOOPBACK-EMU only; voice, unknown and RF modem IDs refuse.\n"
    "        if mode_id != 0x0200 {\n"
    "            return -5;\n"
    "        }\n"
)
if "if mode_id != 0x0200 {" not in s:
    insert(stage_anchor, stage_guard + stage_anchor)

insert("        bus.tx.created_ms = 0;",
      "        bus.tx.created_ms = match mb_host_clock_ms() { Some(ms) => ms, None => return -6 };")
insert("        bus.tx.ttl_ms = 0xFFFFFFFF;",
      "        bus.tx.ttl_ms = K6MB_TTL_MS;")

# 4. Claim hardening: wrap-safe expiry against the host clock.
expiry_anchor = "        if mode_id != bus.tx.mode_id || (held_caps & bus.tx.need_caps) != bus.tx.need_caps {"
insert(expiry_anchor,
       "        // Finite TTL: an unstaged-for-too-long slot fails closed and\n"
        "        // can never replay. TIM14 wrap IRQ extends u32 clock.\n"
       "        if bus.tx.ttl_ms == 0 || bus.tx.ttl_ms == 0xFFFFFFFF\n"
       "            || mb_host_clock_ms().map_or(true, |ms| ms.wrapping_sub(bus.tx.created_ms) >= bus.tx.ttl_ms)\n"
       "        {\n"
       "            bus.tx.state = K6MB_FAILED as u8;\n"
       "            return -6;\n"
       "        }\n" + expiry_anchor)

# 5. Dispatcher body: verified-TNC phase toggle + one-shot provider handoff.
dispatch_block = '''/// Level 27 dispatcher slice 1 (opt-in K6_PACKET_MAILBOX=1 + spilib):
/// one-shot NO-RF handoff to the installed LOOPBACK-EMU provider only,
/// entered only on the TNC's K6MB_TNC_STAGED (-40) return with one packet
/// STAGED in host storage.
/// Never nested: runs in lib_run after run_module returned and the TNC
/// released UART/KISS. KISS-session resume is UNSOLVED: this never
/// re-runs the TNC; completion is polled on a later TNC session.
/// Reliable shared clock across TNC -> provider arena swap. The usual
/// uptime::now() is only a u16 TIM14 count at 100us/tick and wraps each
/// 6.55 s; txdog::ticks() unwraps it with the TIM14 IRQ to u32. If the
/// IRQ table is absent (lean builds without k6_lean_txdog), refuse the
/// mailbox stage/claim. Truncation wraps at ~4.97 days: a payload crossing
/// that boundary is conservatively expired by the wrap-safe difference.
#[cfg(k6_packet_mailbox)]
fn mb_host_clock_ms() -> Option<u32> {
    let (wraps, cnt) = crate::hal::txdog::ticks()?;
    let tick_100us = wraps.wrapping_mul(0x1_0000).wrapping_add(cnt);
    Some(tick_100us / 10)
}

#[cfg(all(k6_packet_mailbox, feature = "spilib"))]
fn mb_name_is(a: &[u8; 12], b: &[u8; 12]) -> bool {
    let mut i = 0;
    while i < 12 {
        if a[i] != b[i] {
            return false;
        }
        i += 1;
    }
    true
}

/// Exact TNC identity: byte-exact "FLUX TNC" library image at exactly
/// level 27 (never >=: dev-radio API29 assigns these tail slots
/// differently, so a level guess would mis-call). Verified by the
/// library loader: this slot is the one kl_load tag-checked before the
/// run that just returned.
#[cfg(all(k6_packet_mailbox, feature = "spilib"))]
fn mb_tnc_slot_verified(app: &mut App, slot: u8) -> bool {
    if slot as i32 >= KL_SLOTS {
        return false;
    }
    unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
    let mut info = ffi::KlInfo::default();
    if unsafe { ffi::kl_info(slot as i32, &LIB_FL, &mut info) } != 0 {
        return false;
    }
    info.flags == 1 && info.level == 27 && mb_name_is(&info.name, b"FLUX TNC\\0\\0\\0\\0")
}

/// The one known packet provider: exact "LOOPBACK-EMU" library image at
/// exactly level 27. Voice-only (SOTTO TEST/RFTEST), unknown, and old-API
/// images never match. Returns its slot, if installed.
#[cfg(all(k6_packet_mailbox, feature = "spilib"))]
fn mb_find_loopback_provider(app: &mut App) -> Option<u8> {
    unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
    let mut slot: i32 = 0;
    while slot < KL_SLOTS {
        let mut info = ffi::KlInfo::default();
        if unsafe { ffi::kl_info(slot, &LIB_FL, &mut info) } == 0
            && info.flags == 1
            && info.level == 27
            && mb_name_is(&info.name, b"LOOPBACK-EMU")
        {
            return Some(slot as u8);
        }
        slot += 1;
    }
    None
}

/// Set the TNC phase only for a verified TNC slot; else stay fail-closed.
#[cfg(all(k6_packet_mailbox, feature = "spilib"))]
fn mb_begin_tnc_phase(app: &mut App, slot: u8) {
    if mb_tnc_slot_verified(app, slot) {
        unsafe { K6MB_BUS.tnc_phase = true; }
    }
}

/// Always clear the TNC phase once the module returned (UART/KISS is
/// released by the TNC itself before k6_main returns).
#[cfg(all(k6_packet_mailbox, feature = "spilib"))]
fn mb_end_tnc_phase() {
    unsafe { K6MB_BUS.tnc_phase = false; }
}

/// One-shot provider handoff. Returns true when it consumed the shared
/// store itself (failed provider load: caller must NOT mark KEPT).
/// Host phase is false for the whole provider run; exactly one
/// run_module, never a retry loop. De-key/radio restore ride on
/// run_module, which dekeys, restores GPIO/frequency and re-arms.
#[cfg(all(k6_packet_mailbox, feature = "spilib"))]
fn mb_dispatch_loopback_provider(app: &mut App, syst: &mut SYST, tnc_slot: u8) -> bool {
    let (generation, state, ttl_ms, created_ms) = unsafe {
        let bus = &*core::ptr::addr_of!(K6MB_BUS);
        (bus.tx.generation, bus.tx.state, bus.tx.ttl_ms, bus.tx.created_ms)
    };
    if state != K6MB_STAGED as u8 || generation == 0 {
        return false; // normal TNC return: nothing staged, keep KEPT
    }
    if !mb_tnc_slot_verified(app, tnc_slot) {
        rl!("K6 MB refused: TNC slot unverified, no dispatch");
        return false; // staged bytes expire via the claim TTL gate
    }
    if ttl_ms == 0 || ttl_ms == 0xFFFFFFFF
        || mb_host_clock_ms().map_or(true, |ms| ms.wrapping_sub(created_ms) >= ttl_ms)
    {
        rl!("K6 MB refused: staged packet expired, no dispatch");
        return false;
    }
    let prov = match mb_find_loopback_provider(app) {
        Some(p) => p,
        None => {
            rl!("K6 MB refused: LOOPBACK-EMU not installed, no dispatch");
            return false;
        }
    };
    unsafe { ffi::km_reset() };
    let r = unsafe {
        ffi::kl_load_action(
            prov as i32,
            &LIB_FL,
            arena::base(),
            arena::ARENA_BYTES as u32,
            35,
        )
    };
    if r <= 0 {
        rl!("K6 MB refused: provider auth failed r={}", r);
        unsafe { ffi::km_reset() };
        clear_store(app);
        return true; // store cleared: caller must not set KEPT
    }
    unsafe { LAST_SLOT = prov; }
    rl!("K6 MB dispatch LOOPBACK-EMU slot={} gen={}", prov, generation);
    run_module(app, syst); // provider phase (tnc_phase == false); dekeys + restores
    // UNSOLVED: no TNC auto-resume here; queued UART bytes may be lost.
    // Completion is polled (poll_result/take_rx) on a later TNC session.
    rl!("K6 MB provider done; TNC NOT auto-resumed (UART continuity unverified)");
    false
}

fn run_module(app: &mut App, syst: &mut SYST) {'''
insert("fn run_module(app: &mut App, syst: &mut SYST) {", dispatch_block)

# 6. lib_run: phase toggle around the TNC run, dispatch after it returns.
lib_anchor = ("        run_module(app, syst);\n"
              "        unsafe { KEPT = true };")
lib_new = ("        #[cfg(all(k6_packet_mailbox, feature = \"spilib\"))]\n"
           "        mb_begin_tnc_phase(app, n);\n"
           "        run_module(app, syst);\n"
           "        #[cfg(all(k6_packet_mailbox, feature = \"spilib\"))]\n"
           "        let mb_cleared = {\n"
           "            mb_end_tnc_phase();\n"
           "            mb_dispatch_loopback_provider(app, syst, n)\n"
           "        };\n"
           "        #[cfg(all(k6_packet_mailbox, feature = \"spilib\"))]\n"
           "        if !mb_cleared {\n"
           "            unsafe { KEPT = true };\n"
           "        }\n"
           "        #[cfg(not(all(k6_packet_mailbox, feature = \"spilib\")))]\n"
           "        unsafe { KEPT = true };")
insert(lib_anchor, lib_new)

pager_path.write_text(s)
print("packet dispatch slice 1 (LOOPBACK-EMU one-shot) applied")
