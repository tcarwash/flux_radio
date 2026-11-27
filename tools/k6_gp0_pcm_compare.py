#!/usr/bin/env python3
"""Offline *pre-vocoder* GP0 PCM characterization of two guarded captures.

Compare generator-OFF and fixed nominal tone WAVs recovered by
k6_gp0_pcm_air.py. This runs on the K6's signed 8kHz PCM **before** Sotto.
It does not decode Sotto, open a radio/SDR, or alter original WAVs.

A measured capture duration in 100us ticks estimates the true emitted PCM
cadence. If the physical generator is on-frequency, compare that clock against
the WAV's spectral pitch: the signed PCM sample clock is NOT necessarily 8kHz.
Only 200ms is captured; noise PSD confidence is limited. Already folded PDM
aliasing cannot be uniquely identified from 8kHz PCM alone.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import math
from scipy.io import wavfile
from scipy.signal import find_peaks
import numpy as np

FS=8000
N=1600


def read_capture(wav_path:Path,metadata_path:Path):
    fs,s=wavfile.read(wav_path)
    if fs!=FS or s.ndim!=1 or len(s)!=N or s.dtype!=np.int16:
        raise ValueError("Expected exactly 1600 s16 PCM samples at header 8000 Hz")
    metadata=json.loads(metadata_path.read_text())
    if metadata["sample_rate_hz"]!=FS or metadata["sample_count"]!=N:
        raise ValueError("PCM metadata sample rate/count mismatch")
    ticks=metadata["capture_duration_ticks_100us"]
    if not isinstance(ticks,int) or not (1500<=ticks<=2800):
        raise ValueError("Nonphysical PCM capture interval (100us ticks)")
    if metadata["duration_if_labeled_8k_ms"]!=200:
        raise ValueError("Expected exact 200 ms nominal sample duration")
    import hashlib
    if hashlib.sha256(s.tobytes()).hexdigest()!=metadata["pcm_sha256"]:
        raise ValueError("WAV PCM hash does not match independently verified RF record")
    return s.astype(np.float64)/32768,metadata


def spectral_peak(x,*,target_hz):
    if len(x)!=N or not 200<=target_hz<=3500:
        raise ValueError("Invalid tone analysis input")
    xx=x-np.mean(x)
    w=np.hanning(N)
    nfft=131072
    hz=np.fft.rfftfreq(nfft,1/FS)
    spec=np.abs(np.fft.rfft(xx*w,nfft))*2/w.sum()
    # Large search window is intentional: a 4-6% physical sample-rate offset
    # is the hypothesis, not a reason to fail before measuring it.
    candidates=np.where((hz>=target_hz*.85)&(hz<=target_hz*1.15))[0]
    peak=candidates[np.argmax(spec[candidates])]
    freq=float(hz[peak])
    # Least-squares fit at measured spectral peak: a*sin + b*cos + DC.
    t=np.arange(N)/FS
    xbasis=np.column_stack((np.sin(2*np.pi*freq*t),
                            np.cos(2*np.pi*freq*t),
                            np.ones(N)))
    coeff=np.linalg.lstsq(xbasis,x,rcond=None)[0]
    predicted=xbasis@coeff
    resid=x-predicted
    tone_amp=math.hypot(float(coeff[0]),float(coeff[1]))
    return dict(decoded_precodec_peak_hz=round(freq,3),
                tone_amplitude_peak_ratio=round(tone_amp,7),
                tone_component_rms_ratio=round(tone_amp/math.sqrt(2),7),
                residual_after_sine_fit_rms_ratio=round(float(np.sqrt(np.mean(resid*resid))),7),
                measured_peak_ratio_to_nominal=round(freq/target_hz,6))


def describe(x):
    xx=x-np.mean(x)
    return dict(rms_ratio=round(float(np.sqrt(np.mean(xx*xx))),7),
                peak_ratio=round(float(np.max(abs(xx))),7),
                mean_ratio=round(float(np.mean(x)),7),
                near_fullscale_samples=int(np.count_nonzero(abs(x)>=.999)),
                exact_zero_fraction=round(float(np.mean(x==0)),6))


def compare(off_wav,off_json,on_wav,on_json,nominal_tone_hz):
    if not math.isfinite(nominal_tone_hz) or not 200<=nominal_tone_hz<=3500:
        raise ValueError("Only 200..3500Hz known tone stimulus is supported")
    off,off_meta=read_capture(off_wav,off_json)
    on,on_meta=read_capture(on_wav,on_json)
    if off_meta["token"]==on_meta["token"]:
        raise ValueError("Expected distinct generator-off and tone-on sessions")
    off_clk=16000000/off_meta["capture_duration_ticks_100us"]
    on_clk=16000000/on_meta["capture_duration_ticks_100us"]
    tone=spectral_peak(on,target_hz=nominal_tone_hz)
    tone_rms=tone["tone_component_rms_ratio"]
    off_rms=describe(off)["rms_ratio"]
    if off_rms==0:ratio=None
    else:ratio=round(20*math.log10(max(tone_rms,1.e-15)/off_rms),2)
    return dict(schema=1,offline_pre_vocoder=True,
                nominal_generator_hz=nominal_tone_hz,
                original_wavs_unchanged=True,
                off_file_basename=off_wav.name,on_file_basename=on_wav.name,
                off_session_token=off_meta["token"],on_session_token=on_meta["token"],
                off_capture_samples_per_wall_second=round(off_clk,3),
                on_capture_samples_per_wall_second=round(on_clk,3),
                input_frequency_inferred_from_timing_hz=round(
                    tone["decoded_precodec_peak_hz"]*on_clk/FS,3),
                generator_off=describe(off),generator_on=describe(on),
                tone=tone,tone_to_off_noise_rms_db=ratio,
                caveats=[
                  "200ms PCM segment cannot establish a long-term drift or stationarity",
                  "Ticks clock is radio MCU uptime reference; measured PCM cadence depends on its accuracy",
                  "Sotto and RF decoder are bypassed; their own distortion cannot be measured by this file alone",
                  "8k PCM has already decimated the GP0 PDM source; folded aliases cannot be unwrapped",
                  "Tone-fit residual includes phase jitter and distortion, not just stochastic noise",
                ])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for flag in ("off-wav","off-json","on-wav","on-json"):
        p.add_argument("--"+flag,type=Path,required=True)
    p.add_argument("--nominal-tone-hz",type=float,required=True)
    p.add_argument("--json-out",type=Path,required=True)
    a=p.parse_args()
    r=compare(a.off_wav,a.off_json,a.on_wav,a.on_json,a.nominal_tone_hz)
    with a.json_out.open("x") as out:out.write(json.dumps(r,indent=2,sort_keys=True)+"\n")
    print(json.dumps(r,indent=2,sort_keys=True))


if __name__=="__main__":main()
