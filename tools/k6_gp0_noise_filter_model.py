#!/usr/bin/env python3
"""OFFLINE conditional GP0 high-rate input-noise sensitivity using actual Rust.

The input is modeled POPCOUNT/64, not true PDM bit timing or a measured live
GPIO trace. Compiles the *exact* GP0 firmware Rust decimator (not a rewritten
math approximation), uses the empirical 7500 clock numerator, and compares
filter behavior on three noise hypotheses. Real RF capture alone cannot
separate aliased high-rate noise from microphone/analog in-band noise.

The existing production build gate intentionally REJECTS combining AA3/FIR11
and 7500-clock until tested. These standalone host builds are simulation-only,
not firmware artifacts or a recommendation to enable a combination on-radio.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import math
import subprocess
import tempfile

import numpy as np
from scipy.signal import butter,sosfilt

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'firmware/k6-aura/sotto-api30-gp0-capture.rs'
INPUT_RATE=93050.0   # inferred from measured GP0 8529..8533 PCM/sec, NOT direct PDM rate
NOMINAL_GROUP_RATE=87260
CLOCK_WEIGHT=7500
OUT_RATE=INPUT_RATE*CLOCK_WEIGHT/NOMINAL_GROUP_RATE
MODES={'legacy':(), 'aa3':('--cfg','k6_sotto_gp0_aa3'),
       'fir11_late':('--cfg','k6_sotto_gp0_fir11_late'),
       'fir11':('--cfg','k6_sotto_gp0_fir11')}


def build_runner(src_text:str):
    st=src_text[src_text.index('#[repr(C)]\n#[derive(Clone,Copy)]\nstruct Decimator'):src_text.index('\n#[repr(C)]\nstruct State')]
    div=src_text[src_text.index('#[inline(always)]\nfn udiv'):src_text.index('// Exact constant-denominator divider')]
    reciprocal=src_text[src_text.index('#[cfg(k6_sotto_gp0_fast_isr)]\n#[inline(always)]\nfn gp0_div_87260'):src_text.index('#[inline(always)]\nfn dec_push')]
    push=src_text[src_text.index('#[inline(always)]\nfn dec_push'):src_text.index('// The real hardware uses 16 volatile')]
    return '''#![allow(dead_code)]
use std::io::{self,Read,Write};
const FS:u32=87260;
'''+st+'\n'+div+'\n'+reciprocal+'\n'+push+'''
fn main(){
    let mut d=Decimator{fs:FS,..Decimator::empty()};
    let mut b=Vec::new();io::stdin().read_to_end(&mut b).unwrap();
    let mut stdout=io::stdout().lock();
    for count in b{
        let mut out=0i16;
        match dec_push(&mut d,count as u32,&mut out){
            1=>stdout.write_all(&out.to_le_bytes()).unwrap(),
            0=>{},other=>panic!("bad gp0 status {}",other)
        }
    }
}
'''


def compile_variants(where:Path):
    rust=where/'noise_model_actual_gp0.rs'
    rust.write_text(build_runner(SOURCE.read_text()))
    result={}
    for name,flags in MODES.items():
        executable=where/name
        subprocess.run(['rustc','--edition=2021','-O','--cfg','k6_sotto_gp0_fast_isr',
                        '--cfg','k6_sotto_gp0_clock_7500',*flags,str(rust),'-o',str(executable)],
                       capture_output=True,text=True,check=True,timeout=50)
        result[name]=executable
    return result


def run(executable:Path,counts):
    p=subprocess.run([str(executable)],input=np.asarray(counts,dtype=np.uint8).tobytes(),
                     check=True,capture_output=True,timeout=30)
    return np.frombuffer(p.stdout,dtype='<i2').copy()


def make_noise(kind:str,n:int,seed:int):
    rng=np.random.default_rng(seed)
    z=rng.normal(size=n)
    if kind=='white':pass
    elif kind=='highpass':z=z-np.concatenate(([0],z[:-1]))
    elif kind=='inband':
        sos=butter(3,2500,fs=INPUT_RATE,output='sos')
        z=sosfilt(sos,z)
    else:raise ValueError('unknown noise model')
    # Normalize the input-count variance; resulting PCM shapes differ.
    z=z-np.mean(z)
    return z/np.std(z)*1.35


def to_counts(z,tone_hz=0,amplitude=3.2):
    # Only valid 0..64 popcounts; count-level stimulus not ideal true PDM.
    value=32+z
    if tone_hz:
        t=np.arange(len(z))/INPUT_RATE
        value=value+amplitude*np.sin(2*np.pi*tone_hz*t)
    return np.clip(np.rint(value),0,64).astype(np.uint8)


def fit_amp(y,hz,trim_sec=.12):
    x=np.asarray(y,dtype=float)[round(OUT_RATE*trim_sec):]
    t=np.arange(len(x))/OUT_RATE
    B=np.column_stack((np.sin(2*np.pi*hz*t),np.cos(2*np.pi*hz*t),np.ones(len(x))))
    a,b,c=np.linalg.lstsq(B,x,rcond=None)[0]
    residual=x-B@[a,b,c]
    return float(math.hypot(a,b)),float(np.std(residual))


def model(duration=.85):
    if not .5<=duration<=1.5:raise ValueError('duration .5..1.5 seconds')
    n=round(INPUT_RATE*duration)
    report={'schema':1,'offline_only':True,'actual_firmware_rust_decimator':True,
        'model_not_physical':'input high-rate popcounts are synthetic; PDM shaping, analog noise and GPIO timing NOT measured',
        'output_rate_model_hz':round(OUT_RATE,3),'input_group_rate_inferred_hz':INPUT_RATE,
        'actual_clock_weight':CLOCK_WEIGHT,'denominator':NOMINAL_GROUP_RATE,
        'excluded_from_firmware_build_gate':True,'duration_seconds':duration,
        'noise_models':{},'speech_band_tone_response':{},'alias_tone_response':{}}
    with tempfile.TemporaryDirectory(prefix='gp0-noise-offline-') as d:
        programs=compile_variants(Path(d))
        for index,kind in enumerate(('white','highpass','inband')):
            noise=make_noise(kind,n,11002+index)
            z=to_counts(noise)
            t=to_counts(noise,tone_hz=1000)
            rows={}
            for name,executable in programs.items():
                noise_out=run(executable,z)
                tone_out=run(executable,t)
                start=round(.12*OUT_RATE)
                noise_rms=np.std(noise_out[start:]/32768)
                amp,remain=fit_amp(tone_out,1000)
                rows[name]={'pcm_samples':len(tone_out),
                    'noise_rms_full_scale':round(float(noise_rms),6),
                    'tone_peak_full_scale':round(amp/32768,6),
                    'tone_fit_residual_full_scale':round(remain/32768,6),
                    'tone_to_noise_db':round(float(20*math.log10(max(amp/math.sqrt(2),1e-12)/max(remain,1e-12))),2)}
            baseline=rows['legacy']
            for name,row in rows.items():
                row['snr_gain_vs_unfiltered_db']=round(row['tone_to_noise_db']-baseline['tone_to_noise_db'],2)
            report['noise_models'][kind]=rows
        # Isolate tone attenuation without noise to avoid mistaking white
        # count noise/quantization for response of high-rate prefilters.
        silence=np.zeros(n)
        for hz in (500,1000,2000,2800,3200):
            b=to_counts(silence,tone_hz=hz)
            amps={name:fit_amp(run(executable,b),hz)[0] for name,executable in programs.items()}
            base=amps['legacy']
            report['speech_band_tone_response'][str(hz)]={name:round(20*math.log10(max(v,1e-8)/max(base,1e-8)),2) for name,v in amps.items()}
        for hz in (7500,9000,15000):
            b=to_counts(silence,tone_hz=hz)
            # Aliasing can appear at an unexpected output frequency; record
            # strongest voice-band magnitude, not only predicted alias bin.
            strengths={}
            for name,executable in programs.items():
                y=run(executable,b).astype(float)
                y=y[round(OUT_RATE*.12):]
                window=np.hanning(len(y))
                fft=np.abs(np.fft.rfft((y-y.mean())*window))
                ff=np.fft.rfftfreq(len(y),d=1/OUT_RATE)
                idx=np.where((ff>=100)&(ff<=3800))[0]
                peak=idx[int(np.argmax(fft[idx]))]
                strengths[name]={'peak_hz':round(float(ff[peak]),1),
                                 'peak_amplitude':float(2*fft[peak]/window.sum())}
            base=max(strengths['legacy']['peak_amplitude'],1e-12)
            report['alias_tone_response'][str(hz)]={name:{'strongest_voice_band_hz':row['peak_hz'],
                'reduction_vs_legacy_db':round(20*math.log10(max(row['peak_amplitude'],1e-12)/base),2)}
                for name,row in strengths.items()}
    return report


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--json-out',required=True,type=Path)
    args=ap.parse_args()
    if args.json_out.exists():ap.error('refuse overwrite existing evidence')
    r=model()
    with args.json_out.open('x') as f:f.write(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print('input rate',INPUT_RATE,'corrected output rate',OUT_RATE)
    for kind,rows in r['noise_models'].items():
        print('MODEL',kind)
        for name,row in rows.items():print(' ',name,'noise',row['noise_rms_full_scale'],'snr',row['tone_to_noise_db'],'SNR gain',row['snr_gain_vs_unfiltered_db'])
    print('SPEECH RESPONSE',r['speech_band_tone_response'])
    print('ALIASES',r['alias_tone_response'])


if __name__=='__main__':main()
