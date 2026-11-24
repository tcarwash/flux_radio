#!/usr/bin/env python3
"""Opt-in API30 PA11 PCM microphone service; keeps API26-31 offsets intact."""
from pathlib import Path
import os

here=Path(__file__).resolve().parent
work=Path(os.environ.get('K6_RX4800_WORK',here/'build/aura-fd6818b-port')).resolve()
src=work/'src'
pager=src/'app/pager.rs'
txdog=src/'hal/txdog.rs'
hal=src/'hal/mod.rs'
s=pager.read_text()
if '// SOTTO_API29_ASYNC_FSK_BEGIN' not in s or '// SOTTO_API28_RESOURCES_BEGIN' not in s:
    raise SystemExit('Sotto API30 requires enabled API28+29+31 overlays')
def sub_once(s,old,new):
    if new in s:return s
    if s.count(old)!=1:raise SystemExit('Sotto API30 overlay anchor changed '+repr(old[:90]))
    return s.replace(old,new,1)
s=sub_once(s,'    mic_stream: mod_sotto_mic_unavailable,',
           '    mic_stream: mod_sotto_mic_stream,')
s=sub_once(s,'    unsafe{sotto_tx29_cleanup();}\n',
           '    unsafe{sotto_tx29_cleanup();}\n'
           '    #[cfg(k6_sotto_mic)]\n'
           '    unsafe{sotto_mic_stop();}\n')
s=sub_once(s,'fn run_module(app: &mut App, syst: &mut SYST) {\n',
           'fn run_module(app: &mut App, syst: &mut SYST) {\n'
           '    #[cfg(k6_sotto_mic)]\n'
           '    unsafe{SOTTO_MIC_ROUTE=false;}\n')
body=(here/'sotto-api30-mic.rs.inc').read_text()
if '// SOTTO_API30_MIC_BEGIN' not in s:s+='\n'+body+'\n'
pager.write_text(s)
mods=hal.read_text()
if 'pub mod pcm_capture;' not in mods:
    mods+='\n#[cfg(k6_sotto_mic)]\npub mod pcm_capture;\n'
hal.write_text(mods)
capture=src/'hal/pcm_capture.rs'
capture.write_text((here/'sotto-api30-pcm-capture.rs').read_text())
t=txdog.read_text()
anchor='pub fn unkeyed() {\n    KEYED_AT.store'
t=sub_once(t,anchor,'pub fn unkeyed() {\n'
    '    #[cfg(k6_sotto_mic)]\n'
    '    super::pcm_capture::stop();\n'
    '    KEYED_AT.store')
if '// SOTTO_API30_PCM_VECTOR_BEGIN' not in t:
    t+='''
// SOTTO_API30_PCM_VECTOR_BEGIN
// TIM17 interrupt vector is only borrowed while TX watchdog remaps SRAM.
// Timer shutdown MUST restore the bootloader vector before txdog::unkeyed.
#[cfg(k6_sotto_mic)]
pub fn pcm_vector(on:bool,handler:u32)->bool{
    let a=SRAM0+4*(16+22);
    if on {
        if !LIVE.load(Ordering::Relaxed)
           || ARMED.load(Ordering::Relaxed)&ARM_TX==0
           || rd(SYSCFG_CFGR1)&3!=3
           || rd(a)!=rd(BOOT_VEC+4*(16+22)) {return false;}
        wr(a,handler);
        if rd(4*(16+22))!=handler {
            wr(a,rd(BOOT_VEC+4*(16+22)));
            return false;
        }
        true
    } else {
        wr(a,rd(BOOT_VEC+4*(16+22)));
        true
    }
}
// SOTTO_API30_PCM_VECTOR_END
'''
txdog.write_text(t)
print('Applied experimental API30 TIM17/PA11 microphone ISR service')
