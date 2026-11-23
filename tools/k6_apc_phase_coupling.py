#!/usr/bin/env python3
"""OFFLINE measured APC-induced phase-fit offsets in archived constant-carrier IQ.

Estimate independent low-power APC 80%/100% stage-edge phase perturbations
from a single radio *APC-only* RF recording. No transmitter, serial, SDR,
flash or network. These are local extrapolated carrier phase offsets, NOT
necessarily mathematically instantaneous phase jumps or chip-only AM-to-PM.
Uncalibrated air-coupled receiver effects cannot be excluded.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from k6_polar_surrogate import (
    _load_iq, _burst_bounds, _downconvert, FS_OUT,
)

PREPOST_WINDOWS = ((-.007,-.002),(.002,.007))
COMPARISON_TIMES = (.023,.037,.052,.724,.744,.756)
HOLD_SEC = .0206


def wrapphase(rad):
    return float(np.angle(np.exp(1j*rad)))


def extrapolated_phase_offset(time, phase, center):
    """Fit phase/frequency on each side, then compare extrapolations at edge."""
    fits=[]
    for lo,hi in PREPOST_WINDOWS:
        sel=(time>center+lo)&(time<center+hi)
        if np.count_nonzero(sel)<50:
            raise ValueError("insufficient time samples for phase estimate")
        slope,intercept=np.polyfit(time[sel]-center,phase[sel],1)
        fits.append((float(slope),float(intercept)))
    return {
        "local_extrapolated_phase_offset_rad":wrapphase(fits[1][1]-fits[0][1]),
        "change_in_frequency_slope_hz":(fits[1][0]-fits[0][0])/(2*np.pi),
    }


def analyze(path:Path):
    raw,meta=_load_iq(path,source="apc")
    onset,off=_burst_bounds(raw)
    if not .71<=off-onset<=.83:
        raise ValueError("not the expected 760-ms APC-fast recording")
    local_start=onset+.006
    local_length=.774
    if local_start+local_length>len(raw)//2/1_024_000:
        raise ValueError("missing end of RF recording")
    iq=_downconvert(raw,local_start,local_length)
    t=np.arange(len(iq))/FS_OUT+(local_start-onset)
    phase=np.unwrap(np.angle(iq))
    amp=np.abs(iq)
    smoothed=np.convolve(amp,np.ones(65)/65,mode="same")
    full=float(np.median(smoothed[(t>.03)&(t<.068)]))
    low=float(np.median(smoothed[(t>.086)&(t<.096)]))
    if not 0<low<full:
        raise ValueError("80-percent stage not measurably below full")
    threshold=(full+low)/2.
    edges=[]
    for k in range(16):
        nominal=.080+k*HOLD_SEC
        sel=np.flatnonzero((t>nominal-.007)&(t<nominal+.011))
        if len(sel)<100:raise ValueError("insufficient samples near expected APC edge")
        nearest=int(sel[np.argmin(np.abs(smoothed[sel]-threshold))])
        when=float(t[nearest])
        before=(t>when-.008)&(t<when-.003)
        after=(t>when+.003)&(t<when+.008)
        before_med=float(np.median(smoothed[before]))
        after_med=float(np.median(smoothed[after]))
        direction="high_to_low" if k%2==0 else "low_to_high"
        if (direction=="high_to_low" and before_med-after_med < (full-low)*.25 or
                direction=="low_to_high" and after_med-before_med < (full-low)*.25):
            raise ValueError("expected APC stage edge not observed")
        fit=extrapolated_phase_offset(t,phase,when)
        edges.append({
            "index":k,"direction":direction,
            "edge_time_relative_to_tx_onset_seconds":round(when,6),
            "pre_post_median_amplitudes_adc":[round(before_med,4),round(after_med,4)],
            "phase_fit_offset_rad":round(fit["local_extrapolated_phase_offset_rad"],5),
            "local_frequency_slope_change_hz":round(fit["change_in_frequency_slope_hz"],3),
        })
    controls=[extrapolated_phase_offset(t,phase,x) for x in COMPARISON_TIMES]
    drops=[e["phase_fit_offset_rad"] for e in edges if e["direction"]=="high_to_low"]
    rises=[e["phase_fit_offset_rad"] for e in edges if e["direction"]=="low_to_high"]
    null=np.array([x["local_extrapolated_phase_offset_rad"] for x in controls])
    median_all=np.median(np.abs([e["phase_fit_offset_rad"] for e in edges]))
    return {
        "analysis_version":1,
        "analysis":"APC-only real RF capture; local extrapolated phase FIT, not direct RF hardware IQ EVM",
        "source_path":str(path),
        "source_iq_raw_sha256_verified":meta["iq_raw_u8_sha256"],
        "phase_coupling_source_attribution_proven":False,
        "combined_frequency_and_amplitude_modulation_demonstrated":False,
        "apc_stages":len(edges),
        "high_to_low_phase_fit_offset_median_rad":round(float(np.median(drops)),5),
        "low_to_high_phase_fit_offset_median_rad":round(float(np.median(rises)),5),
        "median_absolute_stage_edge_phase_fit_offset_rad":round(float(median_all),5),
        "median_absolute_nonedge_comparison_phase_fit_offset_rad":round(float(np.median(np.abs(null))),5),
        "maximum_absolute_nonedge_comparison_phase_fit_offset_rad":round(float(np.max(np.abs(null))),5),
        "all_edge_phases_directionally_consistent":bool(all(d>0 for d in drops) and all(r<0 for r in rises)),
        "calibrated_high_level_adc":round(full,4),
        "calibrated_low_level_adc":round(low,4),
        "edge_detection_midpoint_adc":round(threshold,4),
        "edge_records":edges,
        "nonedge_comparison_times_relative_seconds":list(COMPARISON_TIMES),
        "nonedge_comparison_phase_fit_offsets_rad":[round(float(v),5) for v in null],
        "caveats":[
            "Two-sided local linear extrapolation measures an apparent phase perturbation across a small transition window; it does not prove an instantaneous discontinuity",
            "RF source and RTL-SDR receiver AM-to-PM effects cannot be separated from this air-coupled recording",
            "Frequency-slope deltas include frequency pulling, noise and local fit effects",
            "Amplitude transitions were autonomously timed by firmware and not explicitly timestamped at SPI register-write level",
            "No combined-FSK/APC RF signal has been transmitted or decoded",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("iq",type=Path,nargs="+")
    p.add_argument("--write-reports",action="store_true")
    args=p.parse_args(argv)
    for path in args.iq:
        output=analyze(path)
        print(json.dumps(output,indent=2))
        if args.write_reports:
            target=path.with_suffix(".phase.json")
            with target.open("x") as f:
                json.dump(output,f,indent=2)
                f.write("\n")


if __name__=="__main__":
    main()
