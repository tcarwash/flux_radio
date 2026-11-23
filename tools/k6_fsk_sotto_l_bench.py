#!/usr/bin/env python3
"""FSK-10: ONE guarded two-frame 64-byte native-FSK experiment on shared Flux serial lock.

Run only on confirmed conducted dummy-load/air-coupled SDR fixture. The preexisting
FSK-09 transaction owns /dev/ttyUSB0 exclusively through its atomic serial flock,
backs up and restores signed slot-30 code, observes short TX lease, restores VFO,
and never retries a failed run. This wrapper freezes exactly which test runs.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_boundary_bench import run_transaction
from modem import k6mod

EXPECTED_SOURCE_SHA='0efc961b43954dd3ad72f82558458a3c136ff3bfb79ac39878a63646b3531a93'
EXPECTED_CODE_SHA='1ee09901e3d224493124b58ac4bd16e992b60c43fcb061b5b394756bf7299970'
HERE=Path(__file__).resolve().parents[1]
SOURCE=HERE/'c/k6/mod/fsk_sotto_l_boundary_armed.c'


def frozen_build() -> dict:
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=EXPECTED_SOURCE_SHA:
        raise RuntimeError('Refusing: FSK-10 RF source differs from frozen preflight')
    text=SOURCE.read_text()
    for required in ('#define FSK_BOUNDARY_ENABLE 1',
                     '#define FSK_BOUNDARY_FIXTURE_ACK 1',
                     '#define FSK_BOUNDARY_WATCHDOG_ACK 1',
                     '#define FSK_WORDS 32u',
                     'W(R_LENGTH,0x3f00u);'):
        if required not in text:
            raise RuntimeError('Missing FSK-10 test constraint: '+required)
    code,entry=k6mod.build(str(SOURCE))
    if (entry!=0 or k6mod.source_level(str(SOURCE))!=3 or
        hashlib.sha256(code).hexdigest()!=EXPECTED_CODE_SHA):
        raise RuntimeError('Frozen FSK-10 module compile mismatch')
    return dict(source_sha256=EXPECTED_SOURCE_SHA,code_sha256=EXPECTED_CODE_SHA,
                compiled_bytes=len(code))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    args=p.parse_args()
    if not args.dummy_load_confirmed:
        p.error('Requires explicit --dummy-load-confirmed; default is offline-only')
    print('Frozen code:',frozen_build(),flush=True)
    r=run_transaction(args.port,args.prefix,SOURCE,
                      args.prefix.with_name(args.prefix.name+'-slot30-before.k6m'),
                      armed=True,expected_result=64,
                      experiment='Flux FSK-10 two 64-byte native 4800-bps frames',
                      watchdog_ms=1900)
    if not r['transmission_confirmed_by_firmware']:
        raise RuntimeError('Module did not confirm 2x32 word completion; inspect RF capture')
    print('FSK-10 completed and restored both module and VFO',flush=True)


if __name__=='__main__':
    main()
