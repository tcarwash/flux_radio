#!/usr/bin/env python3
"""Offline RF/codec pressure matrix for real ARM GP0 module + mocked 8kHz mic.

No serial port, bench lock, RF transmission, radio or firmware modification.
The firmware peripheral and microphone IRQ are virtual; reports MUST never be
represented as physical-radio or actual RF evidence. The Codec/module is the
compiled Thumb binary and the RX decoder is compiled C.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

import numpy as np
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_module import build
from tools.k6_sotto_virtual_link import accepted,prepare,trial

SAMPLES=8293
ADC_CYCLES=4000
SPI_US=6.19212962962963
SPI_BYTE_US=0.9403935185185185


def waveform(name: str) -> np.ndarray:
    t=np.arange(SAMPLES,dtype=np.float64)/8000.0
    if name=='silence':
        x=np.zeros(SAMPLES)
    elif name=='multitone':
        x=7200*np.sin(2*np.pi*320*t)+2400*np.sin(2*np.pi*870*t)
    elif name=='chirp':
        f0,f1=250.,2850.
        x=10000*np.sin(2*np.pi*(f0*t+(f1-f0)/(2*t[-1])*t*t))
    elif name=='bursts':
        x=9500*np.sin(2*np.pi*1040*t)*((np.arange(SAMPLES)//640)%2==0)
    else:
        raise ValueError(f'unknown synthetic audio {name}')
    return np.rint(x).clip(-32768,32767).astype('<i2')


def run(output: Path, *, profile_names=('multitone','silence','chirp','bursts'),
        cpi_values=(2.5,3.0)) -> dict:
    output=output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    results=[]
    for profile in profile_names:
        pcm=waveform(profile)
        root=output/profile
        root.mkdir(parents=True,exist_ok=True)
        base=prepare(ROOT,root,pcm=pcm,gp0=True)
        variants={'original':base}
        variants['optimized_4']={**base}
        code,entry=build(host_resources=True,gp0_irq_tx=True,
                         fast_div=True,fast_history_copy=True,
                         fast_cb_pair=True,fast_lag_pair=True)
        assert entry==base['entry']
        assert len(code)<=len(base['code']) and len(code)<=9800
        variants['optimized_4']['code']=code
        variants['optimized_4']['entry']=entry
        for name,art in variants.items():
            for cpi in cpi_values:
                sim=trial(art,Channel(),cpi=cpi,adc_cycles=ADC_CYCLES,
                          spi_us=SPI_US,spi_byte_us=SPI_BYTE_US,fd_us=60,
                          fsk_cooperative_model=True)
                row={
                    'audio_profile':profile,'cpi':cpi,'variant':name,
                    'accepted':bool(accepted(sim)),'module_code_bytes':len(art['code']),
                    'module_sha256':hashlib.sha256(art['code']).hexdigest(),
                    'samples':sim['samples'],'samples_expected':sim['samples_expected'],
                    'pcm_lost':bool(sim['pcm_lost']),
                    'pcm_highwater':sim['pcm_highwater'],
                    'module_return':sim['module_return'],
                    'codec_exact':bool(sim.get('codec_exact')),
                    'wire_decoder_agrees':bool(sim.get('wire_decoder_agrees')),
                    'codec_frames':sim.get('codec_frames'),
                    'session_complete':sim['session']['complete'],
                    'session_valid_packets':sim['session']['valid'],
                    'session_invalid_packets':sim['session']['invalid'],
                    'tx_keyed_on_exit':bool(sim['tx']['active']),
                    'gp0_starts':sim['gp0_starts'],'gp0_stops':sim['gp0_stops'],
                }
                if name=='optimized_4' and not row['accepted'] and cpi==2.5:
                    print('WARNING: optimized variant failed high-load test',profile,file=sys.stderr)
                results.append(row)
                print(f"{profile:9} cpi={cpi:3.1f} {name:11} accepted={row['accepted']} "
                      f"PCM={row['samples']}/{row['samples_expected']} lost={row['pcm_lost']} "
                      f"queue={row['pcm_highwater']} packets={row['session_valid_packets']}",flush=True)
    meta={'test':'actual GP0 Cortex-M0 module virtual microphone + native encoder + virtual FSK/RX',
          'source_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
          'physical_hardware_test':False,
          'not_authorized_to_claim_rf_on_air':True,
          'sample_rate_hz':8000,'sample_count':SAMPLES,'sample_bytes':SAMPLES*2,
          'adc_cycles_per_pdm_irq':ADC_CYCLES,'spi_access_call_us':SPI_US,
          'spi_access_byte_us':SPI_BYTE_US,'fd_access_us':60,
          'fsk_cooperative_model':True,
          'profiles':list(profile_names),'cpu_pressure_cpi':list(cpi_values),
          'cases':results}
    path=output/'matrix-report.json'
    path.write_text(json.dumps(meta,indent=2)+'\n')
    meta['report_path']=str(path)
    return meta


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    r=run(a.out)
    n=len(r['cases']);success=sum(x['accepted'] for x in r['cases'])
    print(f'COMPLETED {n} offline trials, {success} passing; raw report: {r["report_path"]}')

if __name__=='__main__': main()
