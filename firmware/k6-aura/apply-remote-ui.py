#!/usr/bin/env python3
"""Opt-in normal-UI key injection for live serial on the FD6818B host.

Runs after the pinned Aura overlays; refuses unknown source anchors.
The serial parser stays in firmware/k6-aura/live-serial.rs. This hook
only enqueues a validated navigation key, never drives keypad GPIO/PTT.
"""
import os
from pathlib import Path

root = Path(os.environ.get("K6_REMOTE_UI_SOURCE_ROOT", str(
    Path(__file__).parent / "build/aura-fd6818b-port/src")))
app_path = root / "app/mod.rs"
keypad_path = root / "device/keypad.rs"

def add_once(path: Path, anchor: str, insertion: str, marker: str):
    data = path.read_text()
    if marker in data:
        return
    if data.count(anchor) != 1:
        raise SystemExit(f"remote UI overlay anchor mismatch: {path}: {anchor[:90]!r}")
    path.write_text(data.replace(anchor, insertion + anchor, 1))

app_code = '''    /// A logical navigation gesture from the authenticated/local live cable.
    /// Deferred via the normal keypad queue: normal physical-input policy,
    /// UI state machine, and application behavior remain authoritative.
    #[cfg(k6_remote_ui)]
    pub fn live_virtual_key(&mut self, syst: &mut SYST, arg: u32) -> u8 {
        use crate::device::keypad::KeyId;
        if arg & !0x11fu32 != 0 { return 2; }
        let key = match arg & 0xff {
            0 => KeyId::Digit0, 1 => KeyId::Digit1,
            2 => KeyId::Digit2, 3 => KeyId::Digit3,
            4 => KeyId::Digit4, 5 => KeyId::Digit5,
            6 => KeyId::Digit6, 7 => KeyId::Digit7,
            8 => KeyId::Digit8, 9 => KeyId::Digit9,
            10 => KeyId::Asterisk, 11 => KeyId::Pound,
            12 => KeyId::Menu, 13 => KeyId::Exit,
            14 => KeyId::Up, 15 => KeyId::Down,
            16 => KeyId::Vm, 17 => KeyId::Ab,
            18 => KeyId::Band, 19 => KeyId::Side1,
            20 => KeyId::Side2, _ => return 2,
        };
        let long = (arg & 0x100) != 0;
        if self.is_transmitting() || self.cw_key_down()
            || self.power_save_is_asleep() || self.radio.ptt_asserted()
            || crate::board::power_switch_off(unsafe {
                &*kd32f328_pac::Gpioa::ptr()
            }) { return 1; }
        if !self.keypad.queue_virtual_key(syst, key, long) { return 1; }
        0
    }

'''
add_once(app_path, '    // key dispatch\n', app_code,
         "pub fn live_virtual_key(&mut self, syst:")

keypad_code = '''    /// Queue one synthetic *logical* short/long gesture, not GPIO levels.
    /// Reject concurrent physical presses and avoid Press/Release events
    /// (programmable side-key TX-tone functions must not be synthesized).
    #[cfg(k6_remote_ui)]
    pub fn queue_virtual_key(&mut self, syst: &mut SYST, key: KeyId, long: bool) -> bool {
        if self.stable.is_some() || self.scan(syst).is_some()
            || (long && !key.supports_long()) { return false; }
        self.queue.push(KeyEvent {
            key,
            kind: if long { KeyEventKind::Long } else { KeyEventKind::Single },
        });
        true
    }

'''
add_once(keypad_path, '    pub fn pop_event(&mut self) -> Option<KeyEvent> {\n',
         keypad_code, "pub fn queue_virtual_key(&mut self, syst:")

print("Applied optional Flux live serial UI-key queue")
