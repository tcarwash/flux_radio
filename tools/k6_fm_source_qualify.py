#!/usr/bin/env python3
"""OFFLINE FM microphone/front-end qualification from fixed-gain SDR recordings.

A strong carrier quiets FM receiver hiss, but it doesn't erase transmitter's
microphone noise. This script ONLY reads saved complex64 IQ and outputs derived
metrics, never opens an SDR, programs a radio, or keys PTT.

Experimental controls must be recorded with exactly the SAME SDR manual gain,
RF coupling, center, rate, and signal processing:
  * no K6 RF carrier (SDR receiver noise)
  * K6 FM carrier ON with mic input quiet (source + RF/SDR noise)
  * K6 FM carrier ON with calibrated external 1kHz microphone tone
  * optional K6 internally generated analog FM tone, if independently proven
    to bypass MICIN, as an empirical RF/SDR+modulator noise control.

An internal tone may use a DIFFERENT RFIC modulation path: don't subtract
powers across mismatched deviations or filtering without calibration.
This is not an FM SINAD compliance meter, since the 1kHz residual may
include distortion and receiver/equalizer effects. Reports are in demodulated
Hz-deviation units after identical 250..3400Hz audio bandpass.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import butter, resample_poly, sosfiltfilt

OUT_FS=8000
AUDIO_LO_HZ=250
AUDIO_HI_HZ=3400


def load_iq(path: Path, fs: int, *, start_s: float=0, length_s: float=3.) -> np.ndarray:
    if not path.is_file() or path.stat().st_size%8:
        raise ValueError("missing or non-complex64-aligned IQ")
    if fs not in (128_000,1_024_000):
        raise ValueError("supported IQ rates: 128000 or 1024000 Hz")
    if not math.isfinite(start_s) or not math.isfinite(length_s) or start_s<0 or not .25<=length_s<=15:
        raise ValueError("invalid bounded interval")
    nbytes=path.stat().st_size//8
    a=int(round(start_s*fs));b=int(round((start_s+length_s)*fs))
    if b>nbytes:
        raise ValueError("requested interval beyond saved IQ")
    data=np.memmap(path,dtype=np.complex64,mode="r",offset=a*8,shape=(b-a,))
    iq=np.asarray(data,dtype=np.complex64)
    if not np.isfinite(iq.real).all() or not np.isfinite(iq.imag).all():
        raise ValueError("nonfinite IQ")
    return iq


def fm_discriminator(iq: np.ndarray, fs: int, *, carrier_if_hz: float) -> tuple[np.ndarray, dict]:
    """Demod into Hz of instantaneous frequency deviation, fixed front-end."""
    z=np.asarray(iq,dtype=np.complex64)
    if z.ndim!=1 or len(z)<fs//4 or fs not in (128_000,1_024_000):
        raise ValueError("need >=0.25s of sampled complex64 IQ")
    if not np.isfinite(carrier_if_hz) or abs(carrier_if_hz)>fs/2-20_000:
        raise ValueError("carrier IF outside usable Nyquist margin")
    power=float(np.mean(np.abs(z)**2))
    # Mix nominal RF carrier to DC and then downsample; FM absolute carrier
    # frequency error within ~2kHz is harmless to the phase DIFFERENCE.
    t=np.arange(len(z),dtype=np.float64)/fs
    bb=z*np.exp(-2j*np.pi*carrier_if_hz*t).astype(np.complex64)
    decim=fs//32_000
    a=resample_poly(bb,1,decim).astype(np.complex64)
    sos=butter(5,9500,fs=32000,output="sos")
    filtered=sosfiltfilt(sos,a.real)+1j*sosfiltfilt(sos,a.imag)
    # Differential phase avoids unwrap error on valid high-CNR FM.
    instantaneous=np.angle(filtered[1:]*filtered[:-1].conj())
    frequency=instantaneous*32000/(2*np.pi)
    out=resample_poly(np.r_[frequency[0],frequency],1,4)
    return out.astype(np.float64),{
        "rf_iq_mean_power":power,
        "input_iq_sample_rate":fs,
        "demod_audio_rate":OUT_FS,
        "carrier_if_hz":carrier_if_hz,
        "rf_decimation_factor":decim,
        "audio_band_hz":[AUDIO_LO_HZ,AUDIO_HI_HZ],
    }


def audio_band(x: np.ndarray) -> np.ndarray:
    return sosfiltfilt(butter(4,[AUDIO_LO_HZ,AUDIO_HI_HZ],
                                  btype="bandpass",fs=OUT_FS,output="sos"),x)


def analyze_audio(x: np.ndarray, *, tone_hz: float=1000, trim_s: float=.15) -> dict:
    x=np.asarray(x,dtype=float)
    if x.ndim!=1 or len(x)<OUT_FS//4 or not np.isfinite(x).all():
        raise ValueError("too few finite mono audio samples")
    if not 400<=tone_hz<=3000:
        raise ValueError("tone frequency outside qualified band")
    cut=round(trim_s*OUT_FS)
    if cut<0 or len(x)-cut<OUT_FS//10:
        raise ValueError("invalid settling time")
    y=audio_band(x)[cut:]
    # Use a consistent in-band measurement, do not compare unmapped IF noise.
    t=np.arange(len(y))/OUT_FS
    def solve(hz):
        mat=np.column_stack((np.sin(2*np.pi*hz*t),np.cos(2*np.pi*hz*t),np.ones(len(t))))
        co=np.linalg.lstsq(mat,y,rcond=None)[0]
        resid=y-mat@co
        return float(np.hypot(*co[:2])),float(np.std(resid)),float(np.std(y))
    opt=minimize_scalar(lambda hz:solve(hz)[1]**2,
                        bounds=(tone_hz*.97,tone_hz*1.03),method="bounded")
    amp,res,total=solve(opt.x)
    snr_db=20*math.log10(max(amp,1e-10)/(math.sqrt(2)*max(res,1e-10)))
    return dict(
        measured_tone_hz=round(float(opt.x),4),
        tone_peak_deviation_hz=round(amp,5),
        tone_residual_rms_deviation_hz=round(res,5),
        in_band_rms_deviation_hz=round(total,5),
        tone_to_residual_db=round(snr_db,3),
        observed_audio_seconds=round(len(y)/OUT_FS,4),
        note="Fitted sine residual includes source, FM distortion, RF/SDR noise and drift; not SINAD"
    )


def analyze_iq(path: Path,fs:int,if_hz:float,start:float,length:float,tone:float):
    iq=load_iq(path,fs,start_s=start,length_s=length)
    pcm,m=fm_discriminator(iq,fs,carrier_if_hz=if_hz)
    return {**m,**analyze_audio(pcm,tone_hz=tone),
            "source_iq_bytes":path.stat().st_size}


def compare_stages(no_carrier, silent, mic_tone, internal=None) -> dict:
    """Check carrier quieting and source noise, NOT assert causal isolation."""
    p0=float(no_carrier["rf_iq_mean_power"])
    pc=float(silent["rf_iq_mean_power"])
    pt=float(mic_tone["rf_iq_mean_power"])
    if p0<=0 or pc<=0 or pt<=0:
        raise ValueError("nonpositive RF power")
    # All controls need same fixed manual SDR RF gain; user must verify.
    carrier_rise=10*math.log10(pc/p0)
    match=10*math.log10(pt/pc)
    out={
      "carrier_over_no_carrier_rf_power_db":round(carrier_rise,3),
      "mic_tone_vs_silent_carrier_power_delta_db":round(match,3),
      "no_carrier_demod_audio_rms_hz":no_carrier["in_band_rms_deviation_hz"],
      "strong_carrier_mic_silent_demod_audio_rms_hz":silent["in_band_rms_deviation_hz"],
      "mic_tone_demod_tone_to_residual_db":mic_tone["tone_to_residual_db"],
      "strong_carrier_quieting_rms_db":round(20*math.log10(
          max(no_carrier["in_band_rms_deviation_hz"],1e-9)/
          max(silent["in_band_rms_deviation_hz"],1e-9)),3),
      "interpretation":["Carrier/no-carrier is useful for quieting but no-carrier FM discriminator output is not an intrinsic noise reference",
        "At matched strong carrier/receiver gain, microphone-path residual can be compared with internally generated tone residual",
        "Uncorrelated power subtraction is only justified if internal and microphone tone excursions, RF paths and noise are matched",
        "High RF power or audio quieting alone does NOT prove audio-source quality"],
      "manual_sdr_gain_fixed_and_dummy_load_required":True,
    }
    if internal:
        out["internal_tone_to_residual_db"]=internal["tone_to_residual_db"]
        out["extra_tone_residual_vs_internal_db"]=round(
            mic_tone["tone_to_residual_db"]-internal["tone_to_residual_db"],3)
        out["warning_internal_tone_route_must_be_verified_bypassing_MICIN"]=True
    return out


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iq-rate",type=int,choices=(128_000,1_024_000),required=True)
    parser.add_argument("--carrier-if-hz",type=float,required=True,
                        help="actual RF carrier offset from SDR IQ center AFTER any prior digital tuning")
    parser.add_argument("--tone-hz",type=float,default=1000)
    parser.add_argument("--start-s",type=float,default=1.0)
    parser.add_argument("--length-s",type=float,default=3.)
    parser.add_argument("--no-carrier",type=Path,required=True)
    parser.add_argument("--silent-carrier",type=Path,required=True)
    parser.add_argument("--mic-tone",type=Path,required=True)
    parser.add_argument("--internal-tone",type=Path)
    parser.add_argument("--out-json",type=Path,required=True)
    a=parser.parse_args()
    if a.out_json.exists():parser.error("refusing to overwrite result")
    args=dict(fs=a.iq_rate,if_hz=a.carrier_if_hz,start=a.start_s,
              length=a.length_s,tone=a.tone_hz)
    files={"no_carrier":a.no_carrier,"silent_carrier":a.silent_carrier,"mic_tone":a.mic_tone}
    if a.internal_tone:files["internal_tone"]=a.internal_tone
    if a.out_json in files.values():parser.error("output overlaps source")
    measurements={key:analyze_iq(path,**args) for key,path in files.items()}
    results=compare_stages(measurements["no_carrier"],measurements["silent_carrier"],
                          measurements["mic_tone"],measurements.get("internal_tone"))
    report={"schema":1,"offline_only":True,"radio_control":False,
            "manual_fixed_gain_required":True,"rx_conditions":measurements,
            "comparison":results}
    with a.out_json.open("x") as f:f.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
    print(json.dumps(results,indent=2,sort_keys=True))


if __name__=="__main__":main()
