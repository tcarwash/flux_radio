#!/usr/bin/env python3
"""Offline frequency/noise analysis of Sotto monitor PCM WAV recordings.

Read-only for source WAVs. Writes only derived metadata to a new --json file.
Use this *after* a physical recording; never opens serial, SDR, radio or RF.

Decoded WAV is the output of a lossy pitch-based speech vocoder. Dominant
spectral peak != perceived pitch. WAV metadata cannot prove the GP0 ADC clock.
Baseline-subtracted SNR is approximate and assumes stationary unrelated noise.
"""
from __future__ import annotations
import argparse
import json
from math import log10
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from scipy.signal import find_peaks

MIN_TONE_HZ=70.0
MAX_TONE_HZ=3900.0


def load_wav(path:Path):
    fs,y=wavfile.read(path)
    if fs<=0:raise ValueError(f"invalid WAV sample rate: {fs}")
    if y.ndim==2:y=y.astype(np.float64).mean(axis=1)
    if y.ndim!=1:raise ValueError("expected mono or stereo WAV")
    if np.issubdtype(y.dtype,np.integer):
        x=y.astype(np.float64)/max(abs(np.iinfo(y.dtype).min),np.iinfo(y.dtype).max)
    else:
        x=y.astype(np.float64)
    if not np.isfinite(x).all():raise ValueError("nonfinite WAV values")
    return fs,x


def dbfs(rms):
    return round(float(20*log10(max(rms,1.e-12))),3)


def peak_components(x,fs,*,start_seconds=.4,seconds=2.,top=20):
    start=round(start_seconds*fs)
    end=min(len(x),start+round(seconds*fs))
    if end-start < fs//2:raise ValueError("not enough PCM for stable spectral estimate")
    a=x[start:end]-np.mean(x[start:end])
    w=np.hanning(len(a))
    nfft=max(32768,1<<(len(a)-1).bit_length())
    if nfft>262144:nfft=262144
    spec=np.abs(np.fft.rfft(a*w,nfft))*2/np.sum(w)
    hz=np.fft.rfftfreq(nfft,1/fs)
    bins=(hz>=MIN_TONE_HZ)&(hz<=min(MAX_TONE_HZ,fs/2-.5))
    if not bins.any():raise ValueError("sample rate too small for analysis band")
    peak=int(np.argmax(spec*bins))
    step=hz[1]-hz[0]
    pks,_=find_peaks(spec,distance=max(1,round(6/step)),prominence=.004)
    pks=sorted((j for j in pks if bins[j]),key=lambda j:spec[j],reverse=True)[:top]
    return dict(
        dominant_hz=round(float(hz[peak]),2),
        dominant_amplitude=round(float(spec[peak]),5),
        spectral_peaks=[dict(hz=round(float(hz[j]),2),amplitude=round(float(spec[j]),5)) for j in pks],
        spectrum_start_seconds=start_seconds,
        spectrum_duration_seconds=round((end-start)/fs,3),
    )


def analyze(path:Path,*,expected_hz=None):
    fs,x=load_wav(path)
    if len(x)<fs:raise ValueError("recording must contain at least one second")
    rms=float(np.sqrt(np.mean(x*x)))
    result=dict(file_basename=path.name,rate_hz=fs,samples=len(x),
        duration_seconds=round(len(x)/fs,4),rms_dbfs=dbfs(rms),
        rms_linear=round(rms,7),peak_linear=round(float(np.max(np.abs(x))),6),
        dc_offset=round(float(np.mean(x)),7),
        clipped_samples=int(np.count_nonzero(np.abs(x)>=.999)),
        near_zero_fraction=round(float(np.mean(np.abs(x)<.005)),6))
    result.update(peak_components(x,fs))
    if expected_hz is not None:
        result["nominal_input_hz"]=expected_hz
        result["dominant_to_nominal_ratio"]=round(result["dominant_hz"]/expected_hz,5)
    return result


def compare(directory:Path):
    files=[
        ("250hz_at_.1vpp.wav",250),
        ("500hz_at_.1vpp.wav",500),
        ("1khz_at_.1vpp.wav",1000),
        ("2k_at_.1vpp.wav",2000),
        ("gen_off.wav",None),
    ]
    rows=[analyze(directory/name,expected_hz=f) for name,f in files]
    zero=rows[-1]
    noise=zero["rms_linear"]**2
    for r in rows[:-1]:
        p=r["rms_linear"]**2
        r["above_off_baseline_db"]=round(10*log10(p/noise),2) if noise>0 else None
        r["noise_subtracted_snr_db"]=round(10*log10(max(p-noise,1.e-15)/noise),2) if noise>0 else None
    return dict(schema=1,scope="decoded Sotto monitor WAV output only; no RF or GP0 raw input",
        statistical_warning="Generator-off WAV is decoded noise, not proven ADC input. Baseline subtraction assumes stationary uncorrelated noise; no RF packet continuity data was available. Decoded tonal frequency can be pitch-quantized or a harmonic.",
        original_wav_files_preserved=True,source_wav_bytes_included=False,rows=rows)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--directory",type=Path,required=True,help="directory containing five named source WAVs")
    ap.add_argument("--json",type=Path,required=True,help="new output file; refuses overwrite")
    a=ap.parse_args()
    r=compare(a.directory)
    with a.json.open("x") as f:
        f.write(json.dumps(r,indent=2,sort_keys=True)+"\n")
    for row in r["rows"]:
        print(f'{row["file_basename"]:22} rms={row["rms_dbfs"]:7.2f}dBFS  freq={row["dominant_hz"]:8.2f}Hz  samples={row["samples"]:,}')


if __name__=="__main__":main()
