#!/usr/bin/env python3
"""FSK-09S-E: four timed FIFO appends within a proven 64-word total capacity.

One 64-word hardware frame; preload 48, start TX, four 4-word batches at
>=70,95,120,145ms. Total 64<=documented physically proven preload 64;
no uncertain FIFO headroom or interrupt; no destructive FIFO reads. Only
on dummy-loaded conducted fixture, canonical nonblocking Flux serial lock
and one 1900ms TX watchdog via existing signed restore transaction.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from modem import k6mod
from k6_fsk_boundary_bench import run_transaction

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/fsk_fourbatch_append_armed.c'
SOURCE_SHA='79f545d65f6006a34d511679d644429c383cb1bef4cc7970729fce7cb320ed8e'
CODE_SHA='a75d0bf0330ffda460c13ef30165ecad2fe02bf9de8a3412b55be51c4cd074c4'


def frozen_build():
    s=SOURCE.read_text()
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('Frozen source mismatch: refuse TX')
    markers=('#define FSK_BOUNDARY_ENABLE 1','#define FSK_BOUNDARY_FIXTURE_ACK 1',
             '#define FSK_BOUNDARY_WATCHDOG_ACK 1','#define FSK_WORDS 64u',
             '#define FSK_NUM_FRAMES 1u','#define FSK_MAX_TX_MS 1900u',
             'W(R_LENGTH,0x7f00u);','for(i=0;i<48u;i++)',
             'for(batch=0;batch<4u;batch++)','wait_until(a,start,70u+25u*batch)',
             'W(R_FIFO,word_for(frame,48u+4u*batch+j));')
    for x in markers:
        if x not in s:raise RuntimeError('Missing bounded RF safety invariant '+x)
    code,entry=k6mod.build(str(SOURCE))
    if len(code)!=1244 or entry!=0 or k6mod.source_level(str(SOURCE))!=3 or hashlib.sha256(code).hexdigest()!=CODE_SHA:
        raise RuntimeError('Frozen module code differs from prospectively audited image')
    return dict(source_sha256=SOURCE_SHA,code_sha256=CODE_SHA,compiled_code_bytes=len(code))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',required=True,type=Path)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    a=p.parse_args()
    if not a.dummy_load_confirmed:p.error('Requires physical conducted dummy-load fixture confirmation')
    print('Frozen FSK-09S-E module',frozen_build(),flush=True)
    report=run_transaction(a.port,a.prefix,SOURCE,
            a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
            armed=True,expected_result=64,
            experiment='FSK-09S-E 64-word frame: 48 preload and four separate 4-word timed appends',
            watchdog_ms=1900)
    if not report['transmission_confirmed_by_firmware']:
        raise RuntimeError('Incomplete frame status; preserve SDR and do not retry')
    print('FSK-09S-E finite TX completed, original slot/VFO restored',flush=True)

if __name__=='__main__':main()
