"""Offline PDM/count decimator experiment for K6 Sotto GP0 at 87,260Hz.

Does not read radio, transmit RF, or alter original audio. Compares the exact
fractional-boxcar timing with a three-pole high-rate integer IIR prefilter.
"""
from __future__ import annotations
import argparse
import json
from math import cos, sin, pi, sqrt, log10
from pathlib import Path
import numpy as np
from scipy.signal import lfilter

FS=87260
OUT=8000


def decimate(bits: np.ndarray, *, poles=0,fs=FS, fir11=False, fir11_late=False) -> np.ndarray:
    """Python reference for signed, 7-bit-fraction high-rate fixed-point filter.

    Match Rust truncation and DC IIR; divide exactly in Python without the
    optional Cortex-M0 reciprocal. Input is popcount from one 64-bit raw group.
    """
    if (fir11 and fir11_late) or (poles and (fir11 or fir11_late)):
        raise ValueError("select exactly one prefilter")
    phase=0;area=0;dc=0
    lp=[0,0,0]
    taps=[0]*11;tap_head=0;tap_sum=0
    out=[]
    for n in bits:
        x=2*int(n)-64
        if fir11 or fir11_late:
            outgoing=taps[tap_head]
            taps[tap_head]=x
            tap_head=(tap_head+1)%11
            tap_sum+=x-outgoing
            if fir11:
                scaled=tap_sum*128
                x=(-1 if scaled<0 else 1)*(abs(scaled)//11)
            else:
                # Raw signed 11-sample accumulator is normalized at 8 kHz.
                x=tap_sum
        elif poles:
            v=x<<7
            for i in range(poles):
                lp[i]+=(v-lp[i])>>2
                v=lp[i]
            x=v
        newphase=phase+OUT
        if newphase<fs:
            area+=x*OUT;phase=newphase;continue
        left=fs-phase
        whole=area+x*left
        q=(abs(whole)*256)//(11*fs) if fir11_late else \
          (((abs(whole)<<1) if poles or fir11 else (abs(whole)<<8))//fs)
        centered=-q if whole<0 else q
        dc+=((centered<<8)-dc)>>7
        sample=(centered-(dc>>8))*8
        sample=np.clip(sample,-32768,32767)
        out.append(sample)
        phase=newphase-fs
        area=x*phase
    return np.asarray(out,np.int16)


def phase_amp(x: np.ndarray, freq:int, sr=8000) -> float:
    y=x.astype(float)
    # Remove startup transients; measure pure sine amplitude coherently.
    y=y[int(sr*.2):]
    t=np.arange(len(y))/sr
    if not len(y):return 0.
    re=2*np.dot(y,np.cos(2*pi*freq*t))/len(y)
    im=2*np.dot(y,np.sin(2*pi*freq*t))/len(y)
    return float(np.hypot(re,im))


def study(duration=0.8, fs=FS):
    t=np.arange(int(duration*fs))/fs
    results=[]
    for freq in (320,1000,2000,2800,3500,7000,9000,15000,17000,25000):
        # Deterministic 1bit density modulated stream compressed to counts/64.
        bits=np.clip(np.rint(32+4*np.sin(2*pi*freq*t)),0,64).astype(np.uint8)
        for poles in (0,2,3):
            samples=decimate(bits,poles=poles)
            alias=abs((freq+4000)%8000-4000)
            amp=phase_amp(samples,alias)
            results.append(dict(input_hz=freq,alias_hz=alias,poles=poles,output_samples=len(samples),rms=float(np.std(samples[1600:])),amp=amp,db=round(20*log10(max(amp,1.e-12)),3),peak=int(np.max(abs(samples.astype(int))))))
    # Add seeded 64bit binomial popcount noise.
    rng=np.random.default_rng(7718)
    t=np.arange(int(duration*fs))/fs
    p=np.clip(.5 + .015*np.sin(2*pi*1000*t) + .035*np.sin(2*pi*9000*t),0,1)
    bits=rng.binomial(64,p).astype(np.uint8)
    for poles in (0,2,3):
        x=decimate(bits,poles=poles)
        results.append(dict(input_hz='1k_and_9k_binomial',alias_hz=1000,poles=poles,output_samples=len(x),rms=float(np.std(x[1600:])),amp=phase_amp(x,1000),db=round(20*log10(max(phase_amp(x,1000),1e-12)),3),peak=int(np.max(abs(x.astype(int))))))
    return results


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--out',type=Path)
    args=p.parse_args()
    results=study()
    by={(r['input_hz'],r['poles']):r for r in results}
    for f in (320,1000,2000,2800,3500,7000,9000,15000,17000,25000):
        b=by[f,0]['amp'];changes=[round(20*log10(max(by[f,p]['amp'],1e-10)/max(b,1e-10)),1) for p in (2,3)]
        print(f'{f:5} Hz -> {by[f,0]["alias_hz"]:4} Hz: baseline amp {b:8.1f}; two-pole {changes[0]:6.1f} dB; three-pole {changes[1]:6.1f} dB')
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(dict(pdm_group_hz=FS,output_hz=OUT,popcount_per_group=64,filter='poles (0/2/3) of signed Q7 EMA at alpha1/4 before fractional boxcar decimation',results=results),indent=2)+'\n')
        print('STUDY',args.out)

if __name__=='__main__':main()
