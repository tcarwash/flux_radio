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
main=work/'src/main.rs';boot=work/'src/ui/boot.rs'
if not main.is_file() or not boot.is_file():
 raise SystemExit(f'boot probe needs generated sources in {work}')
s=main.read_text();b=boot.read_text()
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
main.write_text(s)
# Never silently ship a phase build whose final overlay left any stage out.
for label in ('PHASE 1', 'PHASE 2', 'PHASE 3'):
 if s.count(f'\"{label}\"') != 1:
  raise SystemExit(f'boot diagnostic missing/duplicated {label} after overlays')
if b.count('BOOT_PHASE_PROBE_BEGIN') != 1:
 raise SystemExit('boot diagnostic function missing/duplicated after overlays')
print('Opt-in K6 boot phase probe verified in generated sources:',work)
