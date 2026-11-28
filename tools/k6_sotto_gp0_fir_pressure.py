#!/usr/bin/env python3
"""Run the Q15 Wiener FIR GP0 module through the Cortex-M0 virtual TX/RX path.

This checks packet completion and microphone-ring pressure only. The FIR
intentionally changes codec input, so baseline bit-exactness is not a criterion.
No radio, serial port, PTT, or physical RF is accessed.
"""
from __future__ import annotations
import argparse, hashlib, json, sys, tempfile, types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
pkg=sys.modules.get('tools')
if pkg is None or str(ROOT/'tools') not in getattr(pkg,'__path__',[]):
    pkg=types.ModuleType('tools');pkg.__path__=[str(ROOT/'tools')];sys.modules['tools']=pkg
import numpy as np
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_module import build
from tools.k6_sotto_virtual_link import prepare,trial

SAMPLES=8293
ADC_CYCLES=4000
SPI_US=6.19212962962963
SPI_BYTE_US=0.9403935185185185
PROFILES=('multitone','silence','chirp','bursts')


def waveform(name):
    t=np.arange(SAMPLES,dtype=np.float64)/8000.0
    if name=='silence': x=np.zeros(SAMPLES)
    elif name=='multitone': x=7200*np.sin(2*np.pi*320*t)+2400*np.sin(2*np.pi*870*t)
    elif name=='chirp':
        f0,f1=250.,2850.;x=10000*np.sin(2*np.pi*(f0*t+(f1-f0)/(2*t[-1])*t*t))
    elif name=='bursts': x=9500*np.sin(2*np.pi*1040*t)*((np.arange(SAMPLES)//640)%2==0)
    else: raise ValueError(name)
    return np.rint(x).clip(-32768,32767).astype('<i2')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--cpi',type=float,nargs='+',default=[2.5,3.0])
    ap.add_argument('--profile',choices=PROFILES,action='append')
    a=ap.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
    code,entry=build(host_resources=True,gp0_wiener_fir_tx=True,ram_micro=True,
                     fast_div=True,fast_history_copy=True,fast_cb_pair=True,fast_lag_pair=True)
    module_sha=hashlib.sha256(code).hexdigest()
    margin=14976-1040-len(code)-24-3884
    rows=[]
    with tempfile.TemporaryDirectory(prefix='sotto-gp0-fir-pressure-') as td:
        temp=Path(td)
        for profile in a.profile or PROFILES:
            pcm=waveform(profile);wd=temp/profile;wd.mkdir()
            artifact=prepare(ROOT,wd,pcm=pcm,gp0=True)
            artifact['code']=code;artifact['entry']=entry;artifact['module_stack_window']=1040
            for cpi in a.cpi:
                r=trial(artifact,Channel(),cpi=cpi,adc_cycles=ADC_CYCLES,
                        spi_us=SPI_US,spi_byte_us=SPI_BYTE_US,fd_us=60,
                        fsk_cooperative_model=True)
                no_drops=(r['samples']==r['samples_expected'] and not r['pcm_lost']
                          and not r['lease_expired'])
                packets=(r['session']['complete'] and r['tx']['completed']==r['received']
                         and r['received']>0 and not r['tx']['active']
                         and not any(r['tx'][k] for k in ('aborted','rejected','overflows')))
                row={'profile':profile,'cpi':cpi,'no_drops':bool(no_drops),
                     'packet_path_complete':bool(packets),'module_bytes':len(code),
                     'module_sha256':module_sha,'scratch_required':3884,
                     'scratch_available':14976-1040-len(code)-24,'scratch_margin':margin,
                     'samples':r['samples'],'samples_expected':r['samples_expected'],
                     'pcm_lost':bool(r['pcm_lost']),'lease_expired':bool(r['lease_expired']),
                     'codec_frames':r['codec_frames'],'module_return':r['module_return'],
                     'codec_exact_vs_unfiltered_oracle':bool(r['codec_exact']),
                     'session_complete':bool(r['session']['complete']),
                     'session_valid':r['session']['valid'],'session_invalid':r['session']['invalid'],
                     'rx_received':r['received'],'tx_active_on_exit':bool(r['tx']['active']),
                     'logs':r['logs'],
                     'reliability_gate_pass':bool(no_drops and packets)}
                rows.append(row)
                print(f"{profile:9} CPI={cpi:.1f} pass={row['reliability_gate_pass']} "
                      f"PCM={r['samples']}/{r['samples_expected']} lost={r['pcm_lost']} "
                      f"RX={r['received']} invalid={r['session']['invalid']}",flush=True)
    report={'schema':1,'offline_only':True,
            'experiment':'15-tap Q15 Wiener FIR inside GP0 TX module; Cortex-M0 virtual RF/receiver path',
            'module_sha256':module_sha,'module_bytes':len(code),
            'ram_micro':True,'scratch_required':3884,
            'scratch_available':14976-1040-len(code)-24,'scratch_margin':margin,
            'module_stack_window':1040,
            'adc_cycles_per_irq':ADC_CYCLES,'spi_call_us':SPI_US,
            'spi_byte_us':SPI_BYTE_US,'cpi_cases':a.cpi,'profiles':a.profile or list(PROFILES),
            'limitations':['Unicorn timing model is not calibrated physical K6 timing.',
                           'No physical microphone, radio, serial, PTT, or RF was used.',
                           'Codec exactness is compared to unfiltered reference and is expected to differ.',
                           'FIR was trained on a statistical GP0 source model; no listener or on-air benefit proven.'],
            'all_reliability_gates_pass':all(x['reliability_gate_pass'] for x in rows),
            'cases':rows}
    (out/'report.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print('SUMMARY',json.dumps({'all_reliability_gates_pass':report['all_reliability_gates_pass'],
                                'module_bytes':len(code),'scratch_margin':margin},sort_keys=True))

if __name__=='__main__': main()
