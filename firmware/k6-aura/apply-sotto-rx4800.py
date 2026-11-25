#!/usr/bin/env python3
"""Append experimental API31 receiver to a main-compatible API27 generated host.

No level-24 changes. API28-30 are explicit unavailable stubs in this
RX-only research host. Requires K6_MODE_REGISTRY=K6_PACKET_MAILBOX=1.
Does not flash or open a radio.
"""
from __future__ import annotations

import os
from pathlib import Path

root=Path(__file__).resolve().parent
work=Path(os.environ.get("K6_RX4800_WORK",
                         root/"build/aura-fd6818b-port")).resolve()
pager=work/"src/app/pager.rs"
s=pager.read_text()

def insert_once(old,new):
    global s
    if new in s:return
    if s.count(old)!=1:
        raise SystemExit(f"RX4800 overlay: expected unique anchor {old[:80]!r}, found {s.count(old)}")
    s=s.replace(old,new,1)

fields='''        pub resource_open: unsafe extern "C" fn(u32) -> i32,
        pub resource_read: unsafe extern "C" fn(u32,u32,*mut u8,u32) -> i32,
        pub fsk_async: unsafe extern "C" fn(u32,*const u16,u32) -> i32,
        pub mic_stream: unsafe extern "C" fn(u32,*mut u8,u32) -> i32,
        pub fsk4800_rx: unsafe extern "C" fn(u32,*mut u16,u32) -> i32,'''
initializers='''    resource_open: mod_sotto_resource_unavailable,
    resource_read: mod_sotto_read_unavailable,
    fsk_async: mod_sotto_async_unavailable,
    mic_stream: mod_sotto_mic_unavailable,
    fsk4800_rx: mod_fsk4800_rx,'''
insert_once('''        pub publish_rx: unsafe extern "C" fn(u16, *const u8, u16) -> i32,''',
            '''        pub publish_rx: unsafe extern "C" fn(u16, *const u8, u16) -> i32,
'''+fields)
insert_once('''    publish_rx: mod_mb_publish_rx,''',
            '''    publish_rx: mod_mb_publish_rx,
'''+initializers)
insert_once('level: if cfg!(k6_packet_mailbox) { 27 }',
            'level: if cfg!(k6_fsk4800_rx) { 31 } else if cfg!(k6_sotto_mic) { 30 } else if cfg!(k6_sotto_async_fsk) { 29 } else if cfg!(k6_sotto_resources) { 28 } else if cfg!(k6_packet_mailbox) { 27 }')
# Preserve the only TX-less invariant even if a module forgets to stop RX:
# always restore RX registers on module return or before TX switch.
insert_once('unsafe extern "C" fn mod_tx_off() {',
            'unsafe extern "C" fn mod_tx_off() {\n    #[cfg(k6_fsk4800_rx)]\n    unsafe { rx4800_stop(); }')
insert_once('    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);',
            '''    #[cfg(k6_fsk4800_rx)]
    {
        // RX-only API31 is not a full API28-30 voice transmitter host.
        // Reject new-tail modules' TX before any analog PA transition.
        if unsafe {*arena::base().add(12) >= 28} {return -1;}
        unsafe {rx4800_stop();}
    }
    let ms = max_ms.clamp(1, MOD_TX_MAX_MS);''')
body=(root/"sotto-fsk4800-rx.rs.inc").read_text()
if "// SOTTO_4800_RX_BEGIN" not in s:
    s+="\n"+body+"\n"
pager.write_text(s)
print(f"Applied isolated API31 RX4800 overlay: {pager}")
