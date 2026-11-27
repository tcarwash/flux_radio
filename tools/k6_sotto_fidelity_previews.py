#!/usr/bin/env python3
"""OFFLINE listening-only Sotto output treatments, including consonant-preserving.

Sotto decoded speech is downstream of GP0 and the vocoder. These variants
cannot diagnose GP0 aliasing, and must not be represented as input-side fixes.
No clean paired speech reference exists; readability is a HUMAN listening call.
Noncausal offline SciPy is not real-time K6 firmware. Outputs remain PRIVATE.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.io import wavfile
from scipy.ndimage import gaussian_filter,median_filter
from scipy.signal import butter,sosfiltfilt,stft,istft,welch

FS=8000
VARIANTS=("original","gentle","presence","focused","strong")


def load_speech(path:Path):
    fs,x=wavfile.read(path)
    if fs!=FS or x.dtype!=np.int16 or x.ndim!=1 or len(x)<FS//2:
        raise ValueError("need >=0.5 second mono signed16 8kHz WAV")
    return x.astype(np.float64)/32768


def voice_band(x:np.ndarray, low:float,high:float):
    sos=butter(3,[low,high],fs=FS,output="sos",btype="bandpass")
    return sosfiltfilt(sos,x)


def masked_noise_reduction(x:np.ndarray,*,floor:float,noise_quantile:float,
                           protect_presence:bool=False):
    if not .25<=floor<=.9 or not .05<=noise_quantile<=.3:
        raise ValueError("bounded denoise settings only")
    freqs,tt,Z=stft(x,fs=FS,nperseg=512,noverlap=384,boundary="zeros",padded=True)
    P=np.abs(Z)**2
    noise=np.quantile(P,noise_quantile,axis=1)
    noise=median_filter(noise,size=5,mode="nearest")
    raw=np.maximum(1.0-noise[:,None]/(P+1e-12),floor**2)
    gain=gaussian_filter(np.sqrt(raw),sigma=(1.0,1.4),mode="nearest")
    if protect_presence:
        # Do not gate away quiet fricatives near 1.7–3.5kHz as aggressively.
        band=np.clip((freqs-1500)/700,0,1)*np.clip((3700-freqs)/450,0,1)
        fmin=floor+(0.72-floor)*band[:,None]
        gain=np.maximum(gain,fmin)
    gain=np.clip(gain,floor,1.0)
    y=istft(Z*gain,fs=FS,nperseg=512,noverlap=384,
            input_onesided=True,boundary=True)[1]
    return y[:len(x)]


def presence_shelf(x:np.ndarray):
    freqs,tt,Z=stft(x,fs=FS,nperseg=512,noverlap=384,boundary="zeros",padded=True)
    # Max +1.4dB in lower consonant band, no boost at the 3.7-4k Nyquist edge.
    window=np.clip((freqs-1100)/750,0,1)*np.clip((3500-freqs)/450,0,1)
    gain=1+.175*window
    y=istft(Z*gain[:,None],fs=FS,nperseg=512,noverlap=384,
            input_onesided=True,boundary=True)[1]
    return y[:len(x)]


def variants(x):
    v={
        "original":x.copy(),
        "gentle":masked_noise_reduction(voice_band(x,140,3650),
                     floor=.70,noise_quantile=.10,protect_presence=True),
        "focused":masked_noise_reduction(voice_band(x,190,3350),
                     floor=.50,noise_quantile=.15,protect_presence=True),
        "strong":masked_noise_reduction(voice_band(x,220,3150),
                     floor=.30,noise_quantile=.20,protect_presence=False),
    }
    v["presence"]=presence_shelf(v["gentle"])
    return {name:v[name] for name in VARIANTS}


def metrics(x):
    freqs,p=welch(x,fs=FS,nperseg=512,noverlap=256)
    power=np.trapezoid(p,freqs)
    out={"rms":float(np.std(x)),"peak":float(max(abs(x))),
         "duration_s":len(x)/FS,"n_samples":len(x)}
    for lo,hi in ((150,500),(500,1500),(1500,3000),(3000,4000)):
        sel=(freqs>=lo)&(freqs<=hi)
        out[f"fraction_energy_{lo}_{hi}hz"]=float(np.trapezoid(p[sel],freqs[sel])/max(power,1e-15))
    return out


def normalize_for_audition(x,original_rms):
    gain=min(1.8,float(original_rms)/max(float(np.std(x)),1e-12))
    if np.max(abs(x*gain))>.98:
        gain=min(gain,.98/np.max(abs(x)))
    encoded=np.clip(np.rint(x*gain*32768),-32768,32767).astype(np.int16)
    return encoded,float(gain)


def save(input_wav:Path,output_dir:Path):
    if not output_dir.is_dir():raise ValueError("output dir must exist")
    for name in VARIANTS:
        if (output_dir/(name+".wav")).exists():raise FileExistsError("refuse overwrite original audition")
    if (output_dir/"manifest.json").exists():raise FileExistsError("refuse overwrite manifest")
    x=load_speech(input_wav)
    original_rms=float(np.std(x))
    rec={"offline_only":True,"no_radio_modifications":True,"subjective_readability_not_proven":True,
        "input_file":input_wav.name,"input_sha256":hashlib.sha256(input_wav.read_bytes()).hexdigest(),
        "sample_rate":FS,"original_metrics":metrics(x),"variants":{}}
    for name,y in variants(x).items():
        assert len(y)==len(x) and np.isfinite(y).all()
        output,gain=normalize_for_audition(y,original_rms)
        with (output_dir/(name+".wav")).open("xb") as stream:
            import io
            b=io.BytesIO();wavfile.write(b,FS,output);stream.write(b.getvalue())
        yy=output.astype(float)/32768
        rec["variants"][name]={"wav":name+".wav","level_match_gain_db":round(20*np.log10(max(gain,1e-12)),3),
              "clipped_samples":int(np.count_nonzero(np.abs(output.astype(np.int32))>=32767)),
              **metrics(yy)}
    with (output_dir/"manifest.json").open("x") as stream:
        stream.write(json.dumps(rec,indent=2,sort_keys=True)+"\n")
    return rec


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-wav",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()
    rec=save(args.input_wav,args.output_dir)
    print("Input",rec["input_file"],"secs",rec["original_metrics"]["duration_s"])
    for name,r in rec["variants"].items():
        print(name,"rms",round(r["rms"],5),"peak",round(r["peak"],5),
              "3-4k",round(r["fraction_energy_3000_4000hz"],3),
              "clipped",r["clipped_samples"])
    print("Preview files are subjective A/B only; not tested for human intelligibility")

if __name__=="__main__":
    main()
