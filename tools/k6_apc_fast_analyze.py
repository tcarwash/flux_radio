#!/usr/bin/env python3
"""Offline only: characterize Flux FD6818B APC 80-100% RF envelope toggling.

The 760-ms nominal module toggles APC 80/100: 8 cycles at nominal 25 Hz,
8 cycles at nominal 50 Hz, 12 cycles at nominal 100 Hz, plus leading/trailing
80-ms 100% holds. Uses an uncalibrated air-coupled RTL-SDR; estimates
switching frequency and normalized carrier amplitude contrast, not PA watts,
RF purity, or phase continuity. NEVER opens serial or SDR hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

FS = 1_024_000
BIN = 512  # 0.5 ms
NFFT = 4096
CENTER_HZ = 433_750_000
RF_BAND = (-320_000, -240_000)
# Windows are EMPIRICAL capture-relative guards, from a 2026-11-22
# offline pilot analysis of the same bounded C module, accounting for
# firmware/module overhead. Not a portable actuator-timing specification.
GROUPS = (
    ("20-ms half-step", 25, .086, .402),
    ("10-ms half-step", 50, .415, .569),
    ("5-ms half-step", 100, .585, .695),
)


def analyze(path: Path) -> dict:
    path = Path(path)
    raw = np.load(path, mmap_mode="r", allow_pickle=False)
    if raw.ndim != 1 or raw.dtype != np.uint8 or len(raw) % 2 or not 1_000_000 <= len(raw) <= 30_000_000:
        raise ValueError("expected bounded, interleaved uint8 IQ")
    meta = json.loads(path.with_suffix(".json").read_text())
    if (meta.get("sample_rate_hz") != FS or
            meta.get("capture_center_hz") != CENTER_HZ):
        raise ValueError("unexpected recorded SDR settings")
    detail = meta.get("hardware_lab_transaction", {})
    result = detail.get("module_completion", {})
    if (not result.get("returned") or result.get("result") != 24 or
            not detail.get("original_module_restored") or
            not detail.get("original_radio_frequency_restored")):
        raise ValueError("no verified 24-result APC fast TX with successful radio restoration")
    checksum = hashlib.sha256(np.asarray(raw).tobytes()).hexdigest()
    if checksum != meta.get("iq_raw_u8_sha256"):
        raise ValueError("raw IQ checksum mismatch")

    frames = (len(raw) // 2) // NFFT
    v = np.asarray(raw[:frames*NFFT*2], dtype=np.float32)
    s = (v[::2]-127.5+1j*(v[1::2]-127.5)).reshape(frames,NFFT)
    f = np.fft.fftfreq(NFFT,1/FS)
    band=(f>RF_BAND[0])&(f<RF_BAND[1])
    spec=np.fft.fft(s*np.hanning(NFFT),axis=1)
    power=np.abs(spec[:,band]).max(axis=1)**2
    active=np.flatnonzero(power>4_000_000)
    if len(active)<110:
        raise ValueError("no sufficiently strong transmitted RF event")
    onset=float(active[0]*NFFT/FS)
    off=float((active[-1]+1)*NFFT/FS)
    if not .71<=off-onset<=.83:
        raise ValueError("RF burst length inconsistent with bounded APC-fast program")

    # Estimate RF carrier from final 100% hold after all fast toggles.
    length=65536
    at=int((off-.080)*FS)
    if at<0 or at+length>=len(raw)//2:
        raise ValueError("final carrier window outside archive")
    u=np.asarray(raw[2*at:2*(at+length)],dtype=np.float32)
    carrier=u[::2]-127.5+1j*(u[1::2]-127.5)
    ff=np.fft.fftfreq(length,1/FS)
    ps=np.abs(np.fft.fft(carrier*np.hanning(length)))
    window=(ff>RF_BAND[0])&(ff<RF_BAND[1])
    f0=float(ff[np.flatnonzero(window)[np.argmax(ps[window])]])

    # Coherent carrier magnitude at 0.5-ms resolution; local baseband
    # oscillator phase is compensated, then bins are averaged COMPLEXLY.
    start=max(0,int((onset-.012)*FS))
    end=min(len(raw)//2,int((off+.012)*FS))
    u=np.asarray(raw[2*start:2*end],dtype=np.float32)
    z=u[::2]-127.5+1j*(u[1::2]-127.5)
    sample=np.arange(len(z),dtype=np.float64)+start
    z*=np.exp(-2j*np.pi*f0/FS*sample)
    n=z.size//BIN
    z=z[:n*BIN].reshape(n,BIN)
    envelope=np.abs(z.mean(axis=1))
    t=(start+np.arange(n)*BIN)/FS-onset

    high=(t>.025)&(t<.065)
    full=float(np.median(envelope[high]))
    if full<=0:raise ValueError("no initial 100% carrier reference")
    groups=[]
    for name,nominal,lo,hi in GROUPS:
        selected=(t>=lo)&(t<=hi)
        tt=t[selected];y=envelope[selected]
        if len(y)<100:
            raise ValueError(f"insufficient samples for {name}")
        grid=np.linspace(nominal*.70,nominal*1.35,600)
        amps=np.empty(len(grid),dtype=np.float64)
        for i,rate in enumerate(grid):
            phase=2*np.pi*rate*tt
            X=np.column_stack([np.ones(len(tt)),tt-tt[0],np.cos(phase),np.sin(phase)])
            b=np.linalg.lstsq(X,y,rcond=None)[0]
            amps[i]=np.hypot(b[2],b[3])
        best=int(np.argmax(amps))
        # For an ideal 50%-duty high/low square wave, fundamental sinusoid
        # amplitude = 2/pi * absolute difference between plateau amplitudes.
        swing=float(amps[best])*np.pi/2
        groups.append({
            "name": name,
            "nominal_full_cycle_hz":nominal,
            "measured_peak_full_cycle_hz":round(float(grid[best]),2),
            "measured_fundamental_amplitude_adc":round(float(amps[best]),4),
            "equivalent_high_low_amplitude_swing_adc":round(swing,4),
            "swing_as_percent_of_full_carrier":round(100*swing/full,2),
            "empirical_time_window_seconds":[lo,hi],
            "window_samples":int(len(y)),
        })
    baseline=groups[0]["equivalent_high_low_amplitude_swing_adc"]
    for g in groups:
        g["swing_relative_to_20ms_percent"]=round(
            100*g["equivalent_high_low_amplitude_swing_adc"]/baseline,2)
    return {
        "analysis_version":1,
        "analysis":"offline fixed-gain air-coupled SDR, no calibrated power/emissions/phase claims",
        "source_path":str(path),
        "raw_iq_sha256_verified":checksum,
        "nominal_rf_hz":433_500_000,
        "apparent_rf_hz_from_uncalibrated_sdr":round(CENTER_HZ+f0,3),
        "onset_seconds":round(onset,4),
        "off_seconds":round(off,4),
        "duration_seconds":round(off-onset,4),
        "baseline_full_carrier_adc":round(full,4),
        "module_succeeded":True,
        "original_module_and_vfo_restored":True,
        "warnings":[
            "Window bounds empirical for this firmware+module; not timestamps of register writes",
            "Full cycle Hz is half the register-update rate for alternating high/low states",
            "Observed rate can be reduced by module scheduling overhead as well as PA dynamics",
            "Fundamental square-wave swing assumes approximately 50% duty and steady periodic update",
            "0.5-ms coherent SDR estimates do not prove sub-millisecond PA attack/release",
            "No hardware RF power calibration, harmonic/spectral mask, or phase continuity measurement",
        ],
        "groups":groups,
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("iq",type=Path,nargs="+")
    p.add_argument("--write-reports",action="store_true")
    args=p.parse_args()
    for path in args.iq:
        result=analyze(path)
        print(json.dumps(result,indent=2))
        if args.write_reports:
            target=path.with_suffix(".analysis.json")
            with target.open("x") as out:
                json.dump(result,out,indent=2)
                out.write("\n")


if __name__=="__main__":
    main()
