#!/usr/bin/env python3
"""Read a completed GP0 fault trace using cable-only command 0x75.

GPF1 data is boot-local RAM, not SPI flash. Reconnect the programming cable
after the attended PTT test without power cycling. The query never keys RF
or writes anything; ending a cable session reboots the receiver.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct

MAGIC=b'GPF1'
COMMAND=0x75
REPORT_SIZE=44

def decode(raw:bytes)->dict:
    if len(raw)!=REPORT_SIZE or raw[:4]!=MAGIC:
        raise ValueError('not a complete 44-byte GPF1 diagnostic')
    serial,ret,flags,res_errors,fsk_errors,fsk_last,gp0_error,irq_count,irq_peak,missed=struct.unpack_from('<IiI2Ii4I',raw,4)
    if flags & ~0x3f:
        raise ValueError('reserved GPF1 flag bits set')
    valid=bool(flags&1)
    if not valid and any((serial,ret,flags,res_errors,fsk_errors,fsk_last,gp0_error,irq_count,irq_peak,missed)):
        raise ValueError('invalid GPF1 report has nonzero data')
    if bool(flags&8)!=(res_errors!=0) or bool(flags&16)!=(fsk_errors!=0) or bool(flags&32)!=(valid and ret<0):
        raise ValueError('GPF1 status flags disagree with counters')
    if not fsk_errors and fsk_last:
        raise ValueError('GPF1 last FSK error without an error')
    problems=[]
    if flags&4 or gp0_error or missed:
        problems.append('pcm_dma')
    if res_errors:
        problems.append('codec_resource')
    if fsk_errors:
        problems.append('fsk_async')
    if valid and ret<0 and not problems:
        problems.append('module_or_engine_unknown')
    return {
        'version':'GPF1','valid':valid,'module_serial':serial,'module_return':ret,
        'gp0_capture_started':bool(flags&2),'pcm_lost':bool(flags&4),
        'resource_errors':res_errors,'fsk_errors':fsk_errors,
        'last_fsk_error':fsk_last,'gp0_dma_error':gp0_error,
        'gp0_irq_count':irq_count,'gp0_peak_isr_ticks':irq_peak,
        'gp0_missed_halves':missed,'failure_classes':problems,
        'saturating_16bit_counters':True,
        'evidence_scope':'firmware callback and DMA error counters; codec-internal failures can remain unclassified',
        'read_only':True,'boot_local':True,
    }

def run(port:str)->dict:
    from k6_install import Cable,CABLE_HANDSHAKE,open_port
    from k6_port_lock import serial_lock
    from types import SimpleNamespace
    with serial_lock(port):
        with open_port(SimpleNamespace(port=port)) as device:
            c=Cable(device,print)
            if not c.handshake(CABLE_HANDSHAKE,wait=10):
                raise RuntimeError('radio not responding to cable protocol; no result')
            try:
                info=c.info()
                if info.get('protocol')!=5 or info.get('arena')!=14976:
                    raise RuntimeError('unexpected firmware or arena; refusing trace read')
                return decode(c.call(COMMAND))
            finally:
                c.end()

def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--ack-reboot',action='store_true',help='required: cable session restarts receiver after readout')
    p.add_argument('--save',type=Path,help='write JSON to a NEW local file, never overwrite')
    args=p.parse_args()
    if not args.ack_reboot:
        p.error('--ack-reboot is required; cable readout restarts the radio')
    report=run(args.port)
    out=json.dumps(report,indent=2,sort_keys=True)+'\n'
    if args.save:
        with args.save.open('x') as file:
            file.write(out)
    print(out,end='')
    if not report['valid']:
        p.exit(2,'No completed GP0 run in current boot; no diagnosis available\n')

if __name__=='__main__':
    main()
