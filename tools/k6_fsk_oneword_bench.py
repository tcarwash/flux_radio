#!/usr/bin/env python3
"""FSK-09S-C: isolated, bounded one-word mid-TX FIFO append discriminator.

Control fully preloads 49 words. Second frame preloads 48 then appends one
16-bit word once 70 ms after TX start, both below previously proven 64-word
capacity even without a single word consumed. No IRQ, destructive reads,
uncertain FIFO headroom inference, or automatic retries. Same canonical
Flux serial flock and preflight/restore transaction as FSK-09/10/11.
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
SOURCE=ROOT/'c/k6/mod/fsk_oneword_append_armed.c'
SOURCE_SHA='034700f2041ebbcfcfc1c1626320a1cce1240f26b3c93f2c1fde62b14a1dd9eb'
CODE_SHA='1a57645904997c560eabad4917731012a0a0089d7df073e042ffb38b01518197'


def frozen_build():
    src=SOURCE.read_text()
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:
        raise ValueError('Refusing modified armed source')
    for marker in ('#define FSK_BOUNDARY_ENABLE 1', '#define FSK_BOUNDARY_FIXTURE_ACK 1',
                   '#define FSK_BOUNDARY_WATCHDOG_ACK 1', '#define FSK_WORDS 49u',
                   '#define FSK_NUM_FRAMES 2u', '#define FSK_MAX_TX_MS 1900u',
                   'W(R_LENGTH,0x6100u);','W(R_FIFO,word_for(frame,48u));',
                   'wait_until(a,start,70u)'):
        if marker not in src:
            raise ValueError('Missing safety invariant '+marker)
    code,entry=k6mod.build(str(SOURCE))
    if entry!=0 or k6mod.source_level(str(SOURCE))!=3 or hashlib.sha256(code).hexdigest()!=CODE_SHA:
        raise ValueError('Compiled module does not match frozen code')
    return {'source_sha256':SOURCE_SHA,'code_sha256':CODE_SHA,'code_bytes':len(code)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    a=p.parse_args()
    if not a.dummy_load_confirmed:p.error('Physical dummy-load confirmation required')
    print('Frozen image',frozen_build(),flush=True)
    x=run_transaction(a.port,a.prefix,SOURCE,
       a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
       armed=True,expected_result=98,
       experiment='FSK-09S-C two 49word native FSK frames; single safe timed word append',
       watchdog_ms=1900)
    if not x['transmission_confirmed_by_firmware']:
        raise RuntimeError('Frame result incomplete; retain IQ, no automatic retry')
    print('One-word append bench finished, radio module and VFO restored',flush=True)


if __name__=='__main__':main()
