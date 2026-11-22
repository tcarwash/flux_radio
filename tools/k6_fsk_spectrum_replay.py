#!/usr/bin/env python3
"""Offline relative occupied-bandwidth comparison for Flux native-FSK IQ.

All output is an uncalibrated, local-SDR, payload-interval comparison.
No serial, RTL device ownership, transmit commands or flash operations.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import convolve, resample_poly, welch


RAW_FS = 1_024_000
FS = 128_000
CAPTURE_CENTER_HZ = 433_750_000
NOMINAL_TX_HZ = 433_500_000
RX_SHIFT_HZ = 280_500.0
ACTIVE_THRESHOLD_ADC = 2.1
ANALYSIS_RANGE_HZ = 15_000.0
NOISE_REFERENCE_INNER_HZ = 7_500.0


def measure_capture(raw: np.ndarray, *, first_s=.68, last_s=1.45,
                    head_skip_s=.09, tail_skip_s=.002) -> dict:
    if (not isinstance(raw,np.ndarray) or raw.ndim!=1 or raw.dtype!=np.uint8
        or raw.size%2 or raw.size<2*int(last_s*RAW_FS)
        or first_s<0 or last_s<=first_s or head_skip_s<0
        or tail_skip_s<0):
        raise ValueError("invalid interleaved 1.024-Msample/s U8 SDR capture")
    a,b=int(first_s*RAW_FS),int(last_s*RAW_FS)
    r=raw[2*a:2*b]
    iq=(r[::2].astype(np.float32)-127.5)+1j*(r[1::2].astype(np.float32)-127.5)
    t=(np.arange(len(iq),dtype=np.float64)+a)/RAW_FS
    iq *= np.exp(2j*np.pi*RX_SHIFT_HZ*t)
    iq=resample_poly(iq,1,RAW_FS//FS,window=("kaiser",10))
    # Detect only the largest continuous, sufficiently strong activity
    # span; avoid accidentally analyzing a stray isolated ADC excursion.
    env=convolve(abs(iq),np.ones(128)/128,mode="same")
    active=(env>ACTIVE_THRESHOLD_ADC).astype(np.uint8)
    padded=np.pad(active,(1,1))
    starts=np.where(np.diff(padded.astype(np.int8))==1)[0]
    ends=np.where(np.diff(padded.astype(np.int8))==-1)[0]
    spans=[(x,y) for x,y in zip(starts,ends) if y-x>=int(.14*FS)]
    if not spans:
        raise ValueError("no suitable RF burst detected")
    left,right=max(spans,key=lambda pair:pair[1]-pair[0])
    n0=left+round(head_skip_s*FS)
    n1=right-round(tail_skip_s*FS)
    if n1-n0<8192:
        raise ValueError("selected payload segment too short")
    payload=iq[n0:n1]
    # Do NOT use Welch's default detrend='constant': when the tuned
    # carrier lands at baseband DC it would remove the entire tone,
    # producing a spurious nearly-full-span occupied-bandwidth result.
    frequency,psd=welch(payload,fs=FS,nperseg=8192,
                        noverlap=4096,return_onesided=False,detrend=False)
    order=np.argsort(frequency)
    f=frequency[order]
    p=psd[order]
    within=np.abs(f)<ANALYSIS_RANGE_HZ
    f=f[within]
    p=p[within]
    noise_bins=(np.abs(f)>NOISE_REFERENCE_INNER_HZ) & (
        np.abs(f)<ANALYSIS_RANGE_HZ)
    floor=float(np.median(p[noise_bins]))
    corrected=np.maximum(p-floor,0.)
    total=float(corrected.sum())
    if not total>0 or not np.isfinite(total):
        raise ValueError("zero power after local noise subtraction")
    cumulative=np.cumsum(corrected)/total
    edges=np.interp([.005,.995],cumulative,f)
    outside=1-float(corrected[np.abs(f)<3000].sum())/total
    return {
        "relative_rf_activity_start_s":round(first_s+left/FS,6),
        "relative_rf_activity_end_s":round(first_s+right/FS,6),
        "analyzed_payload_interval_s":[
            round(first_s+n0/FS,6),round(first_s+n1/FS,6)
        ],
        "payload_samples_complex":int(len(payload)),
        "estimated_99pct_occupied_bandwidth_hz":round(float(edges[1]-edges[0]),2),
        "estimated_99pct_lower_edge_hz_baseband":round(float(edges[0]),2),
        "estimated_99pct_upper_edge_hz_baseband":round(float(edges[1]),2),
        "estimated_signal_power_fraction_outside_plusminus_3khz":round(outside,6),
        "frequency_bin_resolution_hz":round(FS/8192,5),
        "integrated_frequency_window_hz":[-ANALYSIS_RANGE_HZ,ANALYSIS_RANGE_HZ],
        "local_noise_reference_window_hz":[
            NOISE_REFERENCE_INNER_HZ,ANALYSIS_RANGE_HZ
        ],
        "local_noise_psd_median":floor,
        "noise_floor_subtracted":True,
        "rf_power_calibrated":False,
        "emissions_compliance_verified":False,
        "physical_tx_triggered_by_analyzer":False,
        "note":(
            "Air-coupled RTL-SDR relative waveform comparison only. "
            "Receiver filters and frequency window limit these numbers. "
            "This is not a regulatory occupied-bandwidth or adjacent-channel test."
        )
    }


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iq",type=Path,required=True,nargs="+")
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.out.exists():
        parser.error("refusing to overwrite existing spectrum analysis")
    try:
        rows=[]
        for p in args.iq:
            raw=np.load(p,mmap_mode="r",allow_pickle=False)
            result=measure_capture(raw)
            with p.open("rb") as handle:
                result["iq_npy_sha256"]=hashlib.file_digest(handle,"sha256").hexdigest()
            result["iq_path"]=str(p)
            rows.append(result)
        document={
            "project":"Flux",
            "study":"FSK/PDM uncalibrated relative SDR spectral comparison",
            "offline_only":True,
            "real_transmission_initiated":False,
            "capture_center_hz":CAPTURE_CENTER_HZ,
            "nominal_tx_hz":NOMINAL_TX_HZ,
            "sample_rate_input_hz":RAW_FS,
            "complex_mix_shift_hz_assumed":RX_SHIFT_HZ,
            "cases":rows
        }
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(document,indent=2)+"\n")
    except (OSError,ValueError,TypeError) as err:
        parser.error(str(err))
    for row in rows:
        print(Path(row["iq_path"]).name,
              "OBW99",row["estimated_99pct_occupied_bandwidth_hz"],
              "out +/-3k",row["estimated_signal_power_fraction_outside_plusminus_3khz"])
    print("Saved offline spectral comparison:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
