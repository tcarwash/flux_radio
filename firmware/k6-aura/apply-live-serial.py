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
if "Live(live::Request)" not in cps.read_text():
    insert(cps, "pub enum HostHandshake { Aura, Cable }",
       '''pub enum HostHandshake { Aura, Cable,
    #[cfg(k6_live_serial)]
    Live(live::Request),
}''')
if "live::decode(" not in cps.read_text():
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
if "Some(cps::HostHandshake::Live(req))" not in main.read_text():
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
            || self.power_save_is_asleep()
            || crate::board::power_switch_off(unsafe { &*kd32f328_pac::Gpioa::ptr() })
            { return false; }
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
    pub(crate) fn live_cat_idle(&mut self) -> bool {
        if self.mode()!=Mode::Standby || self.is_transmitting() || self.cw_tx_active
            || self.cw_key_down() || self.radio_mut().ptt_asserted()
            || self.power_save_is_asleep()
            || crate::board::power_switch_off(unsafe { &*kd32f328_pac::Gpioa::ptr() })
            { return false; }
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

# Upgrade already generated checkouts to the shared guard visibility.
a=app.read_text().replace("    fn live_cat_idle(&mut self)", "    pub(crate) fn live_cat_idle(&mut self)")
app.write_text(a)

if "pub(crate) fn live_bulk_idle(" not in app.read_text():
    insert(app, "    pub fn master_index(&self) -> usize {",
           '''    #[cfg(all(k6_live_serial,k6_serial_transport))]
    pub(crate) fn live_bulk_idle(&mut self) -> bool {
        if self.mode()!=Mode::Standby || self.is_transmitting() || self.cw_tx_active
            || self.cw_key_down() || self.radio_mut().ptt_asserted()
            || self.power_save_is_asleep()
            || crate::board::power_switch_off(unsafe { &*kd32f328_pac::Gpioa::ptr() })
            { return false; }
        #[cfg(all(k6_vfo_probe, k6_vfo_spi_probe, k6_vfo_selector, not(k6_no_fsk_rx), feature = "blowfang-host"))]
        if self.resident_selected[self.master] || self.resident_selected[self.watching] { return false; }
        true
    }

    pub fn master_index(&self) -> usize {''')

# Existing build worktrees retain generated app/mod.rs; upgrade the three
# guards in place as well as on a clean source overlay. A power-off MCU may
# keep running while the local switch and RF path are already off.
guard = '|| crate::board::power_switch_off(unsafe { &*kd32f328_pac::Gpioa::ptr() })'
old = '            || self.power_save_is_asleep() { return false; }'
new = '            || self.power_save_is_asleep()\n            ' + guard + '\n            { return false; }'
text = app.read_text()
if text.count(old) == 2:
    text = text.replace(old, new)
elif text.count(new) not in (2, 3):
    raise SystemExit("live serial: cannot locate exactly two module/CAT power guards")
old_bulk = '            || self.cw_key_down() || self.radio_mut().ptt_asserted() { return false; }'
new_bulk = ('            || self.cw_key_down() || self.radio_mut().ptt_asserted()\n'
            '            || self.power_save_is_asleep()\n'
            '            ' + guard + '\n            { return false; }')
if old_bulk in text:
    text = text.replace(old_bulk, new_bulk, 1)
elif new_bulk not in text:
    raise SystemExit("live serial: cannot locate bulk read power guard")
app.write_text(text)

# V2 prefix is recognized separately so body bytes are never passed to the
# legacy detector. Keep the V1 sliding-window path untouched.
insert(cps, "    Live(live::Request),",
       "    Live(live::Request),\n    #[cfg(k6_live_serial)]\n    LiveV2,")
insert(cps, "            if self.len >= live::REQUEST_LEN {",
       """            if self.len >= live::MAGIC_V2.len()
                && self.tail[self.len-live::MAGIC_V2.len()..self.len] == *live::MAGIC_V2 {
                self.len=0;
                return Some(HostHandshake::LiveV2);
            }
            #[cfg(k6_live_serial)]
            if self.len >= live::REQUEST_LEN {""")
if "Some(cps::HostHandshake::LiveV2)" not in main.read_text():
    insert(main, "            Some(cps::HostHandshake::Live(req)) => {",
       """            Some(cps::HostHandshake::LiveV2) => cps::live::dispatch_v2(&mut app, &mut cp.SYST),
            #[cfg(k6_live_serial)]
            Some(cps::HostHandshake::Live(req)) => {""")

# Only the bounded staging call owns the arena. No partial upload spans
# the normal UI/pager scheduler, and execution happens on the original stack.
m=main.read_text().replace("Some(cps::HostHandshake::LiveV2) => cps::live::dispatch_v2(),",
                           "Some(cps::HostHandshake::LiveV2) => cps::live::dispatch_v2(&mut app, &mut cp.SYST),")
main.write_text(m)
if "pub(crate) fn live_ram_begin(" not in app.read_text():
    insert(app, "    pub fn master_index(&self) -> usize {",
           '''    #[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack))]
    pub(crate) fn live_ram_begin(&mut self) -> bool {
        self.live_module_idle() && pager::live_ram_begin(self)
    }
    #[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack))]
    pub(crate) fn live_ram_abort(&mut self) {pager::live_ram_abort(self);}
    #[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack))]
    pub(crate) fn live_ram_finish(&mut self,syst:&mut SYST,seq:u8,n:i32) {
        pager::live_ram_finish(self,syst,seq,n);
    }

    pub fn master_index(&self) -> usize {''')
insert(pager, "/// Enter Aura's sideloaded-module picker from the normal launcher.",
       '''#[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack))]
pub(super) fn live_ram_begin(app:&mut App)->bool {
    if app.pager.play_pending || stream_on() || unsafe {
        LIVE_JOB.is_some() || LIVE_ACTIVE.is_some() || PENDING_LIB_SLOT != 255 || PENDING_MENU_OPEN
    } {return false;}
    live_ram_abort(app);
    true
}

#[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack))]
pub(super) fn live_ram_abort(app:&mut App) {
    unsafe {ffi::km_reset();}
    clear_store(app);
}

#[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack))]
pub(super) fn live_ram_finish(app:&mut App,syst:&mut SYST,seq:u8,n:i32) {
    // The loader's verified image is still in the arena; no other App
    // handling has intervened between the commit and this call.
    let result=if app.live_module_idle() && !unsafe{mod_power_off_requested()} {
        unsafe {LIVE_ACTIVE=Some(seq);LIVE_RESULT=None;}
        run_module(app,syst);
        let r=unsafe{LIVE_ACTIVE=None;LIVE_RESULT};
        return_to_aura(app,syst);
        r
    }else{None};
    live_ram_abort(app);
    crate::cps::live::module_finish(seq,255,n,result);
}

/// Enter Aura's sideloaded-module picker from the normal launcher.''')

# Live mutations reuse the packed library callbacks, with a stricter raw
# PTT/power check before every erase/program. Never alter legacy behavior.
if "pub(crate) fn live_lib_commit(" not in app.read_text():
    insert(app, "    pub fn master_index(&self) -> usize {",
       '''    #[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack,k6_user_store))]
    pub(crate) fn live_lib_commit(&mut self,n:usize)->i32 {
        if !self.live_cat_idle() {return -4;}
        pager::live_lib_commit(self,n)
    }

    pub fn master_index(&self) -> usize {''')
insert(pager, "/// Enter Aura's sideloaded-module picker from the normal launcher.",
       '''#[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack,k6_user_store))]
static mut LIVE_LIB_WRITE: bool = false;

#[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack,k6_user_store))]
fn live_lib_write_allowed()->bool {
    let gpioa=unsafe{&*kd32f328_pac::Gpioa::ptr()};
    crate::board::read_ptt(gpioa) && !crate::board::power_switch_off(gpioa)
}

#[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack,k6_user_store))]
pub(super) fn live_lib_commit(app:&mut App,n:usize)->i32 {
    if !live_lib_write_allowed() {return -4;}
    unsafe extern "C" {fn kl_live_mutate(img:*const u8,n:u32,fl:*const ffi::KlFlash)->i32;}
    let r=unsafe {
        CB_NOR=&mut app.storage.norflash as *mut _;
        LIVE_LIB_WRITE=true;
        LIB_WRITE=true;
        let result=kl_live_mutate(arena::base(),n as u32,core::ptr::addr_of!(LIB_FL));
        LIB_WRITE=false;
        LIVE_LIB_WRITE=false;
        result
    };
    r
}

/// Enter Aura's sideloaded-module picker from the normal launcher.''')
for name,signature in [
    ("prog","unsafe extern \"C\" fn lib_fl_prog(addr: u32, buf: *const u8, n: u32) -> i32 {"),
    ("erase","unsafe extern \"C\" fn lib_fl_erase(addr: u32) -> i32 {"),
]:
    insert(pager,signature,signature+'''
    #[cfg(all(k6_live_serial,feature="spilib",k6_ram0_stack,k6_user_store))]
    if LIVE_LIB_WRITE && !live_lib_write_allowed() {return -1;}''')

# New foreground API level 25 is appended to the existing ABI and only
# available on live-enabled SPI-library firmware. An older module API table
# is not changed in-place, and modules must feature-test api->level.
api_field = '        pub fsk_configured: unsafe extern "C" fn(u32, *mut u16, u32, u32) -> i32,'
insert(pager,api_field,
       api_field+'\n        // level 25: token-bound cooperative cancellation; RX-only callback\n'
       '        pub cancel_poll: unsafe extern "C" fn() -> i32,')
api_init = '    fsk_configured: mod_fsk_configured,'
insert(pager,api_init,api_init+'\n    cancel_poll: mod_cancel_poll,')
# Generated non-live starter profiles can have no raw FSK support, so
# apply-configurable-fsk.py leaves a level-23 initializer instead of the
# level-24 variant used by our live FSK bench. Preserve both existing
# ceilings and advertise level 25 only on live+spilib firmware.
api_level = (
    'level: if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} '
    'else if cfg!(feature = "fd6818b") { 23 } else { 9 },'
)
default_level = 'level: if cfg!(feature = "fd6818b") { 23 } else { 9 },'
# A pristine upstream checkout has no raw-FSK callback field yet.
# apply-configurable-fsk.py first introduces this nested expression;
# subsequent builds can normalize it to the flat else-if form above.
nested_level = (
    'level: if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} '
    'else { if cfg!(feature = "fd6818b") { 23 } else { 9 } },'
)
new_level = (
    'level: if cfg!(all(k6_live_serial, feature = "spilib")) { 25 } '
    'else if cfg!(all(k6_config_fsk, not(k6_no_fsk_rx), feature = "fd6818b")) {24} '
    'else if cfg!(feature = "fd6818b") { 23 } else { 9 },'
)
if new_level not in pager.read_text():
    matches = [anchor for anchor in (api_level, nested_level, default_level)
               if anchor in pager.read_text()]
    if len(matches) != 1:
        raise SystemExit("live serial: cannot identify one generated module API ceiling")
    insert(pager, matches[0], new_level)
insert(pager, '/// Enter Aura\'s sideloaded-module picker from the normal launcher.',
       '''// A synchronous module can sample the live UART only at checkpoints it
// chooses. No interrupts, stack manipulation, or implicit TX controls.
unsafe extern "C" fn mod_cancel_poll() -> i32 {
    #[cfg(all(k6_live_serial,feature="spilib"))]
    {return crate::cps::live::cancel_poll();}
    #[cfg(not(all(k6_live_serial,feature="spilib")))]
    {-1}
}

/// Enter Aura's sideloaded-module picker from the normal launcher.''')
