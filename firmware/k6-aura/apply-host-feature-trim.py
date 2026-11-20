#!/usr/bin/env python3
"""Remove native Search and gate FM and Explore services on the normal host."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "build/aura-fd6818b-port/src"


def edit(path, transform):
    source = ROOT / path
    source.write_text(transform(source.read_text()))


def replace(text, old, new):
    if new and new in text:
        return text
    if text.count(old) != 1:
        raise SystemExit(f"Host feature trim: unexpected anchor {old!r}")
    return text.replace(old, new, 1)


def guard(text, line, condition=None):
    """Replace earlier host exclusions, including duplicates in existing worktrees."""
    pattern = r'(?m)^(?:[ \t]*#\[cfg\((?:not\(feature = "blowfang-host"\)|not\(feature = "lean"\)|not\(k6_no_scan\)|k6_no_scan|not\(k6_no_native_fm\))\)\]\n)*' + re.escape(line)
    updated, count = re.subn(pattern, (f"{line[:len(line) - len(line.lstrip())]}#[cfg({condition})]\n" if condition else "") + line, text)
    if count != 1:
        raise SystemExit(f"Host feature trim: unexpected guarded line {line!r}")
    return updated


def module_api(text):
    callbacks = [
        ("ex_init", "_state: Ptr, _entries: Ptr, _cap: u32", "", ""),
        ("ex_view", "_state: Ptr, _index: u32", " -> Ptr", "core::ptr::null_mut()"),
        ("ex_view_n", "_state: *const core::ffi::c_void", " -> u32", "0"),
        ("ex_tone", "_state: Ptr, _hz: u32, _tone: u16", "", ""),
        ("ex_cw", "_state: Ptr, _hz: u32, _call: *const u8", "", ""),
        ("xs_defaults", "_scan: *mut core::ffi::c_void", "", ""),
        ("xs_init", "_scan: *mut core::ffi::c_void, _api: *const super::ffi::K6Api, _candidates: *mut core::ffi::c_void, _mem: *mut u8, _bytes: u32", " -> i32", "-1"),
        ("xs_step", "_scan: *mut core::ffi::c_void", " -> i32", "-1"),
        ("xs_close", "_scan: *mut core::ffi::c_void", "", ""),
        ("xs_resume", "_scan: *mut core::ffi::c_void", "", ""),
        ("lcd_header", "_band: *const u8, _found: u32, _gaze: i32", "", ""),
        ("lcd_frame", "_frame: *const super::ModExploreFrame", "", ""),
        ("cw_init", "_state: *mut core::ffi::c_void", "", ""),
        ("cw_feed", "_state: *mut core::ffi::c_void, _ms: u32, _keyed: i32", " -> i32", "-128"),
        ("cw_flush", "_state: *mut core::ffi::c_void, _ms: u32", " -> i32", "-128"),
        ("cw_call", "_text: *const u8, _call: *mut u8, _flags: *mut u8", " -> i32", "-1"),
        ("tone_decode", "_r68: u16, _r69: u16, _r6a: u16", " -> u16", "0"),
        ("tone_start", "", " -> i32", "-1"),
        ("tone_step", "", " -> i32", "-1"),
        ("tone_stop", "", "", ""),
    ]
    stub_lines = ['#[cfg(k6_no_scan)]', 'mod unavailable_explore {', '    type Ptr = *mut core::ffi::c_void;']
    for name, args, result, body in callbacks:
        stub_lines.append(f'    pub(super) unsafe extern "C" fn {name}({args}){result} {{ {body} }}')
    stub_lines.append('}')
    stub_block = "\n".join(stub_lines)
    if "mod unavailable_explore {" not in text:
        text = replace(text, "static MOD_API: ffi::K6Api", stub_block + "\n\nstatic MOD_API: ffi::K6Api")
    else:
        text = re.sub(r'(?ms)^#\[cfg\(k6_no_scan\)\]\nmod unavailable_explore \{.*?^\}', stub_block, text, count=1)
    fields = {"ex_init": "ffi::ex_init", "ex_view": "ffi::ex_view", "ex_view_n": "ffi::ex_view_n",
              "ex_tone": "ffi::ex_tone", "ex_cw": "ffi::ex_cw",
              "xs_defaults": "ffi::kmx_xs_defaults", "xs_init": "ffi::kmx_xs_init",
              "xs_step": "ffi::kmx_xs_step", "xs_close": "ffi::kmx_xs_close",
              "xs_resume": "ffi::kmx_xs_resume", "lcd_header": "mod_lcd_explore_header",
              "lcd_frame": "mod_lcd_explore_frame",
              "cw_init": "ffi::km_cw_init", "cw_feed": "ffi::km_cw_feed",
              "cw_flush": "ffi::km_cw_flush", "cw_call": "ffi::km_cw_call",
              "tone_decode": "ffi::km_tone_decode", "tone_start": "mod_tone_start",
              "tone_step": "mod_tone_step", "tone_stop": "mod_tone_stop"}
    field_by_callback = {
        "ex_init": "explore_ex_init", "ex_view": "explore_ex_view",
        "ex_view_n": "explore_ex_view_n", "ex_tone": "explore_ex_tone",
        "ex_cw": "explore_ex_cw", "xs_defaults": "explore_xs_defaults",
        "xs_init": "explore_xs_init", "xs_step": "explore_xs_step",
        "xs_close": "explore_xs_close", "xs_resume": "explore_xs_resume",
        "lcd_header": "lcd_explore_header", "lcd_frame": "lcd_explore_frame",
        "cw_init": "cw_init", "cw_feed": "cw_feed", "cw_flush": "cw_flush",
        "cw_call": "cw_call", "tone_decode": "tone_decode",
        "tone_start": "tone_start", "tone_step": "tone_step", "tone_stop": "tone_stop",
    }
    text = guard(text, "    unsafe { mod_tone_stop() }; // restore the detector even if the module forgot", "not(k6_no_scan)")
    arq_reply = "                arq_owe(KM_ST_RAN, n, 0, free_ram().min(0xFFFF) as u16);"
    arq_reply_guard = '                #[cfg(not(feature = "lean"))]\n' + arq_reply
    if arq_reply_guard not in text:
        text = replace(text, arq_reply, arq_reply_guard)
    api_at = text.find("static MOD_API: ffi::K6Api")
    if api_at < 0:
        raise SystemExit("Host feature trim: missing MOD_API initializer")
    api_close = text.find("\n};", api_at)
    if api_close < 0:
        raise SystemExit("Host feature trim: unterminated MOD_API initializer")
    api_end = api_close + len("\n};")
    prefix, api_block, suffix = text[:api_at], text[api_at:api_end], text[api_end:]
    api_lines = api_block.splitlines(keepends=True)
    for name, real in fields.items():
        field = field_by_callback[name]
        stub = "unavailable_explore::" + name
        found = []
        kept = []
        for line in api_lines:
            if line.lstrip().startswith(field + ":"):
                found.append(line)
                if kept and kept[-1].strip().startswith("#[cfg("):
                    kept.pop()
                continue
            kept.append(line)
        if not found:
            raise SystemExit(f"Host feature trim: expected K6Api field {field}, got 0")
        first = next((i for i, line in enumerate(kept) if line.lstrip().startswith("action_chirp:")), len(kept)-1)
        block = [f'    #[cfg(not(k6_no_scan))]\n', f'    {field}: {real},\n',
                 '    #[cfg(k6_no_scan)]\n', f'    {field}: {stub},\n']
        api_lines = kept[:first] + block + kept[first:]
    seen = set()
    deduped = []
    for line in api_lines:
        match = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*): ', line)
        if match and match.group(1) not in set(field_by_callback.values()):
            if match.group(1) in seen:
                if deduped and deduped[-1].strip().startswith("#[cfg("):
                    deduped.pop()
                continue
            seen.add(match.group(1))
        deduped.append(line)
    api_lines = deduped
    api_block = ''.join(api_lines)
    text = prefix + api_block + suffix
    return text


def keys(text):
    text = replace(text, "use super::{pager, scan, scanqt, search, spectrum};", "use super::{pager, scan, scanqt, spectrum};\n#[cfg(not(feature = \"blowfang-host\"))]\nuse super::search;")
    for line in (
        "        Mode::Search => search::dispatch(app, syst, ev),",
        "                        LauncherEntry::Search => search::enter(app, syst),",
    ):
        text = guard(text, line, "not(feature = \"blowfang-host\")")
    for line in (
        "        Mode::Fm => fm::dispatch(app, syst, ev),",
        "                        LauncherEntry::FmRadio => fm::enter(app, syst),",
        "            if app.mode == Mode::Fm {",
        "    if app.mode == Mode::Fm && matches!(ev.key, KeyId::Side1 | KeyId::Side2 | KeyId::Band) {",
    ):
        text = guard(text, line, "not(k6_no_scan)")
    return text


def launcher(text):
    for indent, suffix in (
        ("            ", 'LauncherEntry::Search => "FREQ HUNT",'),
        ("            ", "LauncherEntry::Search => true,"),
        ("            ", "LauncherEntry::Search => Mode::Search,"),
    ):
        text = guard(text, indent + suffix, "not(feature = \"blowfang-host\")")
    for line in (
        "    FmRadio,",
        '            LauncherEntry::FmRadio => "FM RADIO",',
        "            LauncherEntry::FmRadio => true,",
        "            LauncherEntry::FmRadio => Mode::Fm,",
    ):
        text = guard(text, line, "not(k6_no_scan)")
    # Normalize the host item list after earlier patchers have run; legacy Search entries
    # must not reappear in an already-patched generated tree.
    host_array = re.compile(r'(?ms)^#\[cfg\(feature = "blowfang-host"\)\]\npub const LAUNCHER_ITEMS: &\[LauncherEntry\] = &\[.*?^\];')
    normalized = '''#[cfg(feature = "blowfang-host")]
pub const LAUNCHER_ITEMS: &[LauncherEntry] = &[
    LauncherEntry::Settings,
    LauncherEntry::ChannelMgr,
    #[cfg(not(k6_no_scan))]
    LauncherEntry::FmRadio,
    LauncherEntry::Blowfang,
];'''
    text, count = host_array.subn(normalized, text)
    if count != 1:
        raise SystemExit("Host feature trim: could not normalize host launcher items")
    text = text.replace('            #[cfg(feature = "blowfang-host")]\n            LauncherEntry::Blowfang => "BLOWFANG",\n', '')
    return text


def key_functions(text):
    text = replace(text, "use super::{fm, scan, search, App};", "use super::{fm, scan, App};\n#[cfg(not(feature = \"blowfang-host\"))]\nuse super::search;")
    text = guard(text, "        5 => KeyFunction::FmRadio,", "not(k6_no_scan)")
    text = guard(text, "        KeyFunction::FmRadio => fm::enter(app, syst),", "not(k6_no_scan)")
    for line in (
        '            KeyFunction::Search => "SEARCH",',
        "        9 => KeyFunction::Search,",
        "        KeyFunction::Search => search::enter(app, syst),",
        "    Search,",
    ):
        text = guard(text, line, 'not(feature = "blowfang-host")')
    text = replace(text, "        KeyFunction::None | KeyFunction::TxTone => {}", "        KeyFunction::None | KeyFunction::TxTone => {}\n        #[cfg(k6_no_scan)]\n        KeyFunction::FmRadio => {},")
    for line in (
        "    AprsBeacon,",
        '            KeyFunction::AprsBeacon => "APRS TX",',
        "        11 => KeyFunction::AprsBeacon,",
        "        KeyFunction::AprsBeacon => super::aprs_ops::send_beacon(app),",
    ):
        text = text.replace(line + "\n", "", 1)
    return text


def main_loop(text):
    text = guard(text, "        app.poll_search(&mut cp.SYST);", "not(feature = \"blowfang-host\")")
    text = guard(text, "        app.poll_fm(&mut cp.SYST);", "not(k6_no_scan)")
    return re.sub(r'        // APRS beacon TX: draw overlay before blocking transmission\n        if app\.aprs_beacon_active\(\) \{\n.*?^        \}\n\n', "", text, count=1, flags=re.M | re.S)


def calibration_debug_logs(text):
    diagnostics = (
        '    writeln!(dbg, "norflash cal block @0xf210: {:02x?}", cal_buf).ok();',
        '''        writeln!(
            dbg,
            "squelch cal @0xf0c0: th_in={:?} th_out={:?}",
            sql_cal.th_in, sql_cal.th_out
        )
        .ok();''',
    )
    for statement in diagnostics:
        indent = statement[:len(statement) - len(statement.lstrip())]
        guarded = indent + "#[cfg(debug_assertions)]\n" + statement
        if guarded in text:
            continue
        if text.count(statement) != 1:
            raise SystemExit("Host feature trim: expected one calibration diagnostic format")
        text = text.replace(statement, guarded, 1)
    return text


def search_app(text):
    for line in ("mod search;", "    search: search::SearchState,", "            search: search::SearchState::new(),",
                 "    pub fn poll_search(&mut self, syst: &mut SYST) {",
                 "    pub fn search_band_label(&self) -> &'static str {",
                 "    pub fn search_status(&self) -> SearchStatus {",
                 "    pub fn search_candidate_freq_hz(&self) -> u32 {",
                 "    pub fn search_tone(&self) -> Option<SubAudio> {"):
        text = guard(text, line, 'not(feature = "blowfang-host")')
    return text


def search_ui(text):
    for line in ("mod search;", "use search::draw_search;",
                 "        app::Mode::Search => draw_search(display.as_draw_target(), &*app),"):
        text = guard(text, line, 'not(feature = "blowfang-host")')
    return text


def native_storage(text):
    text = replace(text, "let sats = storage.load_satellites();", "let sats = [None; MAX_SATELLITES];")
    text = replace(text, "let has_sstv_image = storage.has_sstv_image();", "let has_sstv_image = false;")
    return text


def cps(text):
    text = re.sub(r'            frame::CMD_SSTV_ERASE => .*?^            frame::CMD_READ_FLASH_RAW', '            frame::CMD_READ_FLASH_RAW', text, count=1, flags=re.M | re.S)
    for fallback in ("None", "false"):
        text = re.sub(r'    \} else if \(SAT_CPS_BASE\.\.SAT_CPS_BASE.*?^    \} else \{\n        '+fallback+r'\n    \};', '    } else {\n        '+fallback+'\n    };', text, count=1, flags=re.M | re.S)
    return text


edit("app/pager.rs", module_api)
edit("ui/pager.rs", lambda s: guard(s, "    if !s.explore.is_null() {", "not(k6_no_scan)"))
edit("app/keys.rs", keys)
edit("app/launcher.rs", launcher)
edit("app/keyfn.rs", key_functions)
edit("ui/mod.rs", lambda s: guard(search_ui(s), "        app::Mode::Fm => draw_fm(display.as_draw_target(), &*app),", "not(k6_no_scan)"))
edit("app/settings_ops.rs", lambda s: s if "keyfn::step_assignment(cur, up)" in s else replace(
    s, "| SettingItem::BandLong => clamp_step(cur, up, 0, 12),",
    """| SettingItem::BandLong => {
            let next = clamp_step(cur, up, 0, 12);
            if next != 0 && keyfn::from_u8(next as u8) == keyfn::KeyFunction::None {
                clamp_step(next, up, 0, 12)
            } else {
                next
            }
        },""",
))
edit("main.rs", lambda s: calibration_debug_logs(main_loop(s)))
edit("app/mod.rs", lambda s: native_storage(search_app(s)))
edit("cps/mod.rs", cps)
edit("ui/standby.rs", lambda s: replace(s, "    if app.aprs_beacon_active() {\n        draw_aprs_beacon_overlay(lcd);\n    } else if app.tx_prohibited() {", "    if app.tx_prohibited() {"))
