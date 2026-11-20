#!/usr/bin/env python3
"""Share APRS text-input event handling without changing settings storage or behavior."""
from pathlib import Path
p=Path(__file__).parent/'build/aura-fd6818b-port/src/app/keys.rs'
s=p.read_text()
if 'fn aprs_text_event<' not in s:
    a=s.index('fn dispatch_aprs_call_input('); b=s.index('/// Solves the calibration equation',a)
    s=s[:a]+'''// Share the cursor/digit/exit logic. Result: 0 continue, 1 commit, 2 cancel.
#[inline(never)]
fn aprs_text_event<const N: usize>(edit: &mut super::name_edit::NameEdit<N>, ev: KeyEvent) -> u8 {
    if ev.kind != KeyEventKind::Single { return 0; }
    if let Some(digit) = digit_value(ev.key) { edit.press_digit(digit); return 0; }
    match ev.key {
        KeyId::Up => edit.move_cursor(true),
        KeyId::Down => edit.move_cursor(false),
        KeyId::Menu => { edit.finalize_pending(); return 1; }
        KeyId::Exit if edit.buf.iter().all(|&b| b==0) => return 2,
        KeyId::Exit => edit.backspace(),
        _ => {}
    }
    0
}
#[inline(never)]
fn commit_aprs_text(src: &[u8], dst: &mut [u8], upper: bool) {
    dst.fill(0);
    let max=dst.len().saturating_sub(1);
    for (d,&b) in dst[..max].iter_mut().zip(src.iter()) {
        if b==0 {break;}
        *d=if upper {b.to_ascii_uppercase()} else {b};
    }
}
fn dispatch_aprs_call_input(app: &mut App, _syst: &mut SYST, ev: KeyEvent) {
    let action=aprs_text_event(&mut app.settings_ui.aprs_call_edit,ev);
    if action==1 {commit_aprs_text(&app.settings_ui.aprs_call_edit.buf,&mut app.settings.aprs_callsign,true);app.save_settings();}
    if action!=0 {app.settings_ui.editing=false;}
}
fn dispatch_aprs_dev_name_input(app: &mut App, _syst: &mut SYST, ev: KeyEvent) {
    let action=aprs_text_event(&mut app.settings_ui.aprs_dev_name_edit,ev);
    if action==1 {commit_aprs_text(&app.settings_ui.aprs_dev_name_edit.buf,&mut app.settings.aprs_dev_name,false);app.save_settings();}
    if action!=0 {app.settings_ui.editing=false;}
}
fn dispatch_aprs_comment_input(app: &mut App, _syst: &mut SYST, ev: KeyEvent) {
    let action=aprs_text_event(&mut app.settings_ui.aprs_comment_edit,ev);
    if action==1 {commit_aprs_text(&app.settings_ui.aprs_comment_edit.buf,&mut app.settings.aprs_custom_comment,false);app.save_settings();}
    if action!=0 {app.settings_ui.editing=false;}
}

'''+s[b:];p.write_text(s)
