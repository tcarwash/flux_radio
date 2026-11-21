#!/usr/bin/env python3
"""Parse a completed receive-only PA11 module capture; export raw NPZ and WAV.

Hardware ADC/timing are experimental. The WAV is individually amplitude-scaled
for listening; use raw sample counts in the NPZ for quantitative comparisons.
No serial control or transmissions occur here.
"""
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path

import numpy as np

HEAD = re.compile(r"^pa11pcm n=(\d+) w=(\d+) h=(\d+) drop=(\d+)$")
REGS = re.compile(r"^pa11pcm r47=([a-f0-9]{4}) r48=([a-f0-9]{4})$")
TIMES = re.compile(r"^pa11pcm tick100us=(\d+) lo=(\d+) hi=(\d+)$")
ROW = re.compile(r"^d ([a-f0-9]{4})([a-f0-9]+)$")


def parse_capture(report: dict) -> tuple[np.ndarray, dict]:
    """Only accept complete, ordered, physically bounded capture reports."""
    if report.get("ram") is not True or report.get("returned") is not True:
        raise ValueError("capture did not return as a RAM module")
    lines = report.get("reports")
    if not isinstance(lines, list) or len(lines) < 5:
        raise ValueError("missing complete module reports")
    head, regs, times = HEAD.fullmatch(lines[0]), REGS.fullmatch(lines[1]), TIMES.fullmatch(lines[2])
    if not head or not regs or not times:
        raise ValueError("invalid PA11 capture header")
    n, w, hold, drop = map(int, head.groups())
    ticks, low, high = map(int, times.groups())
    if n <= 0 or n > 8192 or w < 1 or w > 255 or not 1 <= hold <= 64:
        raise ValueError("unsupported sample configuration")
    if ticks <= 0 or ticks >= 65536:
        raise ValueError("invalid capture timer")
    if report.get("result") != n or lines[-1] != f"pa11pcm end n={n}":
        raise ValueError("module did not complete the declared capture")
    values: list[int] = []
    for line in lines[3:-1]:
        m = ROW.fullmatch(line)
        if not m or len(m.group(2)) == 0 or len(m.group(2)) % 4:
            raise ValueError(f"malformed sample row: {line[:24]}")
        offset = int(m.group(1), 16)
        if offset != len(values):
            raise ValueError(f"out-of-order or missing sample data at {len(values)}: got {offset}")
        values.extend(int(m.group(2)[i:i+4], 16) for i in range(0, len(m.group(2)), 4))
        if len(values) > n:
            raise ValueError("more samples than declared")
    if len(values) != n:
        raise ValueError(f"incomplete sample stream ({len(values)}/{n})")
    x = np.asarray(values, dtype=np.uint16)
    if x.max() > w*w or (int(x.min()), int(x.max())) != (low, high):
        raise ValueError("sample range inconsistent with capture metadata")
    rate = n * 10000.0 / ticks
    metadata = dict(n=n, turns=w, hold=hold, settling_samples=drop,
                    measured_tick_100us=ticks, measured_sample_rate_hz=rate,
                    af47=int(regs.group(1), 16), af48=int(regs.group(2), 16),
                    min_counts=low, max_counts=high)
    return x, metadata


def analyze(samples: np.ndarray, metadata: dict) -> tuple[np.ndarray, dict]:
    """Detrend startup bias and measure power without pretending to calibrate SNR."""
    x = samples.astype(np.float64)
    mean = float(x.mean())
    t = np.arange(x.size, dtype=np.float64)
    if len(x) > 1:
        slope, intercept = np.polyfit(t, x, 1)
        centered = x - (slope*t + intercept)
    else:
        slope = 0.0
        centered = x-mean
    fs = metadata["measured_sample_rate_hz"]
    window = np.hanning(len(x))
    bins = np.fft.rfftfreq(len(x), d=1/fs)
    power = np.abs(np.fft.rfft(centered*window))**2
    band = (bins >= 300.0) & (bins <= 3000.0)
    k = int(np.argmax(np.where(band, power, -1.0))) if band.any() else 0
    data = {**metadata, "mean_counts": mean, "rms_detrended_counts": float(np.sqrt(np.mean(centered**2))),
            "peak_to_peak_counts": int(np.ptp(x)),
            "linear_drift_counts_per_sample": float(slope),
            "peak_300_3000_hz": float(bins[k]) if band.any() else None,
            "inband_power_proxy": float(power[band].sum())}
    return centered, data


def export_capture(path: Path, prefix: Path) -> dict:
    report = json.loads(path.read_text(encoding="utf-8"))
    samples, metadata = parse_capture(report)
    centered, summary = analyze(samples, metadata)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(prefix.with_suffix(".npz"), samples=samples,
                        detrended_counts=centered, measured_sample_rate_hz=metadata["measured_sample_rate_hz"])
    peak = float(np.max(np.abs(centered)))
    wav = np.zeros(centered.shape, dtype=np.int16)
    if peak > 0:
        wav = np.round(np.clip(centered/peak * 0.8, -1, 1)*32767).astype(np.int16)
    with wave.open(str(prefix.with_suffix(".wav")), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(round(metadata["measured_sample_rate_hz"]))
        w.writeframes(wav.tobytes())
    prefix.with_suffix(".meta.json").write_text(json.dumps(summary, indent=2)+"\n", encoding="utf-8")
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("capture", type=Path, help="JSON stdout from k6_live.py run-ram")
    p.add_argument("--out", type=Path, required=True, help="output prefix (WAV, NPZ and JSON)")
    a = p.parse_args()
    print(json.dumps(export_capture(a.capture, a.out), indent=2))


if __name__ == "__main__":
    main()
