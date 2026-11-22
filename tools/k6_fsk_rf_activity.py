#!/usr/bin/env python3
"""Reproduce relative RF activity metrics from *saved* interleaved U8 SDR IQ.

No SDR hardware control, no radio, no serial port, no PTT. A detected
carrier does not imply FSK modulation, output power or frequency accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import welch

FS_HZ = 1_024_000
CENTER_HZ = 433_750_000
TARGET_HZ = 433_500_000
WINDOW_SAMPLES = 51_200


def analyze(raw: np.ndarray, *, fs=FS_HZ, center_hz=CENTER_HZ,
            target_hz=TARGET_HZ, window_samples=WINDOW_SAMPLES,
            min_spectral_contrast_db=15.0) -> dict:
    if (not isinstance(raw, np.ndarray) or raw.ndim != 1
        or raw.dtype != np.uint8 or len(raw) % 2
        or not isinstance(window_samples,int) or window_samples<8192
        or len(raw)<2*window_samples):
        raise ValueError("need 1-D interleaved U8 I/Q with complete windows")
    if (fs<=0 or not 0<min_spectral_contrast_db<100
        or abs(target_hz-center_hz)>=fs*.4):
        raise ValueError("frequency or activity threshold out of range")
    ncomplex = len(raw)//2
    window_count = ncomplex//window_samples
    rows=[]
    for k in range(window_count):
        a=raw[2*k*window_samples:2*(k+1)*window_samples]
        z=(a[::2].astype(np.float32)-127.5)+1j*(a[1::2].astype(np.float32)-127.5)
        freq,power=welch(z,fs=fs,nperseg=8192,noverlap=4096,
                         return_onesided=False)
        use=np.abs(freq-(target_hz-center_hz))<=60_000
        bins=np.flatnonzero(use)
        best=int(bins[np.argmax(power[bins])])
        center_level=float(np.median(power[bins]))
        peak_db=10*np.log10(max(float(power[best]),1e-18)/
                            max(center_level,1e-18))
        rows.append({
            "window_start_s":round(k*window_samples/fs,6),
            "window_end_s":round((k+1)*window_samples/fs,6),
            "peak_above_local_median_db":round(float(peak_db),3),
            "peak_receiver_frequency_hz":int(round(center_hz+float(freq[best]))),
            "rms_relative_adc":round(float(np.sqrt(np.mean(np.abs(z)**2))),4),
        })
    active=[x for x in rows if x["peak_above_local_median_db"]>min_spectral_contrast_db]
    peak=max(rows,key=lambda x:x["peak_above_local_median_db"])
    threshold_before=[x for x in rows if x["window_start_s"]<0.5]
    threshold_after=[x for x in rows if x["window_start_s"]>=2.]
    return {
        "experiment":"Flux FSK-05 saved-IQ relative RF activity",
        "offline_only":True,
        "no_transmission_initiated":True,
        "modulation_identified":False,
        "sample_rate_hz":fs,
        "sdr_center_hz":center_hz,
        "nominal_tx_hz":target_hz,
        "samples_complex":ncomplex,
        "capture_duration_s":round(ncomplex/fs,6),
        "window_duration_s":round(window_samples/fs,6),
        "activity_threshold_db_above_local_median":min_spectral_contrast_db,
        "active_window_count":len(active),
        "first_active_window_s":active[0]["window_start_s"] if active else None,
        "last_active_window_s":active[-1]["window_start_s"] if active else None,
        "peak_spectral_contrast_db":peak["peak_above_local_median_db"],
        "peak_receiver_frequency_hz":peak["peak_receiver_frequency_hz"],
        "apparent_combined_tx_rx_offset_hz":peak["peak_receiver_frequency_hz"]-target_hz,
        "adc_extreme_fraction":round(float(np.mean((raw<=1)|(raw>=254))),8),
        "early_quiet_relative":bool(threshold_before and all(
            x["peak_above_local_median_db"]<=min_spectral_contrast_db
            for x in threshold_before)),
        "late_quiet_relative":bool(threshold_after and all(
            x["peak_above_local_median_db"]<=min_spectral_contrast_db
            for x in threshold_after)),
        "calibrated_rf_power_dbm":None,
        "calibrated_tx_frequency_error_hz":None,
        "notes":[
            "This is only a relative air-coupled SDR signal/activity estimate.",
            "The frequency offset includes transmitter and SDR oscillator errors.",
            "50-ms windows are not an exact keyed-burst duration.",
            "Spectral contrast does not identify FM versus FSK or prove emissions compliance."
        ],
        "windows":rows
    }


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--center-hz",type=int,default=CENTER_HZ)
    p.add_argument("--nominal-tx-hz",type=int,default=TARGET_HZ)
    p.add_argument("--threshold-db",type=float,default=15.0)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite an existing analysis file")
    try:
        raw=np.load(args.iq,mmap_mode="r",allow_pickle=False)
        report=analyze(raw,center_hz=args.center_hz,
                       target_hz=args.nominal_tx_hz,
                       min_spectral_contrast_db=args.threshold_db)
        with args.iq.open("rb") as f:
            report["source_npy_sha256"]=hashlib.file_digest(f,"sha256").hexdigest()
        report["source_path"]=str(args.iq)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2)+"\n")
    except (OSError,ValueError) as exc:
        p.error(str(exc))
    print("Offline only, no TX:",report["active_window_count"],"RF activity windows",
          "peak",report["peak_spectral_contrast_db"],"dB")
    print("Saved:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
