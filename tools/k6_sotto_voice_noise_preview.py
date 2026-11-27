#!/usr/bin/env python3
"""OFFLINE-only soft-noise preview for saved 8 kHz Sotto-decoded speech.

Produces three NEW WAVs for subjective listening: conservative bandpass;
soft time/frequency-smoothed spectral attenuation; stronger attenuation.
This is a receiver-side presentation experiment, not a K6 encoder/vocoder or
high-rate GP0 anti-alias patch. No reference clean speech exists here, so
cannot objectively claim improved intelligibility or SNR.
Do not feed these to a radio or replace original evidence.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import numpy as np
from scipy.io import wavfile
from scipy.ndimage import gaussian_filter,median_filter
from scipy.signal import butter,sosfiltfilt,stft,istft,welch

FS=8000


def read_8k(path):
    fs,x=wavfile.read(path)
    if fs!=FS or x.ndim!=1 or x.dtype!=np.int16 or len(x)<FS//4:
        raise ValueError('expected >=250ms of 8kHz mono signed16 PCM')
    return x.astype(np.float64)/32768


def bandpass(x):
    """Zero-phase conservative voice presentation band 200..3300Hz."""
    s=butter(3,[200,3300],fs=FS,btype='bandpass',output='sos')
    return sosfiltfilt(s,x)


def spectral_soft(x,*,floor=0.45,quantile=.15):
    if not .2<=floor<=.85 or not .05<=quantile<=.4:
        raise ValueError('soft-noise parameters out of bounded range')
    ff,tt,Z=stft(x,fs=FS,nperseg=512,noverlap=384,boundary='zeros',padded=True)
    power=abs(Z)**2
    # Use a low per-frequency time quantile as a deliberately rough
    # stationary noise proxy. This cannot isolate codec artifacts or speech;
    # it is never a physical mic noise PSD measurement.
    baseline=np.quantile(power,quantile,axis=1)
    baseline=median_filter(baseline,size=5,mode='nearest')
    spectral_floor=float(max(np.median(power),1e-12))*1e-4
    raw=np.maximum(1-baseline[:,None]/(power+spectral_floor),floor**2)
    gain=gaussian_filter(np.sqrt(raw),sigma=(1.1,1.5),mode='nearest')
    gain=np.clip(gain,floor,1.)
    result=istft(Z*gain,fs=FS,nperseg=512,noverlap=384,
                 input_onesided=True,boundary=True)[1]
    return result[:len(x)]


def prepare(x):
    y=bandpass(x)
    return {
      'bandpass':y,
      'soft':spectral_soft(y,floor=.55),
      'strong':spectral_soft(y,floor=.28),
    }


def metrics(x):
    x=np.asarray(x,dtype=float)
    f,psd=welch(x,fs=FS,nperseg=256,noverlap=128)
    e=np.trapezoid(psd,f)
    high=np.trapezoid(psd[(f>=3000)&(f<=4000)],f[(f>=3000)&(f<=4000)])
    return {'rms':round(float(np.std(x)),6),
            'peak':round(float(max(abs(x))),6),
            'fraction_power_3k_4k':round(float(high/max(e,1e-15)),4),
            'samples':len(x)}


def save_previews(input_wav:Path,output_dir:Path,*,level_match_rms=False):
    if not output_dir.is_dir():raise ValueError('output directory absent')
    names=['bandpass','soft','strong']
    for name in names:
        if (output_dir/(name+'-preview.wav')).exists():raise FileExistsError('refusing overwrite existing preview')
    manifest=output_dir/'preview.json'
    if manifest.exists():raise FileExistsError('refusing overwrite existing manifest')
    x=read_8k(input_wav)
    variants=prepare(x)
    record={'schema':1,'offline_only':True,'sample_rate_hz':FS,
            'level_matched_to_original_rms':bool(level_match_rms),
            'input_filename':input_wav.name,
            'input_sha256':hashlib.sha256(input_wav.read_bytes()).hexdigest(),
            'preview_quality_not_proven':True,
            'purpose':'Subjective A/B only; receiver-side output treatment, not source/noise characterization or a fixed codec',
            'original':metrics(x),'variants':{}}
    for name,y in variants.items():
        if len(y)!=len(x):raise ValueError('output length mismatch')
        gain=1.
        if level_match_rms:
            raw_rms=float(np.std(x))
            new_rms=float(np.std(y))
            gain=min(1.8,raw_rms/max(new_rms,1e-12))
            # Preserve samples rather than silently hard-clipping.
            if float(np.max(abs(y*gain)))>=.999:
                gain=min(gain,.999/float(np.max(abs(y))))
        data=np.clip(np.rint(y*gain*32768),-32768,32767).astype(np.int16)
        outfile=output_dir/(name+'-preview.wav')
        with outfile.open('xb') as out:
            from scipy.io.wavfile import write
            write(out,FS,data)
        record['variants'][name]={'file':outfile.name,**metrics(data.astype(float)/32768),
                 'applied_loudness_gain_db':round(float(20*np.log10(gain)),3),
                 'clipped_samples':int(np.count_nonzero(abs(data.astype(int))>=32767))}
    with manifest.open('x') as out:out.write(json.dumps(record,indent=2,sort_keys=True)+'\n')
    return record


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input-wav',type=Path,required=True)
    ap.add_argument('--output-dir',type=Path,required=True)
    ap.add_argument('--level-match-rms',action='store_true',
                    help='match overall source RMS with gain capped at 1.8x and no clipping')
    args=ap.parse_args()
    r=save_previews(args.input_wav,args.output_dir,level_match_rms=args.level_match_rms)
    print(json.dumps(r,indent=2,sort_keys=True))


if __name__=='__main__':main()
