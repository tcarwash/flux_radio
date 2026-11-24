#!/usr/bin/env python3
"""FSK-15: inert on-K6 dynamic guarded Sotto packet builder self-test.

Takes ONE exclusive radio + SDR lease, but DOES NOT KEY PTT: the
hash-verified source calls no RF, GPIO or SPI operations. The existing
reversible signed-slot transaction also takes an SDR recording because
we intentionally reuse its proven preflight/backup/restore machinery;
the SDR trace is a baseline, NOT an RF payload experiment. The shared
harness names its generic completion flag 'transmission_confirmed_by_firmware'
on equality of the module return; that field MUST NOT be interpreted as
actual TX for this test. It is only module-completion acknowledgment.

Requires operator-approved separate queue lease, canonical serial lock,
original signed slot/VFO baseline, and confirmed physical dummy load as
fail-closed guard despite no intended PTT. No automatic retry.
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
SOURCE=ROOT/'c/k6/mod/dv_guard33_selftest.c'
SOURCE_SHA='d7793ebb887cc13c4d1079ec9db2055250e501da54f1ac9e75c9cd413fd88bb3'
CODE_SHA='650c3493580b5e53102b46715eb78cc38e6faa79c21632f89b41f60bc1a560ea'

def frozen_module():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('Selftest C source changed; refuse K6 module install')
    s=SOURCE.read_text()
    for bad in ('tx_on(', 'tx_pa(', 'fd_write(', 'fd_read(',
                'fl_prog(', 'fl_erase(', '0x480000', '0x4000'):
        if bad in s:raise RuntimeError('Unexpected hardware action in selftest: '+bad)
    code,entry=k6mod.build(str(SOURCE))
    if (len(code)!=1684 or entry!=0 or
        hashlib.sha256(code).hexdigest()!=CODE_SHA):
        raise RuntimeError('Selftest ARM API3 machine code mismatch')
    return {'source_sha256':SOURCE_SHA,'code_sha256':CODE_SHA,
            'module_code_bytes':len(code),
            'expected_result':33,'no_ptt_or_rf':True}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    p.add_argument('--operator-lease-confirmed',action='store_true')
    a=p.parse_args()
    if not a.dummy_load_confirmed or not a.operator_lease_confirmed:
        p.error('Requires separate acknowledged radio lease AND dummy-load confirmation')
    print('Frozen inert Cortex-M0 packetizer selftest',frozen_module(),flush=True)
    # Emits NO actual TX; reuse signed-slot, original VFO, SDR baseline
    # preflight and restoration code as physically proven in FSK13/14.
    report=run_transaction(
        a.port,a.prefix,SOURCE,
        a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
        armed=True,expected_result=33,
        experiment='FSK-15 NO-RF Cortex-M0 guarded Sotto packet + queue golden selftest',
        watchdog_ms=1900)
    ok=report.get('module_completion',{}).get('result')==33
    if not ok or not report['original_module_restored'] or not report['original_radio_frequency_restored']:
        raise RuntimeError('K6 packetizer on-target selftest failed or restore incomplete')
    print('K6 synthetic packetizer selftest PASS; NO RF transmitted',flush=True)

if __name__=='__main__':
    main()
