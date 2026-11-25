#!/usr/bin/env python3
"""Build the current API31 on-radio Sotto PCM tone/FSK qualification module.

This builds ARM code and a manifest only; no serial, PTT, SDR or radio access.
Uses the pinned, verified Sotto slim encoder and checked K6 resource tables.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from tools.k6_sotto_module import adapt,FIXED,PIN

SRC=ROOT/'c/k6/mod/sotto_fsk_drain_tx.c'


def build(*,settle_ms=20,interframe_ms=0):
    if not 0<=settle_ms<=30 or not 0<=interframe_ms<=100:
        raise ValueError('Out of safe laboratory timing limits')
    revision=subprocess.check_output(['git','-C',str(ROOT/'sotto'),'rev-parse','HEAD'],text=True).strip()
    if revision!=PIN:raise ValueError('Codec source pin changed; requalification required')
    with tempfile.TemporaryDirectory(prefix='sotto-pcm-rf-') as td:
        (Path(td)/'sotto_slim_context.inc').write_text(adapt((FIXED/'sotto_fx_slim.c').read_text()))
        flags=[*k6mod.CFLAGS,'-I'+td,'-I'+str(FIXED),'-isystem',str(FIXED/'cm4/inc'),
               '-DSOTTO_V36','-DSOTTO_PITCH_THIRD','-DFX_NARROW_MUL',
               '-Wno-unused-function','-Wno-unused-const-variable','-Wno-unused-variable',
               '-DSOTTO_DRAIN_ARMED=1','-DSOTTO_DRAIN_LIVE_ENCODE=1',
               '-DSOTTO_HOST_RESOURCES=1','-DSOTTO_DRAIN_EXTRA_WORDS=0',
               '-DSOTTO_DRAIN_KEEP_PA=1','-DSOTTO_DRAIN_DEKEY_BETWEEN=0',
               f'-DSOTTO_DRAIN_SETTLE_MS={settle_ms}',
               f'-DSOTTO_DRAIN_INTERFRAME_MS={interframe_ms}']
        code,entry=k6mod.build(str(SRC),cflags=flags)
    if k6mod.module_name(code)!='SOTTO PCM RF' or entry!=16 or len(code)>=11500:
        raise ValueError('Unexpected ARM module name/entry/arena headroom')
    return code,entry


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--settle-ms',default=20,type=int)
    p.add_argument('--interframe-ms',default=0,type=int)
    a=p.parse_args()
    code,entry=build(settle_ms=a.settle_ms,interframe_ms=a.interframe_ms)
    a.out.mkdir(exist_ok=False,parents=True)
    (a.out/'module.bin').write_bytes(code)
    manifest=dict(
        format=1,module='SOTTO PCM RF',level=31,source='sotto_fsk_drain_tx.c',
        entry=entry,bytes=len(code),sha256=hashlib.sha256(code).hexdigest(),
        codec_pin=PIN,pcm='1kHz sine, 8kHz PCM16, 320ms',
        rf='3 guarded 33-word FSK packets, 4800bps, BEGIN/VOICE/END',
        channel='433.500MHz, dummy load',
        tx_behavior='encode in RX; finite TX lease; continuous PA only across bounded FSK session',
        pa_settle_ms=a.settle_ms,interframe_ms=a.interframe_ms,
        transmits_on_install=False,requires_explicit_run=True)
    (a.out/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__':main()
