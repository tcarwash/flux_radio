#!/usr/bin/env python3
"""Analyze a previously recorded Flux APC 13-step fine-threshold RTL-SDR capture.

OFFLINE ONLY: no radio, serial, SDR device, PTT, firmware writes, or network.
Measures relative coherent carrier amplitude, stage variability, and deep
carrier fades from archived interleaved u8 IQ. Air-coupled SDR measurements
are NOT calibrated RF power, compliance or emissions measurements.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

FS = 1_024_000
STAGES = (100, 85, 80, 78, 76, 74, 72, 70, 72, 74, 76, 80, 100)
STAGE_SECONDS = 0.075
ANALYSIS_FFT = 4096
SAMPLES_PER_MS = FS // 1000
SPECTRAL_BAND_HZ = (-320_000, -240_000)


def group_events(indexes: np.ndarray) -> list[tuple[int, int]]:
    """Contiguous sample-bin spans, inclusive."""
    if not len(indexes):
        return []
    breaks = np.flatnonzero(np.diff(indexes) > 1) + 1
    groups = np.split(indexes, breaks)
    return [(int(g[0]), int(g[-1])) for g in groups]


def analyze_capture(path: Path, *, min_peak_power: float = 4_000_000) -> dict:
    path = Path(path)
    raw = np.load(path, mmap_mode="r", allow_pickle=False)
    if raw.dtype != np.uint8 or raw.ndim != 1 or len(raw) % 2:
        raise ValueError("expected uint8 interleaved I/Q archive")
    if not 1_000_000 <= len(raw) <= 30_000_000:
        raise ValueError("unsupported capture size")
    manifest = path.with_suffix(".json")
    if not manifest.is_file():
        raise FileNotFoundError(f"missing original capture manifest {manifest}")
    meta = json.loads(manifest.read_text())
    if meta.get("sample_rate_hz") != FS or meta.get("capture_center_hz") != 433_750_000:
        raise ValueError("unexpected sample rate or SDR tuning")
    tx = meta.get("hardware_lab_transaction", {})
    done = tx.get("module_completion", {})
    if (not done.get("returned") or done.get("result") != 13 or
            not tx.get("original_module_restored") or
            not tx.get("original_radio_frequency_restored")):
        raise ValueError("capture does not document completed thirteen-step hardware TX and restoration")
    raw_sha = hashlib.sha256(np.asarray(raw).tobytes()).hexdigest()
    if raw_sha != meta.get("iq_raw_u8_sha256"):
        raise ValueError("IQ checksum does not match original manifest")

    nfft = ANALYSIS_FFT
    frames = (len(raw) // 2) // nfft
    x = np.asarray(raw[:frames * nfft * 2], dtype=np.float32)
    z = (x[::2] - 127.5 + 1j * (x[1::2] - 127.5)).reshape(frames, nfft)
    band_hz = np.fft.fftfreq(nfft, 1 / FS)
    roi = (band_hz > SPECTRAL_BAND_HZ[0]) & (band_hz < SPECTRAL_BAND_HZ[1])
    power = np.abs(np.fft.fft(z * np.hanning(nfft), axis=1)[:, roi]) ** 2
    strong = np.flatnonzero(np.max(power, axis=1) > min_peak_power)
    if len(strong) < 110:
        raise ValueError("could not identify sufficiently long, strong RF event")
    onset = float(strong[0] * nfft / FS)
    off = float((strong[-1] + 1) * nfft / FS)
    if not 0.88 <= off - onset <= 1.08:
        raise ValueError("RF event does not match expected ~975 ms bounded frame")

    # Stable final 100% stage for frequency tracking. This frequency is
    # only relative to RTL tuning, NOT proof of radio frequency accuracy.
    length = 65_536
    center = int((off - .075) * FS)
    if center < 0 or center + length > len(raw) // 2:
        raise ValueError("insufficient RF spectrum near final stage")
    ss = np.asarray(raw[2 * center:2 * (center + length)], dtype=np.float32)
    carrier = ss[::2] - 127.5 + 1j * (ss[1::2] - 127.5)
    ff = np.fft.fftfreq(length, 1 / FS)
    spectrum = np.fft.fft(carrier * np.hanning(length))
    mask = (ff > SPECTRAL_BAND_HZ[0]) & (ff < SPECTRAL_BAND_HZ[1])
    f0 = float(ff[np.flatnonzero(mask)[np.argmax(np.abs(spectrum[mask]))]])

    # 1 ms complex coherent averages suppress the SDR's broadband noise.
    # Track actual *magnitude* as well to distinguish phase cancellation
    # from a genuine collapse of received carrier power.
    start = max(0, int((onset - .020) * FS))
    end = min(len(raw) // 2, int((off + .020) * FS))
    v = np.asarray(raw[2 * start:2 * end], dtype=np.float32)
    sig = (v[::2] - 127.5 + 1j * (v[1::2] - 127.5))
    tt = np.arange(sig.size, dtype=np.float64) + start
    sig *= np.exp((-2j * np.pi * f0 / FS) * tt)
    nr = sig.size // SAMPLES_PER_MS
    bb = sig[:nr * SAMPLES_PER_MS].reshape(nr, SAMPLES_PER_MS)
    coherent = np.abs(np.mean(bb, axis=1))
    sample_rms = np.sqrt(np.mean(np.abs(bb) ** 2, axis=1))
    ts = (start + (np.arange(nr) + .5) * SAMPLES_PER_MS) / FS

    vals = []
    for i, percent in enumerate(STAGES):
        lo = onset + i * STAGE_SECONDS + .010
        hi = onset + (i + 1) * STAGE_SECONDS - .010
        selected = (ts > lo) & (ts < hi)
        if selected.sum() < 20:
            raise ValueError("stage has insufficient stable samples")
        vals.append(float(np.median(coherent[selected])))
    reference = (vals[0] + vals[-1]) / 2.0
    if reference <= 0:
        raise ValueError("zero carrier reference amplitude")
    # A deep fade is <36% of full carrier amplitude, well below the
    # expected 70% stage. Count both affected ms bins and fade episodes.
    fade_threshold = reference * .36
    stages = []
    for i, percent in enumerate(STAGES):
        lo = onset + i * STAGE_SECONDS + .010
        hi = onset + (i + 1) * STAGE_SECONDS - .010
        sel = np.flatnonzero((ts > lo) & (ts < hi))
        amps = coherent[sel]
        faded = sel[amps < fade_threshold]
        intervals = group_events(faded)
        stages.append({
            "stage_index": i,
            "commanded_target_percent": percent,
            "apc_target_byte_for_this_radio": round(180 * percent / 100),
            "carrier_median_adc_coherent": round(vals[i], 4),
            "carrier_amplitude_relative_to_full_percent": round(100 * vals[i] / reference, 2),
            "carrier_amplitude_std_adc": round(float(np.std(amps)), 4),
            "deep_fade_bins_1ms": int(len(faded)),
            "deep_fade_events": len(intervals),
            "deep_fade_times_seconds_capture": [round(float(ts[a]), 5) for a, _ in intervals],
            "lowest_received_rms_adc": round(float(np.min(sample_rms[sel])), 4),
        })
    return {
        "analysis_version": 1,
        "analysis": "archived IQ offline relative carrier amplitude, not calibrated RF power",
        "source_path": str(path),
        "source_iq_raw_sha256_verified": raw_sha,
        "sample_rate_hz": FS,
        "manual_tuner_gain_tenth_db": meta.get("manual_gain_tenth_db"),
        "local_capture_carrier_frequency_hz": round(433_750_000 + f0, 2),
        "frequency_caution": "RTL-SDR frequency offset unknown; this is not an absolute radio frequency measurement",
        "event_start_seconds": round(onset, 5),
        "event_end_seconds": round(off, 5),
        "event_duration_seconds": round(off - onset, 5),
        "normalizing_100percent_reference_adc": round(reference, 4),
        "deep_fade_detection_threshold_adc": round(fade_threshold, 4),
        "deep_fade_caution": "Counts 1-ms coherent received power dips, not statistically proven PA-only root cause",
        "firmware_completed_7_step_run": True,
        "original_module_restored": True,
        "original_radio_frequency_restored": True,
        "stage_duration_seconds": STAGE_SECONDS,
        "stage_plateau_center_samples_seconds": [.010, .065],
        "stages": stages,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("iq", nargs="+", type=Path)
    p.add_argument("--write-reports", action="store_true")
    args = p.parse_args()
    for path in args.iq:
        result = analyze_capture(path)
        print(json.dumps(result, indent=2))
        if args.write_reports:
            dest = path.with_suffix(".analysis.json")
            with dest.open("x") as out:
                json.dump(result, out, indent=2)
                out.write("\n")


if __name__ == "__main__":
    main()
