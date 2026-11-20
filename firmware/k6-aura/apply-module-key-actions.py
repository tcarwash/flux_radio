#!/usr/bin/env python3
"""Expose installed SPI modules through the native programmable key settings."""
from pathlib import Path

root = Path(__file__).parent / "build/aura-fd6818b-port/src"
for file in ("app/settings_ops.rs","app/keyfn.rs","app/keys.rs"):
    p=root/file
    t=p.read_text().replace("(32..64)","(32..255)").replace("(0..64)","(0..255)").replace("MODULE SLOT","UNAVAILABLE")
    p.write_text(t.replace("            pager::key_module_run(app, raw);","            if !pager::key_module_run(app, raw) && app.settings.action_beep {app.radio.play_action_beep(syst,true);}"))

def patch(path, old, new):
    p = root / path
    s = p.read_text()
    if new in s or new.replace('write!(w, "{}", super::pager::key_module_label(idx).unwrap_or("UNAVAILABLE"))','w.write_str(super::pager::key_module_label(idx).unwrap_or("UNAVAILABLE"))').replace('write!(w, "{}", keyfn::from_u8(idx).label())','w.write_str(keyfn::from_u8(idx).label())') in s:
        return
    if s.count(old) != 1:
        raise SystemExit(f"{path}: missing or ambiguous module key anchor")
    p.write_text(s.replace(old, new, 1))

# Existing byte assignments 0..12 stay intact. Slot bindings 32..63
# are persistent and explicitly slot-based, never catalog row indexes.
p = root / "flash_map.rs"
s = p.read_text()
for i in range(19, 25):
    s = s.replace(f"if (32..64).contains(&buf[{i}])", f"if (32..255).contains(&buf[{i}])")
    s = s.replace(f"buf[{i}].min(11)", f"if (32..255).contains(&buf[{i}]) {{ buf[{i}] }} else {{ buf[{i}].min(12) }}")
p.write_text(s)

# Reuse the Catalog cache. Settings rendering never reads SPI.
p = root / "app/pager.rs"
s = p.read_text()
if "fn refresh_catalog(" not in s:
    a = s.index("fn menu_open(app: &mut App)")
    b = s.index('    #[cfg(feature = "explore")]', a)
    head = s[a:b].replace("fn menu_open(", "fn refresh_catalog(").replace("i.flags & 0x06 == 0", "i.flags == 1")
    s = s[:a] + head + '}\n\n#[cfg(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]\n#[inline(never)]\nfn menu_open(app: &mut App) {\n    refresh_catalog(app);\n    let m = menu();\n' + s[b:]
    p.write_text(s)
# One global ID maps to one installed provider. Ambiguous registrations fail closed.
p = root / "app/pager.rs"
t = p.read_text()
a = t.find("// Native programmable keys bind")
if a < 0: a=t.find('#[cfg(feature = "blowfang-host")]\nstatic mut ACTION_IDS')
if a >= 0:
    b = t.index("/// (open, the selected row, rows) for the screen", a)
    t = t[:a]+t[b:]
p.write_text(t)
helpers = '''
#[cfg(feature = "blowfang-host")]
static mut ACTION_IDS: [u8;32] = [0;32];
#[cfg(feature = "blowfang-host")]
static mut PENDING_ACTION: u8 = 0;

#[cfg(feature = "blowfang-host")]
pub(super) fn key_catalog_refresh(app: &mut App) {
    refresh_catalog(app);
    // Read-only discovery; no module is armed or executed here.
    unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
    for n in 0..32 {
        let id = unsafe { ffi::kl_action(n as i32, &LIB_FL) };
        unsafe { ACTION_IDS[n] = if id >= 32 { id as u8 } else { 0 }; }
    }
}
#[cfg(feature = "blowfang-host")]
fn action_slot(id:u8)->Option<u8> {
    if id<32 || id==255 {return None;}
    let mut slot=None;
    for n in 0..32 {
        if unsafe {ACTION_IDS[n]}==id {
            if slot.is_some(){return None;}
            slot=Some(n as u8);
        }
    }
    slot
}
#[cfg(feature = "blowfang-host")]
pub(super) fn key_module_label(raw:u8)->Option<&'static str>{
    let slot=action_slot(raw)?;
    let m=menu();
    for i in 0..m.n as usize{
        if m.slots[i]==slot {
            let name=&m.names[i];
            let len=name.iter().position(|&c|c==0).unwrap_or(12);
            return core::str::from_utf8(&name[..len]).ok().filter(|s|!s.is_empty());
        }
    }
    None
}
#[cfg(feature = "blowfang-host")]
pub(super) fn key_module_present(raw:u8)->bool {action_slot(raw).is_some()}
#[cfg(feature = "blowfang-host")]
#[inline(never)]
pub(super) fn key_module_run(app:&mut App,raw:u8)->bool{
    if app.pager.play_pending || stream_on() || app.transmitting {return false;}
    key_catalog_refresh(app);
    if let Some(slot)=action_slot(raw){
        if host_dispatch_slot(slot,false){
            unsafe{PENDING_ACTION=raw;}
            return true;
        }
    }
    false
}
'''
marker = "/// (open, the selected row, rows) for the screen"
patch("app/pager.rs", marker, helpers + marker)
patch("app/pager.rs", "        pub fn kl_load(slot: i32, fl: *const KlFlash, dst: *mut u8, cap: u32) -> i32;", """        pub fn kl_load(slot: i32, fl: *const KlFlash, dst: *mut u8, cap: u32) -> i32;
        pub fn kl_action(slot:i32,fl:*const KlFlash)->i32;
        pub fn kl_load_action(slot:i32,fl:*const KlFlash,dst:*mut u8,cap:u32,action:u32)->i32;""")
# consume pending action at load, clear regardless of success/return.
patch("app/pager.rs", "    let mut r = LIB_PLAYING;", """    #[cfg(feature = "blowfang-host")]
    let action=unsafe{let id=PENDING_ACTION;PENDING_ACTION=0;id};
    let mut r = LIB_PLAYING;""")
patch("app/pager.rs", "            r = unsafe { ffi::kl_load(n as i32, &LIB_FL, arena::base(), arena::ARENA_BYTES as u32) };", """            #[cfg(feature = "blowfang-host")]
            if action!=0 {
                r=unsafe{ffi::kl_load_action(n as i32,&LIB_FL,arena::base(),arena::ARENA_BYTES as u32,u32::from(action))};
            } else {r=unsafe{ffi::kl_load(n as i32,&LIB_FL,arena::base(),arena::ARENA_BYTES as u32)};}
            #[cfg(not(feature = "blowfang-host"))]
            {r = unsafe { ffi::kl_load(n as i32, &LIB_FL, arena::base(), arena::ARENA_BYTES as u32) };}""")
# initial discovery in deferred polling, not on the deeply nested boot stack.
patch("app/pager.rs", "    let open = unsafe { core::ptr::read_volatile(core::ptr::addr_of!(PENDING_MENU_OPEN)) };", """    #[cfg(feature = "blowfang-host")]
    {
        static mut FIRST:bool=true;
        if unsafe{FIRST}{unsafe{FIRST=false;} key_catalog_refresh(app);}
    }
    let open = unsafe { core::ptr::read_volatile(core::ptr::addr_of!(PENDING_MENU_OPEN)) };""")
patch("app/settings_ops.rs", "    app.mode = Mode::Settings;\n    app.settings_ui.group = None;", "    #[cfg(feature = \"blowfang-host\")]\n    super::pager::key_catalog_refresh(app);\n    app.mode = Mode::Settings;\n    app.settings_ui.group = None;")
old = """            let next = clamp_step(cur, up, 0, 12);
            if next != 0 && keyfn::from_u8(next as u8) == keyfn::KeyFunction::None {
                clamp_step(next, up, 0, 12)
            } else {
                next
            }"""
new = """            #[cfg(feature = "blowfang-host")]
            { keyfn::step_assignment(cur, up) }
            #[cfg(not(feature = "blowfang-host"))]
            {
                let next = clamp_step(cur, up, 0, 12);
                if next != 0 && keyfn::from_u8(next as u8) == keyfn::KeyFunction::None {
                    clamp_step(next, up, 0, 12)
                } else { next }
            }"""
patch("app/settings_ops.rs", old, new)
patch("app/settings_ops.rs", '            let idx = current_value(app, item).clamp(0, 11) as u8;\n            let _ = write!(w, "{}", keyfn::from_u8(idx).label());', '''            let idx = current_value(app, item) as u8;
            #[cfg(feature = "blowfang-host")]
            if (32..255).contains(&idx) {
                let _ = write!(w, "{}", super::pager::key_module_label(idx).unwrap_or("UNAVAILABLE"));
                return;
            }
            let _ = write!(w, "{}", keyfn::from_u8(idx).label());''')
p = root / "app/keyfn.rs"
s = p.read_text()
if "fn step_assignment(" not in s:
    s += '''
#[cfg(feature = "blowfang-host")]
pub(super) fn step_assignment(cur: i32, up: bool) -> i32 {
    let mut v = cur;
    loop {
        v += if up { 1 } else { -1 };
        if !(0..255).contains(&v) { return cur; }
        if v == 0 || from_u8(v as u8) != KeyFunction::None ||
            super::pager::key_module_present(v as u8) { return v; }
    }
}
'''
    p.write_text(s)
# Intercept only normal standby single/long actions; retain TX tone, lock,
# FM close, and all existing native functions.
patch("app/keys.rs", "    match app.mode {\n        Mode::Standby => dispatch_standby(app, syst, ev),", '''    #[cfg(feature = "blowfang-host")]
    if app.mode == Mode::Standby && matches!(ev.key, KeyId::Side1 | KeyId::Side2 | KeyId::Band) {
        let raw = match ev.kind {
            KeyEventKind::Single => short_function(app, ev.key),
            KeyEventKind::Long => long_function(app, ev.key),
            _ => 0,
        };
        if (32..255).contains(&raw) {
            if !pager::key_module_run(app, raw) && app.settings.action_beep {app.radio.play_action_beep(syst,true);}
            return;
        }
    }
    match app.mode {
        Mode::Standby => dispatch_standby(app, syst, ev),''')
print("Applied native module key assignments")
