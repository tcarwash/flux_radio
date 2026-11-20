#!/usr/bin/env python3
"""Install the opt-in, non-destructive Flux live serial dispatcher."""
from pathlib import Path

here = Path(__file__).resolve().parent
src = here / "build/aura-fd6818b-port/src"

def insert(path, old, new):
    data = path.read_text()
    if new in data:
        return
    if data.count(old) != 1:
        raise SystemExit(f"live serial: expected one anchor in {path}: {old!r}")
    path.write_text(data.replace(old, new, 1))

(src / "cps/live.rs").write_text((here / "live-serial.rs").read_text())
cps = src / "cps/mod.rs"
insert(cps, "mod frame;", 'mod frame;\n#[cfg(all(feature = "blowfang-host", k6_live_serial))]\npub mod live;')
insert(cps, "pub enum HostHandshake { Aura, Cable }",
       '''pub enum HostHandshake { Aura, Cable,
    #[cfg(k6_live_serial)]
    Live(live::Request),
}''')
insert(cps, "            if self.len >= HANDSHAKE.len()",
       '''            #[cfg(k6_live_serial)]
            if self.len >= live::REQUEST_LEN {
                if let Some(req) = live::decode(&self.tail[self.len - live::REQUEST_LEN..self.len]) {
                    self.len = 0;
                    return Some(HostHandshake::Live(req));
                }
            }
            if self.len >= HANDSHAKE.len()''')
main = src / "main.rs"
insert(main, "            Some(cps::HostHandshake::Aura) =>",
       '''            #[cfg(k6_live_serial)]
            Some(cps::HostHandshake::Live(req)) => {
                cps::live::dispatch(&mut app, &mut cp.SYST, req);
            }
            Some(cps::HostHandshake::Aura) =>''')
app = src / "app/mod.rs"
if "pub fn live_tune(" not in app.read_text():
    insert(app, "    pub fn master_index(&self) -> usize {",
       '''    #[cfg(k6_live_serial)]
    pub fn live_modulation(&self, side: usize) -> u8 {
        modulation_to_raw(self.sides[side].cfg.modulation)
    }

    // Deliberately volatile: no SPI writes on a CAT tune. Do not disturb
    // memories, reverse mode, or a resident digital engine.
    #[cfg(k6_live_serial)]
    pub fn live_tune(&mut self, syst: &mut SYST, hz: u32) -> u8 {
        if hz % 10 != 0 || !((136_000_000..=174_000_000).contains(&hz)
            || (400_000_000..=480_000_000).contains(&hz)) {
            return 2;
        }
        #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
        if self.resident_selected[self.master] || self.resident_selected[self.watching] { return 1; }
        let side = &mut self.sides[self.master];
        if side.vfo_chan != ChVfoMode::Vfo || side.reversed { return 1; }
        side.rx_freq_hz = hz;
        side.refresh_cfg_freqs();
        self.input.clear();
        self.sync_watching_to_master(syst);
        0
    }

    pub fn master_index(&self) -> usize {''')

# Queue through the existing foreground module runner on its own stack.
# The live request only accepts the job; completion is emitted after cleanup.
if "pub fn live_module_idle(" not in app.read_text():
    insert(app, "    pub fn master_index(&self) -> usize {",
       '''    #[cfg(all(k6_live_serial, feature = "spilib"))]
    pub fn live_module_idle(&mut self) -> bool {
        if self.mode() != Mode::Standby || self.is_transmitting()
            || self.cw_key_down() || self.radio_mut().ptt_asserted()
            || self.power_save_is_asleep() { return false; }
        #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
        if self.resident_selected[self.master] || self.resident_selected[self.watching] { return false; }
        true
    }

    #[cfg(all(k6_live_serial, feature = "spilib"))]
    pub fn live_queue_module(&mut self, slot: u8, seq: u8, higher: bool) -> u8 {
        if !self.live_module_idle() { return 1; }
        pager::live_queue_module(self, slot, seq, higher)
    }

    pub fn master_index(&self) -> usize {''')
pager = src / "app/pager.rs"
# The insert helper's idempotence check matches the entire replacement.
insert(pager, "/// Enter Aura's sideloaded-module picker from the normal launcher.",
       '''#[cfg(all(k6_live_serial, feature = "spilib"))]
static mut LIVE_JOB: Option<(u8, u8, bool)> = None;
#[cfg(all(k6_live_serial, feature = "spilib"))]
static mut LIVE_ACTIVE: Option<u8> = None;
#[cfg(all(k6_live_serial, feature = "spilib"))]
static mut LIVE_LOAD: i32 = -200;
#[cfg(all(k6_live_serial, feature = "spilib"))]
static mut LIVE_RESULT: Option<i32> = None;

#[cfg(all(k6_live_serial, feature = "spilib"))]
fn live_module_allowed(app: &mut App, slot: u8, higher: bool) -> bool {
    if slot as i32 >= KL_SLOTS || app.pager.play_pending || stream_on() { return false; }
    unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
    let mut info = ffi::KlInfo::default();
    (unsafe { ffi::kl_info(slot as i32, &LIB_FL, &mut info) == 0 })
        && info.flags == 1 && info.level > 0 && (higher || info.level <= 2)
}

#[cfg(all(k6_live_serial, feature = "spilib"))]
pub(super) fn live_queue_module(app: &mut App, slot: u8, seq: u8, higher: bool) -> u8 {
    if unsafe { core::ptr::read_volatile(core::ptr::addr_of!(LIVE_JOB)).is_some() || core::ptr::read_volatile(core::ptr::addr_of!(LIVE_ACTIVE)).is_some() } { return 1; }
    if !live_module_allowed(app, slot, higher) { return 2; }
    if !host_dispatch_slot(slot, false) { return 1; }
    unsafe { LIVE_JOB = Some((slot, seq, higher)); }
    0
}

/// Enter Aura's sideloaded-module picker from the normal launcher.''')
insert(pager, '        lib_run(app, syst, slot);\n        #[cfg(feature = "blowfang-host")]',
       '''        #[cfg(all(k6_live_serial, feature = "spilib"))]
        let live = unsafe { core::ptr::replace(core::ptr::addr_of_mut!(LIVE_JOB), None) };
        #[cfg(all(k6_live_serial, feature = "spilib"))]
        if let Some((expected, seq, higher)) = live {
            if slot != expected || !app.live_module_idle() || !live_module_allowed(app, slot, higher) {
                crate::cps::live::module_finish(seq, expected, -200, None);
                return;
            }
            unsafe { LIVE_ACTIVE = Some(seq); LIVE_LOAD = -200; LIVE_RESULT = None; PENDING_ACTION = 0; }
        }
        lib_run(app, syst, slot);
        #[cfg(all(k6_live_serial, feature = "spilib"))]
        if let Some((_, seq, _)) = live {
            let (load, result) = unsafe {
                LIVE_ACTIVE = None;
                (LIVE_LOAD, LIVE_RESULT)
            };
            crate::cps::live::module_finish(seq, slot, load, result);
        }
        #[cfg(feature = "blowfang-host")]''')
insert(pager, '    rl!("K6 LIB run slot={} r={}", n, r);',
       '''    #[cfg(all(k6_live_serial, feature = "spilib"))]
    if unsafe { core::ptr::read_volatile(core::ptr::addr_of!(LIVE_ACTIVE)).is_some() } { unsafe { LIVE_LOAD = r; } }
    rl!("K6 LIB run slot={} r={}", n, r);''')
insert(pager, '    tlm_add(t.as_bytes());',
       '''    #[cfg(all(k6_live_serial, feature = "spilib"))]
    if let Some(seq) = unsafe { LIVE_ACTIVE } {
        crate::cps::live::module_text(seq, t.as_bytes());
    }
    tlm_add(t.as_bytes());''')
insert(pager, '    rl!("K6 MOD ret={} ({:#x}) in {} ms stack_free={} B", ret, ret, ms, sfree);',
       '''    #[cfg(all(k6_live_serial, feature = "spilib"))]
    if unsafe { core::ptr::read_volatile(core::ptr::addr_of!(LIVE_ACTIVE)).is_some() } { unsafe { LIVE_RESULT = Some(ret); } }
    rl!("K6 MOD ret={} ({:#x}) in {} ms stack_free={} B", ret, ret, ms, sfree);''')

# Volatile CAT changes use the existing app control path, not SPI writes.
if "pub fn live_set_mode(" not in app.read_text():
    insert(app, "    pub fn master_index(&self) -> usize {",
           '''    #[cfg(k6_live_serial)]
    fn live_cat_idle(&mut self) -> bool {
        if self.mode()!=Mode::Standby || self.is_transmitting() || self.cw_tx_active
            || self.cw_key_down() || self.radio_mut().ptt_asserted()
            || self.power_save_is_asleep() { return false; }
        #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
        if self.resident_selected[self.master] || self.resident_selected[self.watching] { return false; }
        true
    }

    #[cfg(k6_live_serial)]
    pub fn live_set_mode(&mut self, syst: &mut SYST, arg: u32) -> u8 {
        if arg & !0x1ff != 0 || (arg & 0xff)>4 { return 2; }
        if !self.live_cat_idle() { return 1; }
        let side=&mut self.sides[self.master];
        if side.vfo_chan!=ChVfoMode::Vfo || side.reversed {return 1;}
        side.cfg.modulation=modulation_from_raw(arg as u8);
        side.cfg.wide_band=arg & 0x100 != 0;
        self.input.clear();
        self.ani_target_override=None;
        self.cw_hang_ticks=0;
        self.sync_watching_to_master(syst);
        0
    }

    #[cfg(k6_live_serial)]
    pub fn live_select_vfo(&mut self, syst: &mut SYST, arg: u32) -> u8 {
        if arg>1 {return 2;}
        if !self.live_cat_idle() {return 1;}
        let side=&self.sides[arg as usize];
        if side.vfo_chan!=ChVfoMode::Vfo || side.reversed {return 1;}
        if self.master!=arg as usize {side_ops::switch_side(self,syst);}
        0
    }

    pub fn master_index(&self) -> usize {''')
