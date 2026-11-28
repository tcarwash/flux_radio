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
# API29 modules in this deliberately mic-free synthetic test profile use
# the same PA-muted FIFO path as API30. The normal firmware's API30-only
# admission and analog TX behavior remain byte-for-byte unchanged.
# Check both exact guards before writing *any* modified generated file.
pager=work/'src/app/pager.rs'
s=updates[pager]
guards=(
 ('if new_tail && (!cfg!(k6_sotto_async_fsk) || !matches!(unsafe{*arena::base().add(12)},30|31)) {',
  'if new_tail && (!cfg!(k6_sotto_async_fsk) || !matches!(unsafe{*arena::base().add(12)},29|30|31)) {'),
 ('let digital=cfg!(k6_sotto_async_fsk) && unsafe{*arena::base().add(12)>=30};',
  'let digital=cfg!(k6_sotto_async_fsk) && unsafe{*arena::base().add(12)>=29};'),
)
for before,after in guards:
    if s.count(before)!=1 or s.count(after)!=0:
        raise SystemExit('Synthetic-only API29 FSK admission anchor missing or ambiguous')
    s=s.replace(before,after,1)
updates[pager]=s
for file,s in updates.items():
    if file.read_text()!=s:file.write_text(s)
print('Placed bounded live state in RAM0 and allowed synthetic API29 FSK with PA muted')
