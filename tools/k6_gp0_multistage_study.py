#!/usr/bin/env python3
"""Offline halfband plus low-pass/downsample exploration for GP0 popcounts.

The filters run on CRC-verified group counts in host Python. A factor-two
halfband stage reduces rate, then a second low-pass runs at half rate before
the unchanged production Rust fractional decimator. No device or firmware
image is touched. This is a signal experiment, not MCU timing evidence.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import types

import numpy as np
from scipy.signal import firwin, lfilter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pkg = sys.modules.get("tools")
if pkg is None or str(ROOT / "tools") not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules["tools"] = pkg
from tools.k6_gp0_fidelity_lab import GROUP_DENOMINATOR, GROUP_WEIGHT, TRIM_PCM, fit_tone, load_groups, run
from tools.k6_gp0_noise_filter_model import SOURCE, build_runner

RAW_FS = 93303.0
STAGE1_N = 31
STAGE2_LENGTHS = (31, 63)
STAGE1_CUTOFF = 16000.0
STAGE2_CUTOFF = 3800.0
Q = 32768
INPUTS = (
    ("gen-off-20261127-01", None, True),
    ("gen-500-20261127-03", 500, False),
    ("gen-1000-20261127-01", 1000, True),
    ("gen-2800-20261127-01", 2800, False),
)


def taps(length: int, cutoff: float, fs: float) -> np.ndarray:
    h = firwin(length, cutoff, fs=fs, window=("kaiser", 5.0))
    return np.rint(h * Q).astype(np.int64) / Q


def preprocess(counts: np.ndarray, h1: np.ndarray, h2: np.ndarray) -> np.ndarray:
    # Filter centered deviations so lfilter's zero initial state does not
    # create a large artificial step from an uncentered mean of 32 counts.
    # Preserve fractional intermediates through both stages and quantize once
    # after restoring the count-domain DC level expected by the decimator.
    centered = counts.astype(np.float64) - 32.0
    a = lfilter(h1, [1.0], centered)[::2]
    b = lfilter(h2, [1.0], a)
    return np.clip(np.rint(b + 32.0), 0, 64).astype(np.uint8)


def build_half_rate_runner(out: Path) -> Path:
    source = build_runner(SOURCE.read_text())
    if source.count("const FS:u32=87260;") != 1 or source.count("let group_weight=7500u32;") != 1:
        raise ValueError("production runner source changed")
    # Each retained sample represents two original group intervals. Double
    # the integration weight while retaining the production phase denominator.
    source = source.replace("let group_weight=7500u32;", "let group_weight=15000u32;", 1)
    out.write_text(source)
    exe = out.with_suffix("")
    p = subprocess.run(["rustc", "--edition=2021", "-O", "--cfg", "k6_sotto_gp0_fast_isr",
                        "--cfg", "k6_sotto_gp0_clock_7500", str(out), "-o", str(exe)],
                       capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr[:2000])
    return exe


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-root", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite")
    inputs = [(name, hz, *load_groups(args.raw_root, name, partial)) for name, hz, partial in INPUTS]
    report = {
        "schema": 1, "offline_only": True,
        "filter": "Q15 Kaiser beta 5 factor-2 decimation lowpass (16kHz), then Q15 Kaiser 3.8kHz lowpass",
        "stage1_taps": STAGE1_N, "stage1_cutoff_hz": STAGE1_CUTOFF, "stage2_tap_counts": list(STAGE2_LENGTHS),
        "group_rate_hz": "per-capture CRC metadata (~92.0 or 93.3kHz)", "intermediate_rate_hz": "per-capture metadata / 2",
        "decimator": "unchanged Rust production fractional decimator and denominator 87260; integration weight doubled to 15000 for half-rate samples",
        "rounding": "fractional first-stage outputs retained through stage two, then rounded to 0..64 counts once",
        "warnings": [
            "Source spans are 32-44ms; these are preliminary tone results, not population-level evidence.",
            "Host preprocessing does not establish ARM cycles, latency, ISR fit, or exact MCU rounding behavior.",
            "The 31-tap first-stage filter is intentionally non-halfband to leave a guard band before factor-two aliasing.",
            "Two filtering stages add phase/group delay; paired comparisons align the same source-time window after the 32 PCM startup trim.",
        ], "results": {}
    }
    with tempfile.TemporaryDirectory(prefix="gp0-multistage-") as td:
        exe = build_half_rate_runner(Path(td) / "half_rate.rs")
        # Compile the ordinary-rate runner from the same exact Rust source.
        normal_src = Path(td) / "normal.rs"
        normal_src.write_text(build_runner(SOURCE.read_text()))
        normal_exe = Path(td) / "normal"
        cp = subprocess.run(["rustc", "--edition=2021", "-O", "--cfg", "k6_sotto_gp0_fast_isr",
                             "--cfg", "k6_sotto_gp0_clock_7500", str(normal_src), "-o", str(normal_exe)],
                            capture_output=True, text=True, timeout=60)
        if cp.returncode:
            raise RuntimeError(cp.stderr[:2000])
        for length in STAGE2_LENGTHS:
            row = {"tone_results": {}, "generator_off_rms_ratio_db": None,
                   "theoretical_dense_macs_per_second": int(STAGE1_N * RAW_FS + length * RAW_FS / 2)}
            off_db = None
            for name, hz, groups, meta in inputs:
                input_rate = float(meta["groups_per_second"])
                h1 = taps(STAGE1_N, STAGE1_CUTOFF, input_rate)
                h2 = taps(length, STAGE2_CUTOFF, input_rate / 2)
                y0 = run(normal_exe, groups).astype(np.float64) / 32768
                filtered = preprocess(groups, h1, h2)
                y1 = run(exe, filtered).astype(np.float64) / 32768
                # Map the causal two-stage FIR output back to the same source
                # time interval as the unfiltered baseline. Stage 1 operates
                # at the raw rate; stage 2 operates at half that rate.
                delay_groups = (STAGE1_N - 1) / 2 + (length - 1)
                delay_pcm = delay_groups * GROUP_WEIGHT / GROUP_DENOMINATOR
                delay_samples = int(round(delay_pcm))
                common_start = TRIM_PCM
                common_end = min(len(y0), len(y1) - delay_samples)
                if common_end - common_start < 120:
                    raise ValueError("aligned multistage comparison window is too short")
                y0_window = y0[common_start:common_end]
                y1_window = y1[common_start + delay_samples:common_end + delay_samples]
                if hz is None:
                    off_db = 20 * math.log10(max(np.std(y1_window), 1e-15) /
                                             max(np.std(y0_window), 1e-15))
                    row["generator_off_rms_ratio_db"] = round(off_db, 3)
                    row["generator_off_groups_in"] = int(len(groups))
                else:
                    fs0 = meta["groups_per_second"] * GROUP_WEIGHT / GROUP_DENOMINATOR
                    fs1 = fs0  # denominator and input rate both halved
                    f0, f1 = fit_tone(y0_window, fs0, hz, trim=0), fit_tone(y1_window, fs1, hz, trim=0)
                    row["tone_results"][str(hz)] = {
                        "baseline_snr_db": f0["snr_db"], "candidate_snr_db": f1["snr_db"],
                        "snr_gain_db": round(f1["snr_db"] - f0["snr_db"], 4),
                        "tone_level_change_db": round(20 * math.log10(max(f1["tone_peak_fs"], 1e-15) /
                                                                           max(f0["tone_peak_fs"], 1e-15)), 4),
                        "source_groups": int(len(groups)), "output_samples": int(len(y1)),
                    }
            gains = [x["snr_gain_db"] for x in row["tone_results"].values()]
            row["mean_tone_snr_gain_db"] = round(float(np.mean(gains)), 4)
            row["worst_tone_snr_gain_db"] = round(float(min(gains)), 4)
            report["results"][str(length)] = row
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    for length, row in report["results"].items():
        print(f"stage2 {length} taps: mean {row['mean_tone_snr_gain_db']:+.2f}dB "
              f"worst {row['worst_tone_snr_gain_db']:+.2f}dB; off RMS {row['generator_off_rms_ratio_db']:+.2f}dB")


if __name__ == "__main__":
    main()
