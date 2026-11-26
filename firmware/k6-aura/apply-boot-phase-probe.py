#!/usr/bin/env python3
"""Opt-in splash-startup phase tracer for the K6, with zero flash writes.

Call after all regular generated Aura overlays, before compilation. It adds
three human-visible phase screens around the receive transition and the
first main-loop iteration. Source development only: does not open a radio.
"""
import os
from pathlib import Path
root=Path(__file__).resolve().parent
work=Path(os.environ.get('K6_BOOT_PHASE_WORK',root/'build/aura-fd6818b-port'))
main=work/'src/main.rs';boot=work/'src/ui/boot.rs';uart=work/'src/hal/uart.rs'
if not main.is_file() or not boot.is_file() or not uart.is_file():
 raise SystemExit(f'boot probe needs generated sources in {work}')
s=main.read_text();b=boot.read_text();u=uart.read_text()
function='''
// BOOT_PHASE_PROBE_BEGIN
#[cfg(k6_boot_phase_probe)]
pub fn draw_phase_probe<D>(lcd: &mut D, title: &str, line: &str)
where D: DrawTarget<Color = BinaryColor>, {
    draw_headlines(lcd, title, line);
}
// BOOT_PHASE_PROBE_END
'''
if 'BOOT_PHASE_PROBE_BEGIN' not in b:
 b+='\n'+function
boot.write_text(b)
# All three messages use the actual normal screen! macro and draw only to LCD.
# They are NOT copied into the release build unless the opt-in cfg is enabled.
old='''    app.radio_mut().enter_rx(&mut cp.SYST);'''
new='''    #[cfg(k6_boot_phase_probe)]
    screen!(display, |lcd| ui::boot::draw_phase_probe(lcd, "PHASE 1", "RX NEXT"));
    app.radio_mut().enter_rx(&mut cp.SYST);
    #[cfg(k6_boot_phase_probe)]
    screen!(display, |lcd| ui::boot::draw_phase_probe(lcd, "PHASE 2", "RX READY"));'''
if '"PHASE 1"' not in s:
 if s.count(old)!=1:raise SystemExit(f'normal RX entry expected once, got {s.count(old)}')
 s=s.replace(old,new,1)
# The normal main loop first runs only after app construction and the RX
# transition. A one-time progress marker is cleared by the regular UI redraw.
old='''    loop {
        let due = scheduler.tick();'''
new='''    #[cfg(k6_boot_phase_probe)]
    let mut probe_main_first_iteration = true;
    loop {
        #[cfg(k6_boot_phase_probe)]
        if probe_main_first_iteration {
            screen!(display, |lcd| ui::boot::draw_phase_probe(lcd, "PHASE 3", "MAIN LOOP"));
            probe_main_first_iteration = false;
        }
        let due = scheduler.tick();'''
if '"PHASE 3"' not in s:
 if s.count(old)!=1:raise SystemExit(f'normal scheduler loop expected once, got {s.count(old)}')
 s=s.replace(old,new,1)
# Instrument the 50ms VFO refresh to show live UART evidence. This is
# opt-in only, and never runs in ordinary firmware builds.
old = '            app.poll_blink();\n            draw_ui_on_arena(&mut display, &mut app);'
new = old + '''
            #[cfg(k6_boot_phase_probe)]
            {
                let (irq, rx) = uart::phase_uart_counts();
                screen!(display, |lcd| ui::boot::draw_phase_probe(lcd,
                    if irq != 0 { "IRQ YES" } else { "IRQ NO" },
                    if rx != 0 { "RX YES" } else { "RX NO" }));
            }'''
if '"IRQ YES"' not in s:
 if s.count(old) != 1:raise SystemExit(f'VFO refresh expected once, got {s.count(old)}')
 s=s.replace(old,new,1)
main.write_text(s)
# Probe counters are stored in ordinary BSS (not protected RAM0). Only the
# USART ISR writes; the UI reads through volatile loads.
if 'UART_PHASE_PROBE_BEGIN' not in u:
 u += '''
// UART_PHASE_PROBE_BEGIN
#[cfg(k6_boot_phase_probe)]
#[link_section = ".ram0"]
static mut UART_PHASE_ISR: u32 = 0;
#[cfg(k6_boot_phase_probe)]
#[link_section = ".ram0"]
static mut UART_PHASE_RX: u32 = 0;
#[cfg(k6_boot_phase_probe)]
pub fn phase_uart_counts() -> (u32, u32) {
    unsafe {
        (core::ptr::read_volatile(&raw const UART_PHASE_ISR),
         core::ptr::read_volatile(&raw const UART_PHASE_RX))
    }
}
// UART_PHASE_PROBE_END'''
needle='pub unsafe extern "C" fn USART1() {\n    let regs = &*kd32f328_pac::Usart1::ptr();'
replacement='pub unsafe extern "C" fn USART1() {\n    #[cfg(k6_boot_phase_probe)]\n    core::ptr::write_volatile(&raw mut UART_PHASE_ISR, core::ptr::read_volatile(&raw const UART_PHASE_ISR).wrapping_add(1));\n    let regs = &*kd32f328_pac::Usart1::ptr();'
if 'UART_PHASE_ISR).wrapping_add(1)' not in u:
 if u.count(needle)!=1:raise SystemExit(f'USART ISR anchor expected once, got {u.count(needle)}')
 u=u.replace(needle,replacement,1)
needle='    if isr.rxne().bit_is_set() {\n        let byte = regs.rdr().read().rdr().bits() as u8;'
replacement='    if isr.rxne().bit_is_set() {\n        #[cfg(k6_boot_phase_probe)]\n        core::ptr::write_volatile(&raw mut UART_PHASE_RX, core::ptr::read_volatile(&raw const UART_PHASE_RX).wrapping_add(1));\n        let byte = regs.rdr().read().rdr().bits() as u8;'
if 'UART_PHASE_RX).wrapping_add(1)' not in u:
 if u.count(needle)!=1:raise SystemExit(f'USART RX anchor expected once, got {u.count(needle)}')
 u=u.replace(needle,replacement,1)
uart.write_text(u)
# Never silently ship a phase build whose final overlay left any stage out.
for label in ('PHASE 1', 'PHASE 2', 'PHASE 3'):
 if s.count(f'\"{label}\"') != 1:
  raise SystemExit(f'boot diagnostic missing/duplicated {label} after overlays')
if b.count('BOOT_PHASE_PROBE_BEGIN') != 1:
 raise SystemExit('boot diagnostic function missing/duplicated after overlays')
if s.count('"IRQ YES"')!=1 or s.count('"RX YES"')!=1 or u.count('UART_PHASE_PROBE_BEGIN')!=1:
 raise SystemExit('UART probe markers missing/duplicated after overlays')
print('Opt-in K6 boot and UART phase probe verified:',work)
