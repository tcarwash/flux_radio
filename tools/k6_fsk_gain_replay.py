#!/usr/bin/env python3
"""Offline relative two-plateau native-FSK separation from saved RTL U8 I/Q.

No serial, radio, or SDR access. Frequencies are relative to the SDR's
uncalibrated center, and the peak spacing is not an emission-mask result.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
from scipy.signal import butter, find_peaks, resample_poly, sosfiltfilt


def measure(raw: np.ndarray, *, fs=1_024_000, start_s=.78,
            end_s=1.05, shift_hz=280_500, cutoff_hz=10_000):
    if raw.ndim != 1 or raw.dtype != np.uint8 or len(raw) % 2:
        raise ValueError("expected 1D interleaved unsigned-byte IQ")
    if fs != 1_024_000 or not 0 <= start_s < end_s <= len(raw)/2/fs:
        raise ValueError("invalid sample rate or capture window")
    first, last = int(start_s*fs), int(end_s*fs)
    if last-first < 10_000:
        raise ValueError("too few IQ samples")
    chunk = raw[2*first:2*last]
    z = (chunk[::2].astype(np.float32)-127.5)+1j*(chunk[1::2].astype(np.float32)-127.5)
    t = np.arange(len(z))/fs
    z *= np.exp(2j*np.pi*shift_hz*t)
    fs2 = fs//8
    z = resample_poly(z, 1, 8)
    z = sosfiltfilt(butter(4, cutoff_hz, fs=fs2, output="sos"), z)
    freq = np.angle(z[1:]*z[:-1].conj())*fs2/(2*np.pi)
    # High-amplitude sample selection suppresses quiet time before and after
    # the short frame. No known bit sequence is used for the plateau estimate.
    mask = (abs(z[1:]) > np.quantile(abs(z), .8)) & (abs(freq)<12_000)
    vals = freq[mask]
    if len(vals)<100:
        raise ValueError("no sufficiently strong burst samples")
    counts, edges = np.histogram(vals, bins=160, range=(-7000,7000))
    peaks,_ = find_peaks(counts,prominence=counts.max()*.08,distance=10)
    if len(peaks)<2:
        raise ValueError("two stable frequency plateaus not resolved")
    two = sorted(sorted(peaks,key=lambda i:counts[i],reverse=True)[:2])
    hz = [float((edges[i]+edges[i+1])/2) for i in two]
    return dict(lower_peak_hz=hz[0],upper_peak_hz=hz[1],
                separation_hz=hz[1]-hz[0],
                half_deviation_hz=(hz[1]-hz[0])/2,
                relative_midpoint_hz=sum(hz)/2,
                selected_samples=int(len(vals)),
                bin_width_hz=float(edges[1]-edges[0]),
                method="two largest prominent histogram peaks of filtered, frequency-demodulated SDR IQ",
                calibrated=False,transmitted=False)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("iq",type=Path,nargs="+")
    p.add_argument("--output",type=Path)
    a=p.parse_args()
    results={}
    for path in a.iq:
        results[str(path)] = measure(np.load(path,mmap_mode="r",allow_pickle=False))
    if a.output:
        if a.output.exists():p.error("refusing to overwrite report")
        a.output.write_text(json.dumps(results,indent=2)+"\n")
    print(json.dumps(results,indent=2))


if __name__=="__main__":
    main()
