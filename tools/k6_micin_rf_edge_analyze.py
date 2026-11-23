#!/usr/bin/env python3
"""Offline short RF-keying-edge detector for K6/RTL-SDR captures.

Uses ~2 ms Hann FFT windows at 1.024 MS/s, with a narrow expected
frequency-offset search and nearby reference flanks. The long-window SDR
analyzer can miss <30 ms TX-startup pulses. This cannot measure calibrated
RF power, absolute frequency error, PA leakage, or emission compliance.
No radio I/O, PTT, serial access, or waveform transmission.
"""
import argparse
import json
from pathlib import Path

import numpy as np


def measure(raw, *, sample_rate=1_024_000, expected_offset_hz=-280375,
            threshold_db=15.0, min_windows=3, fft_size=2048):
    if (not isinstance(raw, np.ndarray) or raw.dtype != np.uint8
            or raw.ndim != 1 or len(raw) % 2 or len(raw) < fft_size * 4):
        raise ValueError("Expected interleaved uint8 I/Q with complete pairs and >=4 FFT windows")
    if (sample_rate < 100_000 or fft_size < 256 or fft_size & (fft_size - 1)
            or not (1 <= min_windows <= 100) or not (0 < threshold_db <= 60)
            or not (-sample_rate / 2 + 75000 < expected_offset_hz < sample_rate / 2 - 75000)):
        raise ValueError("Invalid sample rate, FFT size, frequency offset, or burst threshold")
    x = (raw[::2].astype(np.float32) - 127.5
         + 1j * (raw[1::2].astype(np.float32) - 127.5))
    n = len(x) // fft_size
    frames = x[:n * fft_size].reshape(n, fft_size)
    power = (np.abs(np.fft.fftshift(np.fft.fft(
        frames * np.hanning(fft_size), axis=1), axes=1)) ** 2)
    bins = np.fft.fftshift(np.fft.fftfreq(fft_size, d=1 / sample_rate))
    near = np.abs(bins - expected_offset_hz) < 7000
    flank = ((np.abs(bins - expected_offset_hz) > 35000)
             & (np.abs(bins - expected_offset_hz) < 65000))
    if near.sum() < 3 or flank.sum() < 6:
        raise ValueError("RF target and reference flanks outside FFT spectrum")
    rel = 10 * np.log10(
        np.maximum(power[:, near].max(axis=1), 1e-20)
        / np.maximum(np.median(power[:, flank], axis=1), 1e-20))
    active = np.flatnonzero(rel > threshold_db)
    groups = []
    if len(active):
        start = prev = int(active[0])
        for i in active[1:]:
            i = int(i)
            if i > prev + 1:
                if prev - start + 1 >= min_windows:
                    groups.append((start, prev))
                start = i
            prev = i
        if prev - start + 1 >= min_windows:
            groups.append((start, prev))
    duration = fft_size / sample_rate
    segments = []
    for start, end in groups:
        strongest = start + int(np.argmax(rel[start:end+1]))
        best = float(bins[near][np.argmax(power[strongest, near])])
        segments.append({
            "start_s": round(start * duration, 4),
            "end_s": round((end + 1) * duration, 4),
            "duration_ms": round((end - start + 1) * duration * 1000, 2),
            "peak_local_bin_vs_flanks_db": round(float(rel[strongest]), 2),
            "carrier_offset_hz_uncalibrated": best,
        })
    return {
        "iq_duration_s": round(len(x) / sample_rate, 4),
        "sample_rate_hz": sample_rate,
        "fft_window_ms": round(duration * 1000, 3),
        "target_offset_hz": expected_offset_hz,
        "threshold_local_db": threshold_db,
        "minimum_consecutive_windows": min_windows,
        "peak_local_bin_vs_flanks_db": round(float(rel.max()), 2),
        "windows_above_threshold": int(len(active)),
        "detected_segments": segments,
        "adc_clip_fraction": round(float(np.mean((raw <= 1) | (raw >= 254))), 8),
        "caution": ("Uncalibrated relative SDR detectability, not measured PA power, "
                    "absolute RF frequency, emissions compliance, or absence of RF leakage."),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("iq", type=Path, help="NumPy .npy interleaved uint8 IQ")
    p.add_argument("--sample-rate", type=int, default=1_024_000)
    p.add_argument("--expected-offset", type=float, default=-280375)
    p.add_argument("--threshold-db", type=float, default=15)
    p.add_argument("--min-windows", type=int, default=3)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    raw = np.load(args.iq, allow_pickle=False, mmap_mode="r")
    result = measure(raw, sample_rate=args.sample_rate,
                     expected_offset_hz=args.expected_offset,
                     threshold_db=args.threshold_db, min_windows=args.min_windows)
    if args.out:
        if args.out.exists():
            p.error("Refusing to overwrite analysis report")
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
