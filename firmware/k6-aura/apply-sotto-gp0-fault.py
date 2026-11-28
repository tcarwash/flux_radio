#!/usr/bin/env python3
"""Opt-in, reversible K6 GP0 fault trace: no module changes or hardware I/O.

Patches only the generated Aura sources, is idempotent and fail-closed on
unrecognized layouts. K6_SOTTO_GP0_TRACE=0 restores the exact pre-overlay
source bytes (regardless of existing unrelated overlays).
"""
from __future__ import annotations
import os
from pathlib import Path
import re

HERE=Path(__file__).resolve().parent
WORK=Path(os.environ.get('K6_RX4800_WORK',HERE/'build/aura-fd6818b-port')).resolve()
ENABLED=os.environ.get('K6_SOTTO_GP0_TRACE','0')
if ENABLED not in ('0','1'):
    raise SystemExit('K6_SOTTO_GP0_TRACE must be 0 or 1')

PAGER=WORK/'src/app/pager.rs'
CPS=WORK/'src/cps/mod.rs'
GP0=WORK/'src/hal/gp0_capture.rs'
HELPER=(HERE/'sotto-gp0-fault.rs.inc').read_text().rstrip()+'\n'
APIS_ORIGINAL='''    resource_read: mod_sotto_resource_read,
    fsk_async: mod_sotto_async_fsk,
    mic_stream: mod_sotto_mic_stream,
'''
APIS='''    // GPF1_APIS_BEGIN
    #[cfg(k6_sotto_gp0_trace)]
    resource_read: mod_sotto_resource_read_traced,
    #[cfg(not(k6_sotto_gp0_trace))]
    resource_read: mod_sotto_resource_read,
    #[cfg(k6_sotto_gp0_trace)]
    fsk_async: mod_sotto_async_fsk_traced,
    #[cfg(not(k6_sotto_gp0_trace))]
    fsk_async: mod_sotto_async_fsk,
    #[cfg(k6_sotto_gp0_trace)]
    mic_stream: mod_sotto_mic_stream_traced,
    #[cfg(not(k6_sotto_gp0_trace))]
    mic_stream: mod_sotto_mic_stream,
    // GPF1_APIS_END
'''
PAGER_BEGIN='''    // GPF1_BEGIN_RUN_BEGIN
    #[cfg(k6_sotto_gp0_trace)]
    gp0_fault_trace_begin(st.serial, unsafe { arena::base().add(16) });
    // GPF1_BEGIN_RUN_END
'''
PAGER_FINISH='''    // GPF1_FINISH_RUN_BEGIN
    #[cfg(k6_sotto_gp0_trace)]
    gp0_fault_trace_finish(ret);
    // GPF1_FINISH_RUN_END
'''
CPS_READ='''            // GPF1_CABLE_READ_BEGIN
            // Cable-only, read-only diagnostic records; no radio/flash operation.
            #[cfg(k6_sotto_gp0_trace)]
            0x75 if f.addr == 0 && f.len == 0 => {
                let report=app.gp0_fault_trace_report();
                cable_send(0x75,0,&report);
            }
            #[cfg(k6_sotto_gp0_timing)]
            0x76 if f.addr == 0 && f.len == 0 => {
                let report=app.gp0_timing_report();
                cable_send(0x76,0,&report);
            }
            // GPF1_CABLE_READ_END
'''
GP0_READER='''// GPF1_RING_READER_BEGIN
// The IRQ is stopped by mic_stream(op1) before this pointer is inspected.
#[cfg(k6_sotto_gp0_trace)]
pub unsafe fn fault_ring_lost(ring:*mut PcmRing)->bool {
    !ring.is_null() &&
        unsafe{rdv(core::ptr::addr_of!((*ring).lost))!=0}
}
#[cfg(k6_sotto_gp0_timing)]
pub unsafe fn fault_ring_backlog(ring:*mut PcmRing)->(u8,u8){
    if ring.is_null(){return (0,0);}
    let p=unsafe{rdv(core::ptr::addr_of!((*ring).produced))};
    let c=unsafe{rdv(core::ptr::addr_of!((*ring).consumed))};
    let off=unsafe{rdv(core::ptr::addr_of!((*ring).offset))};
    (p.wrapping_sub(c),off)
}
// GPF1_RING_READER_END
'''

BLOCKS={
    PAGER:[
        ('// SOTTO_GP0_FAULT_TRACE_BEGIN','// SOTTO_GP0_FAULT_TRACE_END',''),
        ('    // GPF1_APIS_BEGIN','    // GPF1_APIS_END',APIS_ORIGINAL),
        ('    // GPF1_BEGIN_RUN_BEGIN','    // GPF1_BEGIN_RUN_END',''),
        ('    // GPF1_FINISH_RUN_BEGIN','    // GPF1_FINISH_RUN_END',''),
    ],
    CPS:[('            // GPF1_CABLE_READ_BEGIN',
          '            // GPF1_CABLE_READ_END','')],
    GP0:[('// GPF1_RING_READER_BEGIN','// GPF1_RING_READER_END','')],
}

def strip(source, blocks):
    for start,end,replacement in blocks:
        if source.count(start)!=source.count(end) or source.count(start)>1:
            raise SystemExit(f'Duplicated or unbalanced trace markers: {start}')
        if source.count(start):
            pattern=r'(?ms)^'+re.escape(start)+r'\n.*?^'+re.escape(end)+r'\n'
            source,n=re.subn(pattern,lambda _: replacement,source)
            if n!=1:raise SystemExit(f'Cannot safely remove marker {start}')
    if 'GPF1_' in source and any(t in source for t in ('GPF1_APIS','GPF1_CABLE_READ','GPF1_RING_READER','GPF1_BEGIN_RUN','GPF1_FINISH_RUN')):
        raise SystemExit('Unknown GP0 trace marker remains')
    return source

# API30 MIC is absent in a deliberately restricted synthetic PCM host.
# When GP0 tracing is off, skip its optional generated source entirely.
if ENABLED=='1' and not GP0.exists():
    raise SystemExit('GPF1 requires the GP0 microphone source')
files={p:strip(p.read_text(),blocks) for p,blocks in BLOCKS.items()
       if p!=GP0 or p.exists()}

def insert(source,anchor,addition,location='before'):
    if source.count(anchor)!=1:
        raise SystemExit('Missing or ambiguous GP0 fault trace anchor '+repr(anchor))
    if location=='before': return source.replace(anchor,addition+anchor,1)
    return source.replace(anchor,anchor+addition,1)

if ENABLED=='1':
    pg=files[PAGER]
    pg=insert(pg,'unsafe extern "C" fn mod_sotto_resource_unavailable',HELPER)
    pg=insert(pg,APIS_ORIGINAL,APIS) if False else pg
    if pg.count(APIS_ORIGINAL)!=1:
        raise SystemExit('Missing or duplicated API30 field triplet')
    pg=pg.replace(APIS_ORIGINAL,APIS,1)
    pg=insert(pg,'    let ret = unsafe { ffi::km_run_on_stack',PAGER_BEGIN)
    pg=insert(pg,'    unsafe { mod_tx_off() }; // a module that left the transmitter on\n',PAGER_FINISH,'after')
    files[PAGER]=pg

    cp=files[CPS]
    cp=insert(cp,'            frame::CMD_READ_FLASH_RAW if (f.addr as u32) < FLASH_CHIP_SIZE / FLASH_RAW_CHUNK => {\n',CPS_READ)
    files[CPS]=cp

    g=files[GP0]
    g=insert(g,'pub fn active()->bool',GP0_READER)
    files[GP0]=g

# All files verified and in memory before any modification.
for p,data in files.items():
    if p.read_text()!=data:
        p.write_text(data)
print('Enabled GP0 fault trace' if ENABLED=='1' else 'Disabled GP0 fault trace; source restored')
