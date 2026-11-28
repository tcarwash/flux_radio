#!/usr/bin/env python3
"""Sweep modeled GP0 PDM-count aliases in the *entire* 8-kHz voice band.

Run offline; uses only the Python fixed-point decimator oracle and deterministic
64-popcount input. This is a *synthetic* test, not a microphone measurement or
an exact noise-shaped PDM representation of the FD6818 analog front-end.

The intermediate CIC R=8 rate is 10,907.5Hz, so an interfering frequency's
largest output component need not occur at the direct 8-kHz alias bin.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from tools.k6_gp0_antialias_study import decimate, FS

VOICE_LOW_HZ=80
VOICE_HIGH_HZ=3900


def peak(x):
    x=np.asarray(x,dtype=float)
    x=x[1600:]
    if len(x)<800:raise ValueError("needs at least 300ms output")
    w=np.hanning(len(x))
    axis=np.fft.rfftfreq(len(x),d=1/8000)
    amp=np.abs(np.fft.rfft(x*w))*2/w.sum()
    amp[(axis<VOICE_LOW_HZ)|(axis>VOICE_HIGH_HZ)]=0
    i=int(amp.argmax())
    return (round(float(axis[i]),2),float(amp[i]))


def sweep(*,frequencies,duration=.7,density_amplitude=4.0):
    frequencies=[float(v) for v in frequencies]
    if not frequencies or len(frequencies)>96:
        raise ValueError("expected 1..96 frequencies")
    if not .35<=duration<=2.0:
        raise ValueError("duration must be .35..2s")
    if not 0<density_amplitude<=8:
        raise ValueError("centered popcount sinusoid must be 0..8")
    t=np.arange(int(FS*duration))/FS
    rows=[]
    for hz in frequencies:
        if not math.isfinite(hz) or not 4000<hz<FS/2:
            raise ValueError("frequency must be in (4k, Nyquist)")
        bits=np.clip(np.rint(32+density_amplitude*np.sin(2*np.pi*hz*t)),0,64).astype(np.uint8)
        spectra={
          "legacy":peak(decimate(bits)),
          "aa3":peak(decimate(bits,poles=3)),
          "cic3":peak(decimate(bits,cic3=True)),
        }
        base=max(spectra["legacy"][1],1e-10)
        aa3=20*np.log10(max(spectra["aa3"][1],1e-10)/base)
        cic3=20*np.log10(max(spectra["cic3"][1],1e-10)/base)
        rows.append(dict(input_hz=hz,
                         legacy_peak_hz=spectra["legacy"][0],
                         aa3_peak_hz=spectra["aa3"][0],
                         cic3_peak_hz=spectra["cic3"][0],
                         aa3_db=round(float(aa3),2),
                         cic3_db=round(float(cic3),2),
                         cic3_minus_aa3_db=round(float(cic3-aa3),2)))
    worst=sorted(rows,key=lambda x:x["cic3_minus_aa3_db"],reverse=True)
    return dict(schema=1,offline_only=True,
        scope="deterministic centered 64-popcount sinusoid, NOT measured microphone/PDM",
        status="alias-suppression sensitivity, NOT hardware qualification",
        input_group_rate_hz=FS,output_rate_hz=8000,
        output_voice_band_hz=[VOICE_LOW_HZ,VOICE_HIGH_HZ],
        duration_seconds=duration,density_amplitude=density_amplitude,
        fixture_count=len(rows),
        worst_cic3_vs_aa3=worst[:8],
        rows=rows)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start",type=float,default=5000)
    ap.add_argument("--stop",type=float,default=43000)
    ap.add_argument("--step",type=float,default=1250)
    ap.add_argument("--duration",type=float,default=.7)
    ap.add_argument("--json",type=Path,help="save results to a NEW path")
    a=ap.parse_args()
    if not 0<a.step and a.start<a.stop:ap.error("invalid sweep interval")
    frequencies=np.arange(a.start,a.stop+.001,a.step)
    result=sweep(frequencies=frequencies,duration=a.duration)
    print(json.dumps(result["worst_cic3_vs_aa3"],indent=2))
    print("SWEEP",result["fixture_count"],"WORST_DELTA_DB",
          result["worst_cic3_vs_aa3"][0]["cic3_minus_aa3_db"])
    if a.json:
        with a.json.open("x") as output:
            output.write(json.dumps(result,indent=2,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
