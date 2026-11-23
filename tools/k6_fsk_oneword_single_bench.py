#!/usr/bin/env python3
"""FSK-09S-D: guarded one-only 48 + 1-word in-flight FIFO append frame.

A new independent test, not an automatic retry of failed FSK-09S-C. No
prior modem rearm. Total 49 FIFO writes, below proven 64-word preload.
Canonical nonblocking Flux serial flock, exact trusted slot30 restoration,
VFO restoration, and 1900ms firmware TX watchdog preserved via existing
FSK hardware transaction. No unknown IRQ/FIFO reads or unbounded TX.
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
SOURCE=ROOT/'c/k6/mod/fsk_oneword_single_armed.c'
SOURCE_SHA='7c6a90b2bb91f6304df7851741955df8a417f22b089479c4dfd221c9c21a7fea'
CODE_SHA='63c7b7597136d4c3d5c4d3b14851445edd8c968cd7ae52a3578e76e3f726b6e1'


def frozen_build():
    src=SOURCE.read_text()
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('FSK-09S-D source differs from frozen code')
    for marker in ('#define FSK_BOUNDARY_ENABLE 1',
                   '#define FSK_BOUNDARY_FIXTURE_ACK 1',
                   '#define FSK_BOUNDARY_WATCHDOG_ACK 1',
                   '#define FSK_WORDS 49u','#define FSK_NUM_FRAMES 1u',
                   '#define FSK_MAX_TX_MS 1900u',
                   'W(R_LENGTH,0x6100u);',
                   'ret=send_frame(api,1u);',
                   'wait_until(a,start,70u)',
                   'W(R_FIFO,word_for(frame,48u));'):
        if marker not in src:raise RuntimeError('Required safe source invariant missing: '+marker)
    code,entry=k6mod.build(str(SOURCE))
    if not 0<len(code)<4096 or entry!=0 or k6mod.source_level(str(SOURCE))!=3 or hashlib.sha256(code).hexdigest()!=CODE_SHA:
        raise RuntimeError('Compiled RF module does not match frozen expected digest')
    return dict(armed_source_sha256=SOURCE_SHA,code_sha256=CODE_SHA,code_bytes=len(code))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    a=p.parse_args()
    if not a.dummy_load_confirmed:p.error('Requires dummy-load confirmation')
    print('FSK-09S-D preflight',frozen_build(),flush=True)
    report=run_transaction(a.port,a.prefix,SOURCE,
                a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
                armed=True,expected_result=49,
                experiment='FSK-09S-D one single 49-word TX, with 48 preloaded and one timed in-flight FIFO write',
                watchdog_ms=1900)
    if not report['transmission_confirmed_by_firmware']:
        raise RuntimeError('Early frame finish or module failure; retain IQ and do not retry automatically')
    print('FSK-09S-D bounded TX complete, original module and VFO restored',flush=True)


if __name__=='__main__':main()
