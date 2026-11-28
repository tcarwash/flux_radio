#!/usr/bin/env python3
"""Excite real generator-off GP0 count noise with known tones offline.

The synthetic tone is injected into a CRC-contiguous captured count-noise
sequence, quantized to the real 0..64 alphabet, then replayed through the
actual Rust decimator and host-only candidate filters. This tests filter
behavior under one measured noise realization; it is not a new RF capture,
speech test, or MCU qualification.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import types

import numpy as np
from scipy.signal import firwin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pkg = sys.modules.get("tools")
if pkg is None or str(ROOT / "tools") not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules["tools"] = pkg
from tools.k6_gp0_fidelity_lab import GROUP_DENOMINATOR, GROUP_WEIGHT, load_groups, run, compile_lab
from tools.k6_gp0_long_fir_study import build_long_fir_runner

SOURCE_CAPTURE = "gen-off-20261127-01"
TONES_HZ = (500, 1000, 2000, 2800, 3200)
INPUT_AMPLITUDES_COUNTS = (0.8, 1.6, 3.2)
PHASES_RAD = tuple(float(x) for x in np.linspace(0, 2 * np.pi, 12, endpoint=False))
LONG_TAPS = (127, 255)
TRIM = 32
Q15 = 32768


def quantized_kernel_at_rate(length: int, fs: float) -> tuple[int, ...]:
    coeff = firwin(length, 3800.0, fs=fs, window=("kaiser", 5.0))
    q = np.rint(coeff * Q15).astype(np.int64)
    q[length // 2] += Q15 - int(q.sum())
    if int(q.sum()) != Q15 or np.max(np.abs(q)) > 32767:
        raise ValueError("Q15 FIR quantization failed")
    return tuple(int(x) for x in q)


def fit_known_tone(pcm: np.ndarray, fs: float, hz: float, start: int, end: int) -> dict:
    """Measure the known injected frequency without per-output frequency search."""
    y = np.asarray(pcm, dtype=np.float64)[start:end]
    if len(y) < 120:
        raise ValueError("aligned comparison window is too short")
    t = np.arange(len(y), dtype=np.float64) / fs
    basis = np.column_stack((np.sin(2 * np.pi * hz * t),
                             np.cos(2 * np.pi * hz * t), np.ones(len(y))))
    coeff = np.linalg.lstsq(basis, y, rcond=None)[0]
    residual = y - basis @ coeff
    peak = float(np.hypot(coeff[0], coeff[1]))
    noise = float(np.sqrt(np.mean(residual ** 2)))
    return {"tone_peak_raw_pcm_units": peak,
            "residual_rms_raw_pcm_units": noise,
            "snr_db": 20 * math.log10(max(peak, 1e-15) /
                                      (math.sqrt(2) * max(noise, 1e-15)))}


def known_linear_phase_delay_output_samples(mode: str) -> float | None:
    if mode == "legacy":
        return 0.0
    if mode.startswith("fir"):
        digits = ""
        for char in mode[3:]:
            if not char.isdigit():
                break
            digits += char
        if digits:
            return ((int(digits) - 1) / 2) * GROUP_WEIGHT / GROUP_DENOMINATOR
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-root", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    noise, meta = load_groups(args.raw_root, SOURCE_CAPTURE, True)
    center = float(np.mean(noise))
    fs_input = float(meta["groups_per_second"])
    fs_output = fs_input * GROUP_WEIGHT / GROUP_DENOMINATOR
    report = {
        "schema": 1, "offline_only": True, "device_contact": False,
        "source_capture": meta,
        "noise_source": "one CRC-verified contiguous generator-off physical GP0 popcount sequence",
        "tone_injection": {"frequencies_hz": list(TONES_HZ),
                           "peak_amplitudes_counts": list(INPUT_AMPLITUDES_COUNTS),
                           "phase_offsets_radians": list(PHASES_RAD),
                           "count_domain_mean": round(center, 5),
                           "group_rate_hz": round(fs_input, 3),
                           "frequency_fit": "fixed to the exact injected frequency; no optimizer"},
        "fir_design_sample_rate_hz": round(fs_input, 3),
        "output_rate_hz": round(fs_output, 3), "minimum_startup_trim_pcm_samples": TRIM,
        "interpretation_limits": [
            "One noise realization from one short generator-off capture; not session/population uncertainty.",
            "Tones are synthetic, added to captured noise after the ADC count quantization; this is not a measured microphone or RF tone path.",
            "SNR is a fixed-frequency residual proxy, not speech intelligibility or perceptual quality.",
            "Long FIR candidates use host-only temporary state and are not MCU qualified.",
            "Input frequencies above the output Nyquist are excluded; 3.2kHz is the upper in-band probe.",
            "Linear-phase FIR comparison windows are aligned using their analytic group delay; IIR and AA3 delay is not compensated.",
        ],
        "filters": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-empirical-noise-injection-") as td:
        temp = Path(td)
        programs = compile_lab(temp)
        for length in LONG_TAPS:
            exe = build_long_fir_runner(length, quantized_kernel_at_rate(length, fs_input),
                                        temp / f"fir{length}.rs")
            programs[f"fir{length}_kaiser3800"] = exe
        rows = {name: {} for name in programs}
        delays = {name: known_linear_phase_delay_output_samples(name) for name in programs}
        known_delays = [d for d in delays.values() if d is not None]
        max_delay = max(known_delays)
        max_delay_samples = int(math.ceil(max_delay))
        # Treat output sample n from a causal FIR with delay D as representing
        # source time n-D. Use the same source-time interval for every variant.
        common_time_start = TRIM + max_delay_samples
        for hz in TONES_HZ:
            for amplitude in INPUT_AMPLITUDES_COUNTS:
                phase_rows = {name: [] for name in programs}
                for phase in PHASES_RAD:
                    t = np.arange(len(noise), dtype=np.float64) / fs_input
                    unbounded = noise.astype(np.float64) + amplitude * np.sin(
                        2 * np.pi * hz * t + phase)
                    clip_count = int(np.count_nonzero((unbounded < 0) | (unbounded > 64)))
                    synthetic = np.clip(np.rint(unbounded), 0, 64).astype(np.uint8)
                    for name, exe in programs.items():
                        pcm = run(exe, synthetic).astype(np.float64)
                        delay = delays[name]
                        delay_samples = int(round(delay)) if delay is not None else 0
                        start = common_time_start + delay_samples
                        end = len(pcm) - max_delay_samples + delay_samples
                        measurement = fit_known_tone(pcm, fs_output, hz, start, end)
                        measurement["input_count_clip_samples"] = clip_count
                        measurement["output_full_scale_clip_samples"] = int(
                            np.count_nonzero((pcm <= -32768) | (pcm >= 32767)))
                        measurement["window_start_pcm_sample"] = start
                        measurement["window_end_pcm_sample_exclusive"] = end
                        phase_rows[name].append(measurement)
                baseline = phase_rows["legacy"]
                for name, metrics in phase_rows.items():
                    gains = [metrics[i]["snr_db"] - baseline[i]["snr_db"] for i in range(len(PHASES_RAD))]
                    tone_db = [20 * math.log10(max(metrics[i]["tone_peak_raw_pcm_units"], 1e-15) /
                                               max(baseline[i]["tone_peak_raw_pcm_units"], 1e-15))
                               for i in range(len(PHASES_RAD))]
                    per_phase_abs = [x["snr_db"] for x in metrics]
                    per_phase_resid = [x["residual_rms_raw_pcm_units"] for x in metrics]
                    rows[name].setdefault(str(hz), {})[str(amplitude)] = {
                        "phase_snr_gain_db": [round(float(x), 3) for x in gains],
                        "median_snr_gain_db": round(float(np.median(gains)), 3),
                        "min_snr_gain_db": round(float(min(gains)), 3),
                        "phase_absolute_snr_db": [round(float(x), 3) for x in per_phase_abs],
                        "phase_residual_rms_raw_pcm_units": [round(float(x), 4) for x in per_phase_resid],
                        "phase_tone_level_change_db": [round(float(x), 3) for x in tone_db],
                        "max_input_count_clip_samples": max(x["input_count_clip_samples"] for x in metrics),
                        "max_output_full_scale_clip_samples": max(x["output_full_scale_clip_samples"] for x in metrics),
                        "phase_window_start_pcm_sample": [x["window_start_pcm_sample"] for x in metrics],
                    }
        report["filters"] = rows
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    for name, tones in report["filters"].items():
        vals = [entry["median_snr_gain_db"] for levels in tones.values() for entry in levels.values()]
        worst = min(entry["min_snr_gain_db"] for levels in tones.values() for entry in levels.values())
        print(f"{name:22s} mean {np.mean(vals):+.2f}dB worst-phase {worst:+.2f}dB")


if __name__ == "__main__":
    main()
