#!/usr/bin/env python3
"""Compare actual compiled Sotto development modules over the SAME virtual RF inputs.

Offline-only: compiles two module trees, runs native C reference codec,
Unicorn Cortex-M0 TX, synthetic IQ, and independent C/Python RX oracles.
Never flashes, opens serial/SDR, or transmits RF. Timing is a MODEL.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import tempfile
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import prepare,trial,accepted

DEFAULT_CASES=((0,34),(4800,10),(4800,34),(5745,10),(5745,34))

def parse_case(value):
    try:
        adc,spi=(int(v) for v in value.split(':'))
    except (ValueError,TypeError):
        raise argparse.ArgumentTypeError('case must be ADC_CYCLES:SPI_US')
    if not 0<=adc<=12000 or not 1<=spi<=200:
        raise argparse.ArgumentTypeError('ADC must be 0..12000 cycles and SPI 1..200 us')
    return adc,spi

def mixed_pcm():
    """Deterministic silence/voiced/multitone stimulus; NOT a natural-speech score."""
    n=9600
    t=np.arange(n)/8000
    v=np.zeros(n,dtype=np.float64)
    v[1600:4000]=.5*np.sin(2*np.pi*185*t[1600:4000])
    v[4000:6400]=.35*np.sin(2*np.pi*315*t[4000:6400])+.2*np.sin(2*np.pi*631*t[4000:6400])
    v[6400:]=.2*np.sin(2*np.pi*250*t[6400:])+.16*np.sin(2*np.pi*730*t[6400:])
    return (v*20000).astype('<i2')

def read_pcm(path):
    with wave.open(str(path),'rb') as w:
        if (w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getcomptype()) != (8000,1,2,'NONE'):
            raise ValueError('WAV must be 8000Hz mono uncompressed PCM16')
        pcm=np.frombuffer(w.readframes(w.getnframes()),dtype='<i2').copy()
    if not 800<=len(pcm)<=24000 or len(pcm)%80:
        raise ValueError('WAV must hold 800..24000 samples divisible by 80')
    return pcm

def tally(rows):
    if not rows:raise ValueError('empty trial matrix')
    base_pass=sum(bool(a['accepted']) for a,b in rows)
    cand_pass=sum(bool(b['accepted']) for a,b in rows)
    regressions=sum(bool(a['accepted']) and not bool(b['accepted']) for a,b in rows)
    new_passes=sum(not bool(a['accepted']) and bool(b['accepted']) for a,b in rows)
    return dict(trials=len(rows),baseline_pass=base_pass,candidate_pass=cand_pass,
                improved=new_passes,regressed=regressions,no_regressions=regressions==0)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline',type=Path,required=True,help='pinned original Sotto development checkout')
    p.add_argument('--candidate',type=Path,required=True,help='pinned optimized Sotto development checkout')
    p.add_argument('--wav',type=Path,action='append',default=[],help='additional 8k mono PCM16 fixture; repeat as needed')
    p.add_argument('--include-mixed',action='store_true',help='include 1.2s deterministic mixed-frequency/silence stimulus')
    p.add_argument('--case',type=parse_case,action='append',default=[],help='ADC_CYCLES:SPI_US; repeat, defaults include passing/failing controls')
    p.add_argument('--cpi',type=float,default=1.5,help='modeled cycles per instruction (not calibrated)')
    p.add_argument('--json',type=Path,help='write detailed machine-readable results')
    p.add_argument('--strict',action='store_true',help='exit nonzero if any prior success regresses')
    a=p.parse_args()
    if not 0.2<=a.cpi<=20 or not np.isfinite(a.cpi):p.error('cpi must be finite and 0.2..20')
    try:
        audio=[('triangle-1.04s',None)]
        if a.include_mixed:audio.append(('mixed-1.2s',mixed_pcm()))
        audio.extend((str(f),read_pcm(f)) for f in a.wav)
    except (wave.Error,OSError,ValueError) as e:p.error(str(e))
    cases=a.case or list(DEFAULT_CASES)
    if len(cases)*len(audio)>150:p.error('too many cases')
    results=[]
    for name,pcm in audio:
        with tempfile.TemporaryDirectory(prefix='flux-sotto-opt-') as d:
            artifacts={}
            for kind,tree in [('baseline',a.baseline),('candidate',a.candidate)]:
                work=Path(d)/kind;work.mkdir()
                artifacts[kind]=prepare(tree,work,pcm=pcm)
            for adc,spi in cases:
                r={kind:trial(artifact,Channel(),cpi=a.cpi,spi_us=spi,adc_cycles=adc)
                   for kind,artifact in artifacts.items()}
                for v in r.values():v['accepted']=accepted(v)
                results.append(dict(fixture=name,adc_cycles=adc,spi_us=spi,**r))
                print(f'{name} ADC={adc} SPI={spi}us | baseline {"PASS" if r["baseline"]["accepted"] else "FAIL"}'
                      f' ({r["baseline"]["samples"]}/{r["baseline"]["samples_expected"]})'
                      f' -> candidate {"PASS" if r["candidate"]["accepted"] else "FAIL"}'
                      f' ({r["candidate"]["samples"]}/{r["candidate"]["samples_expected"]})',flush=True)
    scores=tally([(r['baseline'],r['candidate']) for r in results])
    out=dict(format_version=1,method='compiled Sotto + Unicorn TX + virtual IQ + compiled RX; modeled not calibrated',
             cpi=a.cpi,summary=scores,results=results)
    print('Summary:',json.dumps(scores,sort_keys=True),flush=True)
    if a.json:a.json.write_text(json.dumps(out,indent=2)+'\n')
    if a.strict and not scores['no_regressions']:
        return 1
    return 0

if __name__=='__main__':sys.exit(main())
