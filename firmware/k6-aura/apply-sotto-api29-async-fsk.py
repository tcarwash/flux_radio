#!/usr/bin/env python3
"""Append-only FSK async API29 host with PA-muted TX entry. Offline build only."""
from __future__ import annotations
import os
from pathlib import Path
here=Path(__file__).resolve().parent
work=Path(os.environ.get('K6_RX4800_WORK',here/'build/aura-fd6818b-port')).resolve()
pager=work/'src/app/pager.rs'
radio=work/'src/device/radio.rs'
s=pager.read_text()
if '// SOTTO_API28_RESOURCES_BEGIN' not in s or '// SOTTO_4800_RX_BEGIN' not in s:
    raise SystemExit('API29 requires already-applied API28+API31 overlays')
r=radio.read_text()
def change(src,old,new):
    if new in src:return src
    if src.count(old)!=1:
        raise SystemExit('API29 overlay anchor missing or ambiguous '+repr(old[:105]))
    return src.replace(old,new,1)

# Keep regular analog TX callers untouched. This is a separate PA-muted entry.
r=change(r,'    pub fn enter_tx(&mut self, syst: &mut SYST) -> bool {',
'''    pub fn enter_tx(&mut self, syst: &mut SYST) -> bool {
        self.enter_tx_with_pa(syst,true)
    }
    // Existing analog callers always pass true; only opt-in API29 uses false.
    pub fn enter_tx_with_pa(&mut self, syst: &mut SYST, pa:bool) -> bool {''')
# Modifies only the single enter_tx body, no other PA state transitions.
a=r.index('    pub fn enter_tx_with_pa(')
b=r.index('    /// Bare-carrier TX for CW/CWF keying:',a)
old='''        self.fd6818.pa_enable(syst, self.cfg.power);'''
if r[a:b].count(old)==1:
    r=r[:a]+r[a:b].replace(old,'''        if pa {self.fd6818.pa_enable(syst, self.cfg.power);}
        else {self.fd6818.pa_off(syst);}''')+r[b:]
elif "if pa {self.fd6818.pa_enable" not in r[a:b]:
    raise SystemExit("API29 radio PA-entry anchor missing or inconsistent")
# Clean version: make normal enter_tx_with_pa a separate helper that is invoked
# by the original entry and API29 explicit caller.
s=change(s,'    fsk_async: mod_sotto_async_unavailable,',
         '    fsk_async: mod_sotto_async_fsk,')
s=change(s,'unsafe extern "C" fn mod_tx_off() {\n',
         'unsafe extern "C" fn mod_tx_off() {\n'
         '    #[cfg(k6_sotto_async_fsk)]\n    unsafe{sotto_tx29_cleanup();}\n')
old='''        // RX-only API31 is not a full API28-30 voice transmitter host.
        // Reject new-tail modules' TX before any analog PA transition.
        if unsafe {*arena::base().add(12) >= 28} {return -1;}
        unsafe {rx4800_stop();}'''
new='''        // Only the explicitly enabled Data-pack API29 service may borrow TX.
        // Neither the old RX-only build nor ordinary KISS modules gain it.
        let new_tail = unsafe{*arena::base().add(12)>=28};
        if new_tail && (!cfg!(k6_sotto_async_fsk) || !matches!(unsafe{*arena::base().add(12)},30|31)) {
            return -1;
        }
        unsafe{rx4800_stop();}'''
s=change(s,old,new)
# Only change the module TX path, not ordinary UI or CW transmitters.
start=s.index('unsafe extern "C" fn mod_tx_on(max_ms: u32)')
finish=s.index('unsafe extern "C" fn mod_tx_off()',start)
section=s[start:finish]
old='    let ok = app.radio.enter_tx(syst);'
new='    // Native FSK modules enter with the PA muted until FIFO preload.\n    let digital=cfg!(k6_sotto_async_fsk) && unsafe{*arena::base().add(12)>=30};\n    let ok = app.radio.enter_tx_with_pa(syst,!digital);'
if section.count(old)==1:
    s=s[:start]+section.replace(old,new)+s[finish:]
elif 'app.radio.enter_tx_with_pa(syst,!digital)' not in section:
    raise SystemExit('module TX anchor missing or inconsistent')
# A never-cleared stale async state must not bleed into another module run.
s=change(s,'fn run_module(app: &mut App, syst: &mut SYST) {\n',
'''fn run_module(app: &mut App, syst: &mut SYST) {
    #[cfg(k6_sotto_async_fsk)]
    unsafe{SOTTO_TX29_ACTIVE=false;SOTTO_TX29_SNAPSHOT=false;}
''')
body=(here/'sotto-api29-async-fsk.rs.inc').read_text()
if '// SOTTO_API29_ASYNC_FSK_BEGIN' not in s:
    s+='\n'+body+'\n'
pager.write_text(s)
radio.write_text(r)
print('Applied opt-in API29 bounded PA-muted FSK async TX')
