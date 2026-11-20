#!/usr/bin/env python3
"""Wire the sideloaded module picker into Aura's normal launcher for host builds."""
from pathlib import Path

root = Path(__file__).parent / "build/aura-fd6818b-port/src"

def edit(rel: str, edits: list[tuple[str, str]]) -> None:
    path = root / rel
    text = path.read_text()
    for old, new in edits:
        if new in text:
            continue
        count = text.count(old)
        if count != 1:
            raise SystemExit(f"{rel}: expected one anchor ({count}): {old[:100]!r}")
        text = text.replace(old, new, 1)
    path.write_text(text)


edit("app/launcher.rs", [
    ("    Pager,\n}", "    #[cfg(not(feature = \"blowfang-host\"))]\n    Pager,\n    #[cfg(feature = \"blowfang-host\")]\n    Blowfang,\n}"),
    ("    LauncherEntry::Pager,\n];", "    #[cfg(not(feature = \"blowfang-host\"))]\n    LauncherEntry::Pager,\n    #[cfg(feature = \"blowfang-host\")]\n    LauncherEntry::Blowfang,\n];"),
    ("            LauncherEntry::Pager => \"VOICE MSG\",", "            #[cfg(not(feature = \"blowfang-host\"))]\n            LauncherEntry::Pager => \"VOICE MSG\",\n            #[cfg(feature = \"blowfang-host\")]\n            LauncherEntry::Blowfang => \"CATALOG\","),
    ("            LauncherEntry::Pager => true,", "            #[cfg(not(feature = \"blowfang-host\"))]\n            LauncherEntry::Pager => true,\n            #[cfg(feature = \"blowfang-host\")]\n            LauncherEntry::Blowfang => true,"),
    ("            LauncherEntry::Pager => Mode::Pager,", "            #[cfg(not(feature = \"blowfang-host\"))]\n            LauncherEntry::Pager => Mode::Pager,\n            #[cfg(feature = \"blowfang-host\")]\n            LauncherEntry::Blowfang => Mode::Pager,"),
])

launcher_path = root / "app/launcher.rs"
launcher_text = launcher_path.read_text()
launcher_text = launcher_text.replace('            #[cfg(feature = "blowfang-host")]\n            LauncherEntry::Blowfang => "BLOWFANG",\n', '')
items = '''pub const LAUNCHER_ITEMS: &[LauncherEntry] = &[
    LauncherEntry::Settings,
    LauncherEntry::ChannelMgr,
    LauncherEntry::Contacts,
    LauncherEntry::FmRadio,
    LauncherEntry::ScanQt,
    LauncherEntry::Search,
    LauncherEntry::Spectrum,
    LauncherEntry::Satellite,
    LauncherEntry::Sstv,
    #[cfg(not(feature = "blowfang-host"))]
    LauncherEntry::Pager,
    #[cfg(feature = "blowfang-host")]
    LauncherEntry::Blowfang,
];'''
host_items = '''#[cfg(feature = "blowfang-host")]
pub const LAUNCHER_ITEMS: &[LauncherEntry] = &[
    LauncherEntry::Settings,
    LauncherEntry::ChannelMgr,
    #[cfg(not(k6_no_scan))]
    LauncherEntry::FmRadio,
    LauncherEntry::Blowfang,
];

#[cfg(not(feature = "blowfang-host"))]
pub const LAUNCHER_ITEMS: &[LauncherEntry] = &[
    LauncherEntry::Settings, LauncherEntry::ChannelMgr, LauncherEntry::Contacts,
    LauncherEntry::FmRadio, LauncherEntry::ScanQt, LauncherEntry::Search,
    LauncherEntry::Spectrum, LauncherEntry::Satellite, LauncherEntry::Sstv,
    LauncherEntry::Pager,
];'''
if host_items not in launcher_text:
    if launcher_text.count(items) == 1:
        launcher_path.write_text(launcher_text.replace(items, host_items, 1))
    elif '#[cfg(feature = "blowfang-host")]\npub const LAUNCHER_ITEMS:' not in launcher_text:
        raise SystemExit("launcher.rs: cannot find launcher item array")

edit("app/keys.rs", [
    ("                        LauncherEntry::Pager => pager::enter(app, syst),",
     "                        #[cfg(not(feature = \"blowfang-host\"))]\n"
     "                        LauncherEntry::Pager => pager::enter(app, syst),\n"
     "                        #[cfg(feature = \"blowfang-host\")]\n"
     "                        LauncherEntry::Blowfang => pager::host_enter(app),"),
])
keys_path = root / "app/keys.rs"
keys_text = keys_path.read_text()
for mode in ("Contacts", "Fm", "Scan", "Search", "ScanQt", "Spectrum", "Satellite | Mode::SatelliteTracking", "Sstv"):
    old = f"        Mode::{mode} =>"
    if old in keys_text:
        keys_text = keys_text.replace(old, f"        #[cfg(not(feature = \"blowfang-host\"))]\n{old}", 1)
if "        #[cfg(feature = \"blowfang-host\")]\n        _ => {}" not in keys_text:
    anchor = "        Mode::Pager => pager::dispatch(app, syst, ev),\n    }"
    if keys_text.count(anchor) != 1:
        raise SystemExit("keys.rs: cannot locate top-level mode dispatch")
    keys_text = keys_text.replace(anchor, "        Mode::Pager => pager::dispatch(app, syst, ev),\n"
                                  "        #[cfg(feature = \"blowfang-host\")]\n        _ => {},\n    }", 1)
keys_path.write_text(keys_text)

pager_path = root / "app/pager.rs"
text = pager_path.read_text()
dispatch_anchor = "pub(super) fn dispatch(app: &mut App, syst: &mut SYST, ev: KeyEvent) {\n"
dispatch_prefix = dispatch_anchor + (
    "    #[cfg(feature = \"blowfang-host\")]\n    {\n"
    "        if matches!(ev.key, KeyId::Vm | KeyId::Ab) && matches!(ev.kind, KeyEventKind::Single | KeyEventKind::Long) {\n"
    "            return_to_aura(app, syst);\n            return;\n        }\n"
    "        if ev.key == KeyId::Exit && ev.kind == KeyEventKind::Long { return_to_aura(app, syst); return; }\n"
    "        if menu_key(app, syst, ev) { return; }\n"
    "        return;\n    }\n"
    "    #[cfg(not(feature = \"blowfang-host\"))]\n    {\n"
)
if dispatch_prefix not in text:
    if text.count(dispatch_anchor) != 1:
        raise SystemExit("pager.rs: cannot locate pager dispatcher")
    text = text.replace(dispatch_anchor, dispatch_prefix, 1)
text = text.replace(
    '    if !matches!(ev.key, KeyId::Digit6 | KeyId::Pound) {\n'
    '        report_cfg(app, key_name(ev.key));\n'
    '    }\n}\n\n/// The receive settings changed:',
    '    if !matches!(ev.key, KeyId::Digit6 | KeyId::Pound) {\n'
    '        report_cfg(app, key_name(ev.key));\n'
    '    }\n    }\n}\n\n/// The receive settings changed:',
)
text = text.replace(
    "#[cfg(all(feature = \"explore\", feature = \"spilib\"))]\nconst MENU_CORE: u8 = 2;",
    "#[cfg(all(feature = \"explore\", feature = \"spilib\"))]\nconst MENU_CORE: u8 = 2;\n"
    "#[cfg(all(feature = \"blowfang-host\", not(feature = \"explore\"), feature = \"spilib\"))]\n"
    "const MENU_CORE: u8 = 1;",
)
text = text.replace(
    '#[cfg(all(feature = "explore", feature = "spilib"))]',
    '#[cfg(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]',
)
# The above broad replacement also covers the menu state, row, open, and key functions.
text = text.replace(
    '#[cfg(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]\nconst MENU_CORE: u8 = 2;',
    '#[cfg(all(feature = "spilib", feature = "explore"))]\nconst MENU_CORE: u8 = 2;',
)
text = text.replace(
    "    let core: &[u8] = match i {\n        0 => b\"EXPLORE\",\n        1 => b\"ARENA\",\n        _ => b\"\",\n    };",
    "    #[cfg(feature = \"explore\")]\n"
    "    let core: &[u8] = match i { 0 => b\"EXPLORE\", 1 => b\"ARENA\", _ => b\"\" };\n"
    "    #[cfg(all(feature = \"blowfang-host\", not(feature = \"explore\")))]\n"
    "    let core: &[u8] = if i == 0 { b\"AURA RADIO\" } else { b\"\" };",
)
text = text.replace(
    "    m.sel = if explore::on() { 0 } else { 1 };",
    "    #[cfg(feature = \"explore\")]\n    { m.sel = if explore::on() { 0 } else { 1 }; }\n"
    "    #[cfg(all(feature = \"blowfang-host\", not(feature = \"explore\")))]\n"
    "    { m.sel = 0; }",
)
old_select = '''                match sel {
                    0 => {
                        if !explore::on() {
                            explore::enter(app, syst);
                        }
                    }
                    1 => {
                        if explore::on() {
                            explore::leave(app, syst);
                        }
                    }
                    _ => {
                        rl!("K6 MENU module pre-leave explore={}", explore::on());
                        if explore::on() {
                            explore::leave(app, syst);
                        }
                        rl!("K6 MENU module post-leave");
                        queue_lib_run(m.slots[(sel - MENU_CORE) as usize]);
                    }
                }'''
new_select = '''                #[cfg(feature = "explore")]
                match sel {
                    0 => { if !explore::on() { explore::enter(app, syst); } }
                    1 => { if explore::on() { explore::leave(app, syst); } }
                    _ => {
                        if explore::on() { explore::leave(app, syst); }
                        queue_lib_run(m.slots[(sel - MENU_CORE) as usize]);
                    }
                }
                #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]
                if sel == 0 {
                    return_to_aura(app, syst);
                } else {
                    queue_lib_run(m.slots[(sel - MENU_CORE) as usize]);
                }'''
# A later dispatcher patch may already have replaced the host-only queue call.
dispatched_select = new_select.rsplit(
    "                    queue_lib_run(m.slots[(sel - MENU_CORE) as usize]);",
    1)
dispatched_select = "                    let _ = host_dispatch_slot(m.slots[(sel - MENU_CORE) as usize], true);".join(dispatched_select)
if new_select not in text and dispatched_select not in text:
    if text.count(old_select) != 1:
        raise SystemExit("pager.rs: could not locate menu row dispatch")
    text = text.replace(old_select, new_select, 1)
text = text.replace(
    '            KeyId::Exit if matches!(ev.kind, KeyEventKind::Single) => m.on = false,',
    '            KeyId::Exit if matches!(ev.kind, KeyEventKind::Single) => {\n'
    '                m.on = false;\n'
    '                #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n'
    '                return_to_aura(app, syst);\n'
    '            },',
)
text = text.replace(
    "    if explore::on() {\n        explore::touch();\n    } else {\n        explore::voice_key(); // a key in the arena: its quiet minute starts again\n    }",
    "    #[cfg(feature = \"explore\")]\n"
    "    if explore::on() { explore::touch(); } else { explore::voice_key(); }\n"
    "    #[cfg(all(feature = \"blowfang-host\", not(feature = \"explore\")))]\n"
    "    { let _ = app; }",
)
text = text.replace(
    '    #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n    explore::voice_key();',
    '    #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n    { let _ = app; }',
)
text = text.replace(
    "#[cfg(all(feature = \"explore\", feature = \"spilib\"))]\n    if menu_key(app, syst, ev) {",
    "#[cfg(all(feature = \"spilib\", any(feature = \"explore\", feature = \"blowfang-host\")))]\n"
    "    if menu_key(app, syst, ev) {",
)
text = text.replace(
    "    #[cfg(all(feature = \"explore\", feature = \"spilib\"))]\n    { menu().on = false; }",
    "    #[cfg(all(feature = \"spilib\", any(feature = \"explore\", feature = \"blowfang-host\")))]\n"
    "    { menu().on = false; }",
)
# `arq_owe` is only used for optional over-the-air telemetry. The lean host keeps the caller
# for a shared module runner but must not compile out the helper it references.
text = text.replace(
    "#[cfg(not(feature = \"lean\"))]\nfn arq_owe(",
    "#[cfg(any(not(feature = \"lean\"), feature = \"blowfang-host\"))]\nfn arq_owe(",
)
if "pub(super) fn host_enter(app: &mut App)" not in text:
    anchor = "/// (open, the selected row, rows) for the screen\n"
    addition = '''/// Enter Aura's sideloaded-module picker from the normal launcher.
#[cfg(feature = "blowfang-host")]
pub(super) fn host_enter(app: &mut App) {
    app.mode = Mode::Pager;
    app.input.clear();
    unsafe { core::ptr::write_volatile(core::ptr::addr_of_mut!(PENDING_MENU_OPEN), true) };
    rl!("K6 MENU host");
}

'''
    if text.count(anchor) != 1:
        raise SystemExit("pager.rs: could not locate menu-state anchor")
    text = text.replace(anchor, addition + anchor, 1)
text = text.replace(
    '            KeyId::Exit if matches!(ev.kind, KeyEventKind::Single) => m.on = false,',
    '            KeyId::Exit if matches!(ev.kind, KeyEventKind::Single) => {\n'
    '                m.on = false;\n'
    '                #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n'
    '                return_to_aura(app, syst);\n'
    '            },',
)
text = text.replace(
    '        lib_run(app, syst, slot);\n    }\n}',
    '        lib_run(app, syst, slot);\n'
    '        #[cfg(feature = "blowfang-host")]\n        menu_open(app);\n'
    '    }\n}',
)
pager_path.write_text(text)

edit("ui/pager.rs", [
    ('#[cfg(all(feature = "explore", feature = "spilib"))]\npub fn draw_menu',
     '#[cfg(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]\npub fn draw_menu'),
    ('    out!(b, "MODULES {}/{}", sel + 1, rows);',
     '    #[cfg(feature = "explore")]\n    out!(b, "MODULES {}/{}", sel + 1, rows);\n'
     '    #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n    out!(b, "CATALOG {}/{}", sel + 1, rows);'),
])

pager_ui_path = root / "ui/pager.rs"
pager_ui_text = pager_ui_path.read_text()
pager_ui_text = pager_ui_text.replace(
    '    #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n    out!(b, "BLOWFANG {}/{}", sel + 1, rows);\n',
    '',
)
pager_ui_path.write_text(pager_ui_text)

edit("ui/mod.rs", [
    ('        #[cfg(all(feature = "explore", feature = "spilib"))]\n        let menu = app::pager::menu_state().0;\n'
     '        #[cfg(not(all(feature = "explore", feature = "spilib")))]\n        let menu = false;',
     '        #[cfg(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]\n'
     '        let menu = app::pager::menu_state().0;\n'
     '        #[cfg(not(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host"))))]\n'
     '        let menu = false;'),
    ('            #[cfg(all(feature = "explore", feature = "spilib"))]\n            if menu {',
     '            #[cfg(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]\n            if menu {'),
    ('    #[cfg(all(not(banded), feature = "explore", feature = "spilib"))]\n    if app::pager::menu_state().0 {',
     '    #[cfg(all(not(banded), feature = "spilib", any(feature = "explore", feature = "blowfang-host")))]\n'
     '    if app::pager::menu_state().0 {'),
    ('        app::Mode::Pager => draw_pager(display.as_draw_target(), &*app),',
     '        app::Mode::Pager => {\n'
     '            #[cfg(feature = "blowfang-host")]\n'
     '            pager::draw_menu(display.as_draw_target());\n'
     '            #[cfg(all(not(feature = "blowfang-host"), feature = "explore", feature = "spilib"))]\n'
     '            if app::pager::menu_state().0 { pager::draw_menu(display.as_draw_target()); }\n'
     '            else { draw_pager(display.as_draw_target(), &*app); }\n'
     '            #[cfg(not(all(feature = "spilib", any(feature = "explore", feature = "blowfang-host"))))]\n'
     '            draw_pager(display.as_draw_target(), &*app);\n'
     '        },'),
])

ui_path = root / "ui/mod.rs"
ui_text = ui_path.read_text()
for mode in ("Contacts", "Fm", "Scan", "Search", "ScanQt", "Spectrum", "Satellite | app::Mode::SatelliteTracking", "Sstv"):
    old = f"        app::Mode::{mode} =>"
    if old in ui_text:
        ui_text = ui_text.replace(old, f"        #[cfg(not(feature = \"blowfang-host\"))]\n{old}", 1)
if "        #[cfg(feature = \"blowfang-host\")]\n        _ => draw_standby(display.as_draw_target(), &*app)," not in ui_text:
    anchor = "        _ => draw_standby(display.as_draw_target(), &*app),\n    }\n    display.flush();"
    if ui_text.count(anchor) != 1:
        raise SystemExit("ui/mod.rs: cannot locate mode draw fallback")
    ui_text = ui_text.replace(anchor, "        #[cfg(feature = \"blowfang-host\")]\n"
                              "        _ => draw_standby(display.as_draw_target(), &*app),\n"
                              "        #[cfg(not(feature = \"blowfang-host\"))]\n"
                              "        _ => draw_standby(display.as_draw_target(), &*app),\n    }\n    display.flush();", 1)
ui_path.write_text(ui_text)

main_path = root / "main.rs"
main_text = main_path.read_text()
voice_start = "        // Voice pager: packets in, then the blocking playback / test TX\n"
voice_end = "        // power sw\n"
if "        #[cfg(not(feature = \"blowfang-host\"))]\n        {\n" + voice_start not in main_text:
    if main_text.count(voice_start) != 1 or main_text.count(voice_end) != 1:
        raise SystemExit("main.rs: cannot locate normal-loop voice pager block")
    start = main_text.index(voice_start)
    end = main_text.index(voice_end, start)
    body = main_text[start:end]
    main_text = main_text[:start] + "        #[cfg(not(feature = \"blowfang-host\"))]\n        {\n" + body + "        }\n\n" + main_text[end:]
main_path.write_text(main_text)

# Keep a failed module selection visible when the menu is reopened, and expose
# the slot metadata needed to distinguish a corrupt header from a loader limit.
pager_path = root / "app/pager.rs"
pager_text = pager_path.read_text()
pager_text = pager_text.replace('        pub fn kl_last_load_reason() -> i32;\n', '')
old_sel_reset = '''    #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]
    { m.sel = 0; }'''
new_sel_reset = '''    #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]
    { m.sel = m.sel.min(MENU_CORE.saturating_add(m.n).saturating_sub(1)); }'''
if new_sel_reset not in pager_text:
    if pager_text.count(old_sel_reset) != 1:
        raise SystemExit("pager.rs: cannot find host menu selection reset")
    pager_text = pager_text.replace(old_sel_reset, new_sel_reset, 1)
load_anchor = '            r = unsafe { ffi::kl_load(n as i32, &LIB_FL, arena::base(), arena::ARENA_BYTES as u32) };'
load_diag = '''            let mut info = ffi::KlInfo::default();
            let info_result = unsafe { ffi::kl_info(n as i32, &LIB_FL, &mut info) };
            rl!("K6 LIB hdr slot={} info={} code={} flags={:#04x} cap={} serial={}",
                n, info_result, info.code_len, info.flags, arena::ARENA_BYTES, info.serial);
''' + load_anchor
if load_diag in pager_text:
    pager_text = pager_text.replace(load_diag, load_anchor, 1)
reason_logged = '            rl!("K6 LIB loaded slot={} r={} reason={}", n, r, unsafe { ffi::kl_last_load_reason() });'
reason_plain = '            rl!("K6 LIB loaded slot={} r={}", n, r);'
if reason_logged in pager_text:
    pager_text = pager_text.replace(reason_logged, reason_plain, 1)
pager_path.write_text(pager_text)

# Use a scratch-tail stack for LCD callbacks. The module keeps its own stack; callbacks
# must not consume the same frames while drawing a screen.
pager_text = pager_path.read_text()
if "fn module_lcd_stack_top()" not in pager_text:
    marker = '#[cfg(feature = "blowfang-host")]\nfn cardlink_module_loaded()'
    helper = """#[cfg(feature = "blowfang-host")]
fn module_lcd_stack_top() -> *mut u8 {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        unsafe { core::ptr::addr_of_mut!(crate::MODULE_PAGE_BUFFER_STORAGE).cast::<u8>().add(1152) }
    } else { arena::end() as *mut u8 }
}

"""
    if pager_text.count(marker) != 1:
        raise SystemExit("pager.rs: missing callback-stack insertion point")
    pager_text = pager_text.replace(marker, helper + marker, 1)
pager_text = pager_text.replace("arena::end().saturating_sub(1024) // reserve isolated LCD callback stack", "arena::end().saturating_sub(1040) // reserve aligned LCD callback stack below globals")
pager_text = pager_text.replace("arena::end().saturating_sub(1024) // isolated LCD callback stack", "arena::end().saturating_sub(1040) // reserve aligned LCD callback stack below globals")
pager_text = pager_text.replace("arena::end().saturating_sub(2048)", "arena::end().saturating_sub(2064)")
pager_text = pager_text.replace("""fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        arena::end().saturating_sub(2048)
    } else {
        arena::end()
    }
}""", """fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        arena::end().saturating_sub(2048)
    } else {
        arena::end().saturating_sub(1024) // isolated LCD callback stack
    }
}""")
pager_text = pager_text.replace("""fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).tlm_cap } != 0 {
        arena::tlm() as usize
    } else {
        arena::end()
    }
}""", """fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).tlm_cap } != 0 {
        arena::tlm() as usize
    } else {
        arena::end().saturating_sub(1040) // aligned 1 KiB LCD callback stack below globals
    }
}""")
pager_text = pager_text.replace("""fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        arena::end().saturating_sub(2048)
    } else {
        arena::end()
    }
}""", """fn scratch_end() -> usize {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        arena::end().saturating_sub(2064) // 2 KiB module stack below globals
    } else {
        arena::end().saturating_sub(1040) // aligned 1 KiB LCD callback stack below globals
    }
}""")
pager_text = pager_text.replace("""fn module_lcd_stack_top() -> *mut u8 {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        unsafe { core::ptr::addr_of_mut!(crate::MODULE_PAGE_BUFFER_STORAGE).cast::<u8>().add(1152) }
    } else { arena::end() as *mut u8 }
}""", """fn module_lcd_stack_top() -> *mut u8 {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        unsafe { core::ptr::addr_of_mut!(crate::MODULE_PAGE_BUFFER_STORAGE).cast::<u8>().add(1152) }
    } else { ((arena::end().saturating_sub(16)) & !7usize) as *mut u8 }
}""")
pager_text = pager_text.replace("""fn module_stack_top() -> *mut u8 {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        arena::end() as *mut u8
""", """fn module_stack_top() -> *mut u8 {
    if unsafe { (*addr_of!(MOD_CTX)).stack_from_arena } {
        ((arena::end().saturating_sub(16)) & !7usize) as *mut u8
""")
pager_path.write_text(pager_text)

# `lean` strips the voice decoder to save MCU flash, but the sideload host still
# needs the full shared RAM arena to load modules and provide their scratch space.
build_path = root.parent / "build.rs"
build_text = build_path.read_text()
old_arena_cfg = 'if feat("LEAN") || (feat("EXPLORE") && !feat("VOICE_EXPLORE")) {'
intermediate_arena_cfg = 'if (feat("LEAN") && !feat("BLOWFANG_HOST")) || (feat("EXPLORE") && !feat("VOICE_EXPLORE")) {'
new_arena_cfg = 'if !feat("BLOWFANG_HOST") && (feat("LEAN") || (feat("EXPLORE") && !feat("VOICE_EXPLORE"))) {'
if intermediate_arena_cfg in build_text:
    build_text = build_text.replace(intermediate_arena_cfg, new_arena_cfg, 1)
if new_arena_cfg not in build_text:
    if build_text.count(old_arena_cfg) != 1:
        raise SystemExit("build.rs: cannot find small_arena feature condition")
    build_text = build_text.replace(old_arena_cfg, new_arena_cfg, 1)
build_path.write_text(build_text)

# The full Aura loop owns the serial RX stream. Route Aura CPS and Blowfang cable
# handshakes through one detector so two independent pollers cannot consume each
# other's bytes.
cps_path = root / "cps/mod.rs"
cps_text = cps_path.read_text()
detector_anchor = "pub struct HandshakeDetector {\n"
host_detector = '''#[cfg(feature = "blowfang-host")]
pub enum HostHandshake { Aura, Cable }

#[cfg(feature = "blowfang-host")]
pub struct HostHandshakeDetector { tail: [u8; 16], len: usize }

#[cfg(feature = "blowfang-host")]
impl HostHandshakeDetector {
    pub const fn new() -> Self { Self { tail: [0; 16], len: 0 } }

    pub fn poll(&mut self) -> Option<HostHandshake> {
        while let Some(byte) = uart::take_byte() {
            if self.len < self.tail.len() {
                self.tail[self.len] = byte;
                self.len += 1;
            } else {
                self.tail.copy_within(1.., 0);
                self.tail[15] = byte;
            }
            if self.len >= HANDSHAKE.len()
                && self.tail[self.len - HANDSHAKE.len()..self.len] == *HANDSHAKE
            {
                self.len = 0;
                return Some(HostHandshake::Aura);
            }
            #[cfg(feature = "spilib")]
            if self.len >= CABLE_HANDSHAKE.len()
                && self.tail[self.len - CABLE_HANDSHAKE.len()..self.len] == *CABLE_HANDSHAKE
            {
                self.len = 0;
                return Some(HostHandshake::Cable);
            }
        }
        None
    }
}

'''
if "pub struct HostHandshakeDetector" not in cps_text:
    if cps_text.count(detector_anchor) != 1:
        raise SystemExit("cps/mod.rs: cannot find handshake detector insertion point")
    cps_text = cps_text.replace(detector_anchor, host_detector + detector_anchor, 1)
cps_path.write_text(cps_text)

main_text = main_path.read_text()
old_detector = "    let mut cps_handshake = cps::HandshakeDetector::new();"
new_detector = '''    #[cfg(not(feature = "blowfang-host"))]
    let mut cps_handshake = cps::HandshakeDetector::new();
    #[cfg(feature = "blowfang-host")]
    let mut host_handshake = cps::HostHandshakeDetector::new();'''
if new_detector not in main_text:
    if main_text.count(old_detector) < 1:
        raise SystemExit("main.rs: cannot find Aura CPS detector setup")
    pos = main_text.rfind(old_detector)
    main_text = main_text[:pos] + new_detector + main_text[pos + len(old_detector):]
old_poll = '''        if cps_handshake.poll() {
            cps::run_session(&mut app, &mut cp.SYST, &mut display, &mut dbg);
        }'''
new_poll = '''        #[cfg(not(feature = "blowfang-host"))]
        if cps_handshake.poll() {
            cps::run_session(&mut app, &mut cp.SYST, &mut display, &mut dbg);
        }
        #[cfg(feature = "blowfang-host")]
        match host_handshake.poll() {
            Some(cps::HostHandshake::Aura) =>
                cps::run_session(&mut app, &mut cp.SYST, &mut display, &mut dbg),
            Some(cps::HostHandshake::Cable) if !app.is_transmitting() =>
                cps::run_cable_session(&mut app, &mut cp.SYST, &mut display),
            _ => {}
        }'''
if new_poll not in main_text:
    if main_text.count(old_poll) < 1:
        raise SystemExit("main.rs: cannot find Aura CPS handshake poll")
    pos = main_text.rfind(old_poll)
    main_text = main_text[:pos] + new_poll + main_text[pos + len(old_poll):]
main_path.write_text(main_text)

# Keep the host image focused on VFO/channel radio operation, APRS beacons, and
# the sideload host. Other Aura apps remain available in the ordinary Aura build
# but their periodic service and dedicated TX paths are not linked here.
main_text = main_path.read_text()
for statement in (
    "        if app.sstv_tx_pending() {",
    "        if due.every_100ms && app.poll_wall_clock() {",
    "        app.poll_satellite_name_timeout();",
    "        app.poll_sstv_name_timeout();",
    "        app.poll_search(&mut cp.SYST);",
    "        app.poll_scanqt(&mut cp.SYST);",
    "        app.poll_fm(&mut cp.SYST);",
    "        app.poll_spectrum(&mut cp.SYST);",
):
    guarded = "        #[cfg(not(feature = \"blowfang-host\"))]\n" + statement
    if guarded not in main_text:
        if main_text.count(statement) != 1:
            raise SystemExit(f"main.rs: expected one host-prunable call: {statement.strip()}")
        main_text = main_text.replace(statement, guarded, 1)

start_marker = "        // PTT / CW key. CW bypasses the voice-PTT debouncer entirely"
end_marker = "        if due.ticks_10ms > 0 {"
if "// Host image PTT path: normal VFO/CW and Blowfang return only." not in main_text:
    start = main_text.find(start_marker)
    end = main_text.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("main.rs: cannot isolate the optional tracking/SSTV PTT path")
    original = main_text[start:end]
    host_ptt = '''        // Host image PTT path: normal VFO/CW and Blowfang return only.
        #[cfg(feature = "blowfang-host")]
        {
            if app.mode() == app::Mode::Pager {
                app.handle_pager_ptt(&mut cp.SYST);
            } else if matches!(
                app.side_modulation(app.master_index()),
                Modulation::Cw | Modulation::Cwf
            ) {
                app.poll_cw_key(&mut cp.SYST);
            } else if let Some(level) = app.radio_mut().poll_ptt() {
                app.set_ptt(&mut cp.SYST, !level);
            }
        }

'''
    main_text = main_text[:start] + '#[cfg(not(feature = "blowfang-host"))]\n        {\n' + original + '        }\n\n' + host_ptt + main_text[end:]
main_path.write_text(main_text)

# The slim host launcher intentionally keeps only the normal VFO, settings,
# channel manager, and Blowfang module host. Gate the other launcher variants as
# well as the table entries: otherwise their labels and dispatch arms remain in
# the host binary even though they are not selectable.
launcher_path = root / "app/launcher.rs"
launcher_text = launcher_path.read_text()
for variant in ("Contacts", "FmRadio", "ScanQt", "Search", "Spectrum", "Satellite", "Sstv"):
    enum_line = f"    {variant},\n"
    enum_guard = f"    #[cfg(not(feature = \"blowfang-host\"))]\n{enum_line}"
    if enum_guard not in launcher_text:
        if launcher_text.count(enum_line) < 1:
            raise SystemExit(f"launcher.rs: missing {variant} enum entry")
        launcher_text = launcher_text.replace(enum_line, enum_guard, 1)
    lines = launcher_text.splitlines(keepends=True)
    updated = []
    for line in lines:
        if line.startswith(f"            LauncherEntry::{variant} =>"):
            if not updated or 'cfg(not(feature = "blowfang-host"))' not in updated[-1]:
                updated.append('            #[cfg(not(feature = "blowfang-host"))]\n')
        updated.append(line)
    launcher_text = "".join(updated)
launcher_path.write_text(launcher_text)

keys_path = root / "app/keys.rs"
keys_text = keys_path.read_text()
for variant in ("Contacts", "FmRadio", "ScanQt", "Search", "Spectrum", "Satellite", "Sstv"):
    lines = keys_text.splitlines(keepends=True)
    updated = []
    for line in lines:
        if line.startswith(f"                        LauncherEntry::{variant} =>"):
            if not updated or 'cfg(not(feature = "blowfang-host"))' not in updated[-1]:
                updated.append('                        #[cfg(not(feature = "blowfang-host"))]\n')
        updated.append(line)
    keys_text = "".join(updated)
keys_path.write_text(keys_text)

print("Aura launcher now includes the Blowfang host menu")
