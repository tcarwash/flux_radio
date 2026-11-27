#!/usr/bin/env python3
"""Opt-in, synthetic-TX-only protected-RAM0 placement of shared live state.

Requires K6_SOTTO_SYNTH_HOST=1, K6_RAM0=1, K6_LIVE_SERIAL=1 and MIC=0.
No IRQ or codec code changed. Fail closed on any unmatched generated host.
"""
from pathlib import Path
import os
ROOT=Path(__file__).resolve().parent
if not (os.getenv('K6_SOTTO_SYNTH_HOST')=='1' and os.getenv('K6_RAM0')=='1'
        and os.getenv('K6_LIVE_SERIAL')=='1' and os.getenv('K6_SOTTO_MIC')=='0'):
    raise SystemExit('Synthetic RAM0 placement requires live serial, expanded RAM0 and no mic capture')
work=Path(os.environ.get('K6_RX4800_WORK',ROOT/'build/aura-fd6818b-port')).resolve()
insert={
    work/'src/app/pager.rs': 'static mut MOD_CTX: ModCtx = ModCtx {',
    work/'src/cps/live.rs': 'static mut CANCEL:CancelState=CancelState::empty();',
}
prefix='#[cfg_attr(k6_sotto_ram0_state, link_section=".ram0")]\n'
updates={}
for file,anchor in insert.items():
    s=file.read_text()
    if s.count(anchor)!=1:
        raise SystemExit(f'Cannot safely position synthetic control state in {file}')
    if prefix+anchor in s:
        updates[file]=s
    else:
        updates[file]=s.replace(anchor,prefix+anchor,1)
for file,s in updates.items():
    if file.read_text()!=s:file.write_text(s)
print('Placed bounded module context and cooperative cancellation state in protected RAM0')
