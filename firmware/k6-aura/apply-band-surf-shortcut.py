#!/usr/bin/env python3
"""Install the shared host module dispatcher and route BAND to SURF."""
from pathlib import Path

root = Path(__file__).parent / "build/aura-fd6818b-port/src"
keys = root / "app/keys.rs"
text = keys.read_text()
import re

pattern = re.compile(r'(?ms)^([ \t]*)KeyId::Band => \{\n.*?^\1\}')
replacement = r"""\1KeyId::Band => {
                    #[cfg(all(feature = "blowfang-host", not(k6_no_scan)))]
                    { pager::host_dispatch_module(app, b"SURF"); }
                    #[cfg(all(feature = "blowfang-host", k6_no_scan))]
                    scan::enter(app, syst);
                    #[cfg(not(feature = "blowfang-host"))]
                    {
                        let func = keyfn::from_u8(short_function(app, ev.key));
                        keyfn::invoke(app, syst, func);
                    }
                }"""
# Fresh Aura has a combined short-side/BAND arm. Split only the standby
# short-press arm, preserving the long press and other modes.
combined = """                KeyId::Side1 | KeyId::Side2 | KeyId::Band => {
                    let func = keyfn::from_u8(short_function(app, ev.key));
                    keyfn::invoke(app, syst, func);
                }"""
if not pattern.search(text) and text.count(combined) == 1:
    band = combined.replace("KeyId::Side1 | KeyId::Side2 | KeyId::Band", "KeyId::Band")
    text = text.replace(combined, band + "\n" + combined.replace(" | KeyId::Band", ""), 1)
text2, count = pattern.subn(replacement, text, count=1)
if count != 1:
    raise SystemExit("keys.rs: cannot locate BAND shortcut block")
keys.write_text(text2)

pager = root / "app/pager.rs"
text = pager.read_text()
anchor = '''/// Enter Aura's sideloaded-module picker from the normal launcher.
#[cfg(feature = "blowfang-host")]
pub(super) fn host_enter(app: &mut App) {
'''
insert = '''/// Resolve an installed executable module by its exact catalog name and queue it.
/// All execution goes through poll_library_queue/lib_run (including signature checks).
#[cfg(all(feature = "blowfang-host", feature = "spilib"))]
#[inline(never)]
pub(super) fn host_dispatch_module(app: &mut App, name: &[u8]) -> bool {
    if name.is_empty() || name.len() > 12 || app.pager.play_pending || stream_on() {
        return false;
    }
    unsafe { CB_NOR = &mut app.storage.norflash as *mut _; }
    for n in 0..KL_SLOTS {
        let mut info = ffi::KlInfo::default();
        if unsafe { ffi::kl_info(n, &LIB_FL, &mut info) } == 0 && info.flags == 1 {
            let len = info.name.iter().position(|&c| c == 0).unwrap_or(info.name.len());
            if &info.name[..len] == name {
                return host_dispatch_slot(n as u8, false);
            }
        }
    }
    false
}

/// Queue an executable catalog slot, with an explicit return destination.
/// The loader, not the caller, decides whether the image is safe to execute.
#[cfg(all(feature = "blowfang-host", feature = "spilib"))]
#[inline(never)]
fn host_dispatch_slot(slot: u8, reopen: bool) -> bool {
    unsafe {
        if core::ptr::read_volatile(core::ptr::addr_of!(PENDING_LIB_SLOT)) != 255 {
            return false;
        }
        core::ptr::write_volatile(core::ptr::addr_of_mut!(PENDING_MENU_OPEN), false);
        core::ptr::write_volatile(core::ptr::addr_of_mut!(PENDING_HOST_MENU_AFTER_LIB), reopen);
    }
    queue_lib_run(slot);
    true
}

'''
# Replace either the old generated shortcut or our own previous output.
legacy = '/// Run the installed module named SURF from the Aura standby BAND key.'
current = '/// Resolve an installed executable module by its exact catalog name and queue it.'
if anchor not in text:
    raise SystemExit("pager.rs: cannot locate host module picker")
for marker in (legacy, current):
    if marker in text:
        begin = text.index(marker)
        end = text.index(anchor, begin)
        text = text[:begin] + text[end:]
text = text.replace(anchor, insert + anchor, 1)

# Catalog selections use the very same queue helper, with the original return policy.
old_select = '                    queue_lib_run(m.slots[(sel - MENU_CORE) as usize]);'
new_select = '                    let _ = host_dispatch_slot(m.slots[(sel - MENU_CORE) as usize], true);'
# The non-explore host branch has exactly one selection; leave legacy Explore alone.
host_start = text.index('                #[cfg(all(feature = "blowfang-host", not(feature = "explore")))]\n                if sel == 0 {')
host_end = text.index('                }', text.index('                } else {', host_start)+len('                } else {'))
section = text[host_start:host_end]
if old_select in section:
    text = text[:host_start] + section.replace(old_select, new_select, 1) + text[host_end:]
elif new_select not in section:
    raise SystemExit("pager.rs: cannot locate host catalog selection")

# Host key shortcut runs should return to Aura; the regular module picker should reopen.
old_poll = '''        lib_run(app, syst, slot);
        #[cfg(feature = "blowfang-host")]
        menu_open(app);
'''
new_poll = '''        lib_run(app, syst, slot);
        #[cfg(feature = "blowfang-host")]
        {
            let reopen = unsafe { core::ptr::read_volatile(core::ptr::addr_of!(PENDING_HOST_MENU_AFTER_LIB)) };
            unsafe { core::ptr::write_volatile(core::ptr::addr_of_mut!(PENDING_HOST_MENU_AFTER_LIB), true) };
            if reopen && app.mode == Mode::Pager { menu_open(app); }
        }
'''
if old_poll in text:
    text = text.replace(old_poll, new_poll, 1)
elif new_poll.replace("if reopen && app.mode == Mode::Pager", "if reopen") in text:
    text = text.replace(new_poll.replace("if reopen && app.mode == Mode::Pager", "if reopen"), new_poll, 1)
elif new_poll not in text and not ("LIVE_JOB" in text and new_poll.split("\n", 1)[1] in text):
    raise SystemExit("pager.rs: cannot locate module-queue completion")

state = '''#[cfg(feature = "spilib")]
static mut PENDING_MENU_OPEN: bool = false;
'''
state_new = '''#[cfg(feature = "blowfang-host")]
static mut PENDING_HOST_MENU_AFTER_LIB: bool = true;
''' + state
if state in text and "static mut PENDING_HOST_MENU_AFTER_LIB" not in text:
    text = text.replace(state, state_new, 1)

pager.write_text(text)
print("Applied shared host module dispatcher and BAND-to-SURF shortcut")
