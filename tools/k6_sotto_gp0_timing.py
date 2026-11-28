#!/usr/bin/env python3
"""Retrieve BOTH boot-local GPF1 (fault) and GPT1 (timing) from K6.

Uses one cable session and reboots only after both reads. Do not call a
status command first after physical PTT: this RAM-only evidence is lost on
a power cycle or cable-session reboot. Commands 0x75/0x76 are read-only.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import struct

from k6_sotto_gp0_fault import decode as decode_fault

TIMING_COMMAND=0x76
TIMING_MAGIC=b'GPT1'
TIMING_SIZE=40
CAUSES={
    0:'none_or_unclassified',
    1:'decimator_rejected_input',
    2:'pcm_queue_full',
    3:'pcm_offset_invalid',
    4:'pcm_already_lost_or_other',
}

def decode_timing(raw:bytes, fault:dict)->dict:
    if len(raw)!=TIMING_SIZE or raw[:4]!=TIMING_MAGIC:
        raise ValueError('not a complete 40-byte GPT1 timing record')
    serial,flags,reads,peak,total,preload_peak,preloads,cause,packed=struct.unpack_from('<9I',raw,4)
    if flags&~1:
        raise ValueError('GPT1 reserved flag bits set')
    valid=bool(flags&1)
    if valid!=fault['valid'] or serial!=fault['module_serial']:
        raise ValueError('GPT1 and GPF1 refer to different module runs')
    if not valid and any((serial,reads,peak,total,preload_peak,preloads,cause,packed)):
        raise ValueError('invalid GPT1 timing record has nonzero fields')
    if reads==0 and (peak or total):
        raise ValueError('GPT1 resource timing without any reads')
    if reads and (peak==0 or total<peak):
        raise ValueError('GPT1 invalid resource timing accumulator')
    if preloads==0 and preload_peak:
        raise ValueError('GPT1 FSK preload time without any preload')
    if cause not in CAUSES:
        raise ValueError('unknown GPT1 GP0 error reason')
    if cause and not fault['gp0_capture_started']:
        raise ValueError('GPT1 reason without GP0 capture')
    if cause and fault['gp0_dma_error']!=3:
        raise ValueError('GPT1 code 3 reason conflicts with GPF1 DMA code')
    if fault['gp0_dma_error']==3 and cause==0:
        raise ValueError('GPF1 code 3 requires specific GPT1 reason')
    if packed&~0xffff:
        raise ValueError('GPT1 reserved PCM ring bits set')
    backlog=packed&255
    offset=(packed>>8)&255
    if backlog>4:
        raise ValueError('GPT1 impossible queue occupancy exceeds four blocks')
    if cause==3 and offset<80:
        raise ValueError('GPT1 invalid-offset reason without invalid offset')
    if offset>79 and cause!=3:
        raise ValueError('GPT1 invalid PCM offset without offset-failure reason')
    if not fault['gp0_capture_started'] and any((reads,peak,total,preload_peak,preloads,backlog,offset)):
        raise ValueError('GPT1 contains capture measurements without capture')
    return {
        'version':'GPT1','valid':valid,'module_serial':serial,
        'resource_reads':reads,'resource_peak_tim17_ticks':peak,
        'resource_total_tim17_ticks_modulo':total,
        'resource_peak_approx_us':round(peak/96,2),
        'resource_timer_resolution':'96MHz nominal; 16-bit counter wraps every ~682.7us',
        'resource_timing_qualifier':'individual calls spanning >=1 TIM17 counter wrap are ambiguous; values are not definitive wall times',
        'fsk_preloads':preloads,
        'fsk_preload_peak_100us_ticks':preload_peak,
        'fsk_preload_peak_approx_ms':round(preload_peak/10,2),
        'gp0_code3_reason':CAUSES[cause],
        'gp0_code3_reason_id':cause,
        'pcm_backlog_at_stop_blocks':backlog,
        'pcm_backlog_qualifier':'sampled on op1 stop, not at IRQ failure; foreground may consume queued blocks before stop',
        'pcm_offset_at_stop_samples':offset,
        'read_only':True,'boot_local':True,
    }

def run(port:str, *, save:Path|None=None)->dict:
    """Read raw records first; write evidence BEFORE the cable session reboots.

    Even if the host decoder rejects a valid firmware report, save the exact
    raw bytes and all successfully decoded fields. The physical boot-local
    record cannot be recovered after c.end() resets the radio.
    """
    from k6_install import Cable,CABLE_HANDSHAKE,open_port
    from k6_port_lock import serial_lock
    from types import SimpleNamespace
    record={
        'fault_raw_hex':None, 'timing_raw_hex':None,
        'fault':None, 'timing':None, 'decode_error':None,
    }
    with serial_lock(port):
        with open_port(SimpleNamespace(port=port)) as dev:
            c=Cable(dev,print)
            try:
                if not c.handshake(CABLE_HANDSHAKE,wait=10):
                    raise RuntimeError('radio did not respond to cable session')
                info=c.info()
                if info.get('protocol')!=5 or info.get('arena')!=14976:
                    raise RuntimeError('unexpected firmware protocol or arena')
                # Both reads must precede end() to preserve this boot's data.
                raw_fault=c.call(0x75)
                record['fault_raw_hex']=raw_fault.hex()
                raw_timing=c.call(0x76)
                record['timing_raw_hex']=raw_timing.hex()
                record['fault']=decode_fault(raw_fault)
                record['timing']=decode_timing(raw_timing,record['fault'])
                return {'fault':record['fault'],'timing':record['timing']}
            except Exception as exc:
                record['decode_error']=f'{type(exc).__name__}: {exc}'
                raise
            finally:
                # Persist in the same cable session, before c.end() resets
                # the radio. Save even if one query or a decoder failed.
                try:
                    if save is not None and (
                            record['fault_raw_hex'] is not None or
                            record['timing_raw_hex'] is not None):
                        with save.open('x') as output:
                            output.write(json.dumps(record,indent=2,sort_keys=True)+'\n')
                finally:
                    c.end()

def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--ack-reboot',action='store_true',help='required: exiting cable session resets receiver')
    p.add_argument('--save',type=Path,help='NEW JSON file only; never overwrite evidence')
    a=p.parse_args()
    if not a.ack_reboot:p.error('specify --ack-reboot to acknowledge cable-session restart')
    obj=run(a.port,save=a.save)
    data=json.dumps(obj,indent=2,sort_keys=True)+'\n'
    print(data,end='')
    if not obj['fault']['valid']:
        p.exit(2,'No completed GP0 module run in this boot; diagnostic is empty\n')

if __name__=='__main__':
    main()
