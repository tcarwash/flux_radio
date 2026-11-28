#!/usr/bin/env python3
"""Offline long-FIR pre-decimator study using CRC-gated physical popcounts.

Builds temporary HOST Rust runners around the production fractional decimator,
with experimental Q15 FIR taps applied to bounded group counts. FIR history is
host-only and unbounded relative to MCU RAM/ISR constraints; this is a signal
quality study, not firmware-ready code. Raw captures stay local; output is
metrics only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import types

import numpy as np
from scipy.signal import firwin, freqz

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pkg = sys.modules.get("tools")
local_tools = str(ROOT / "tools")
if pkg is None or local_tools not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [local_tools]
    sys.modules["tools"] = pkg

from tools.k6_gp0_fidelity_lab import fit_tone, load_groups, run
from tools.k6_gp0_noise_filter_model import SOURCE, build_runner, compile_variants


GROUP_DENOMINATOR = 87260
GROUP_WEIGHT = 7500
TRIM_PCM = 32
INPUTS = (
    ("gen-off-20261127-01", None, True),
    ("gen-500-20261127-01", None, False),  # documented 500Hz tone absent; source state uncertain
    ("gen-500-20261127-03", 500, False),
    ("gen-1000-20261127-01", 1000, True),
    ("gen-2800-20261127-01", 2800, False),
)
LENGTHS = (31, 63, 127, 255)
CUTOFF_HZ = 3800.0
BETA = 5.0
Q15 = 32768
BOOTSTRAP_REPLICATES = 3000
BOOTSTRAP_BLOCK_SAMPLES = 32


def quantized_kernel(length: int) -> tuple[int, ...]:
    coeff = firwin(length, CUTOFF_HZ, fs=93303.0,
                   window=("kaiser", BETA), pass_zero="lowpass")
    q = np.rint(coeff * Q15).astype(np.int64)
    q[length // 2] += Q15 - int(q.sum())
    if int(q.sum()) != Q15 or np.max(np.abs(q)) > 32767:
        raise ValueError("Q15 FIR coefficient quantization failed")
    return tuple(int(v) for v in q)


def build_long_fir_runner(length: int, coeff: tuple[int, ...], out: Path) -> Path:
    source = build_runner(SOURCE.read_text())
    required = (
        "const FS:u32=87260;\n",
        "    fs:u32,phase:u32,area:i32,dc:i32,samples:u32,clipped:u32,",
        "    fs:0,phase:0,area:0,dc:0,samples:0,clipped:0,",
        "#[cfg(not(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11,k6_sotto_gp0_fir11_late,k6_sotto_gp0_cic2,k6_sotto_gp0_cic3)))]\n    let x=raw;",
        "#[cfg(not(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11,k6_sotto_gp0_fir11_late,k6_sotto_gp0_cic2,k6_sotto_gp0_cic3)))]\n    let num=mag<<8;",
    )
    if any(source.count(item) != 1 for item in required):
        raise ValueError("firmware source shape changed; refusing unsafe rewrite")
    source = source.replace(required[0], required[0] + f"const LAB_N:usize={length};\n", 1)
    source = source.replace(required[1], required[1] + "\n    lab_taps:[i8;LAB_N],lab_head:u16,", 1)
    source = source.replace(required[2], required[2] + "\n    lab_taps:[0;LAB_N],lab_head:0,", 1)

    old_x = required[3]
    new_x = "#[cfg(k6_sotto_gp0_longfir)]\n    let x={\n"
    new_x += f"        const N:usize={length}; const DEN:i64={Q15};\n"
    new_x += f"        const W:[i32;N]=[{','.join(map(str, coeff))}];\n"
    new_x += "        let h=d.lab_head as usize; d.lab_taps[h]=raw as i8;\n"
    new_x += "        d.lab_head=if h+1==N{0}else{(h+1) as u16};\n"
    new_x += "        let mut acc=0i64; let mut j=0usize;\n"
    new_x += "        while j<N{let pos=(h+N-j)%N;acc+=W[j] as i64*(d.lab_taps[pos] as i64);j+=1;}\n"
    new_x += "        (acc*128/DEN) as i32\n    };\n" + old_x
    new_x = new_x.replace(
        "#[cfg(not(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11,k6_sotto_gp0_fir11_late,k6_sotto_gp0_cic2,k6_sotto_gp0_cic3)))]\n    let x=raw;",
        "#[cfg(not(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11,k6_sotto_gp0_fir11_late,k6_sotto_gp0_cic2,k6_sotto_gp0_cic3,k6_sotto_gp0_longfir)))]\n    let x=raw;",
        1,
    )
    source = source.replace(old_x, new_x, 1)
    source = source.replace(required[4], required[4].replace("k6_sotto_gp0_cic3", "k6_sotto_gp0_cic3,k6_sotto_gp0_longfir"), 1)
    # The custom variant uses a Q7 prefiltered input, matching the Q8 scale of
    # AA3/FIR11. Keep its declaration separate from existing cfg variants.
    anchor = "#[cfg(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11))]\n    let num=mag<<1;"
    if source.count(anchor) != 1:
        raise ValueError("firmware Q7 scaling branch changed")
    source = source.replace(anchor, anchor + "\n    #[cfg(k6_sotto_gp0_longfir)]\n    let num=mag<<1;", 1)
    out.write_text(source)
    exe = out.with_suffix("")
    p = subprocess.run(["rustc", "--edition=2021", "-O",
                        "--cfg", "k6_sotto_gp0_fast_isr",
                        "--cfg", "k6_sotto_gp0_clock_7500",
                        "--cfg", "k6_sotto_gp0_longfir",
                        str(out), "-o", str(exe)],
                       capture_output=True, text=True, timeout=60)
    if p.returncode:
        raise RuntimeError(f"rustc failed for {length}-tap FIR: {p.stderr[:2000]}")
    return exe


def response_metrics(coeff: tuple[int, ...]) -> dict:
    h = np.asarray(coeff, dtype=np.float64) / Q15
    f, H = freqz(h, worN=32768, fs=93303.0)
    mag = np.maximum(np.abs(H), 1e-12)
    points = {}
    for hz in (500, 1000, 2000, 2800, 3200, 3500, 3800, 4000, 4500, 6000, 8000, 12000, 20000):
        points[str(hz)] = round(float(20 * np.log10(np.interp(hz, f, mag))), 3)
    passband = 20 * np.log10(mag[f <= 3200])
    stopband_power = float(np.mean(mag[f >= 4000] ** 2))
    return {
        "response_db_at_hz": points,
        "passband_ripple_0_3200hz_db": round(float(np.max(passband) - np.min(passband)), 3),
        "white_noise_power_rejection_above_4khz_db": round(10 * math.log10(max(stopband_power, 1e-15)), 3),
        "note": "Analytical FIR response; white-noise stopband metric is a filter property, not a measured GP0 improvement.",
    }


def tone_residual(pcm: np.ndarray, fs: float, fit: dict) -> np.ndarray:
    y = np.asarray(pcm, dtype=np.float64)[TRIM_PCM:]
    t = np.arange(len(y), dtype=np.float64) / fs
    hz = float(fit["fitted_hz"])
    basis = np.column_stack((np.sin(2 * np.pi * hz * t),
                             np.cos(2 * np.pi * hz * t), np.ones(len(y))))
    coef = np.linalg.lstsq(basis, y, rcond=None)[0]
    return y - basis @ coef


def paired_block_bootstrap(base_resid: np.ndarray, candidate_resid: np.ndarray,
                           amplitude_ratio_db: float, seed: int) -> dict:
    if len(base_resid) != len(candidate_resid) or len(base_resid) < 160:
        raise ValueError("paired residuals must have at least 160 aligned samples")
    n = len(base_resid)
    block = min(BOOTSTRAP_BLOCK_SAMPLES, n // 4)
    blocks = math.ceil(n / block)
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n - block + 1,
                          size=(BOOTSTRAP_REPLICATES, blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :])
    idx = idx.reshape(BOOTSTRAP_REPLICATES, -1)[:, :n]
    b = base_resid[idx]
    c = candidate_resid[idx]
    b -= b.mean(axis=1, keepdims=True)
    c -= c.mean(axis=1, keepdims=True)
    noise_gain = 20 * np.log10(np.maximum(np.sqrt(np.mean(b * b, axis=1)), 1e-15) /
                               np.maximum(np.sqrt(np.mean(c * c, axis=1)), 1e-15))
    snr_gain = noise_gain + amplitude_ratio_db
    q = np.quantile(snr_gain, (.025, .5, .975))
    return {
        "block_length_samples": block,
        "replicates": BOOTSTRAP_REPLICATES,
        "conditional_snr_gain_db_95pct_interval": [round(float(q[0]), 3), round(float(q[2]), 3)],
        "conditional_snr_gain_db_median": round(float(q[1]), 3),
        "bootstrap_fraction_above_zero": round(float(np.mean(snr_gain > 0)), 4),
        "interpretation_limit": "Within-record paired moving-block bootstrap of residuals only; tone amplitude is held at the full-record fit. Does not estimate between-session or source-population uncertainty.",
    }


def analyze(raw_root: Path) -> dict:
    inputs = [(name, hz, *load_groups(raw_root, name, partial))
              for name, hz, partial in INPUTS]
    result = {
        "schema": 1,
        "offline_only": True,
        "actual_firmware_fractional_decimator": True,
        "custom_long_fir": "Q15 Kaiser beta 5 lowpass, 3.8kHz cutoff, applied to raw group counts before the unchanged host Rust decimator",
        "input_stage_fir_host_only": True,
        "clock_weight": GROUP_WEIGHT,
        "denominator": GROUP_DENOMINATOR,
        "startup_trim_pcm_samples": TRIM_PCM,
        "capture_manifest": {name: {k: v for k, v in meta.items() if k != "sample_sha256"}
                             for name, hz, groups, meta in inputs},
        "filters": {},
        "warnings": [
            "32-44ms physical captures remain too short for population-level noise estimates.",
            "Filter ranking uses only these measured tones and one generator-off span; it is exploratory and may overfit.",
            "The long history state and per-group MAC count are not MCU RAM/ISR-qualified.",
            "The source is grouped popcount, not the original 1-bit PDM stream.",
            "Analytical stopband response assumes white noise; measured source noise is only approximately flat by broad-band PSD.",
            "A 3.8kHz FIR cutoff places the 3.2kHz response near its transition region; use response values, not cutoff label, for speech-band claims.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="gp0-long-fir-study-") as td:
        temp = Path(td)
        progs = compile_variants(temp)
        for length in LENGTHS:
            kernel = quantized_kernel(length)
            exe = build_long_fir_runner(length, kernel, temp / f"fir{length}.rs")
            progs[f"fir{length}_kaiser3800"] = exe
        pcm_by_mode: dict[str, dict[str, np.ndarray]] = {}
        for mode, exe in progs.items():
            row = {"tone_results": {}, "noise_controls": {}, "analytical_response": None}
            pcm_by_mode[mode] = {}
            for name, hz, groups, meta in inputs:
                pcm = run(exe, groups).astype(np.float64) / 32768.0
                if hz is None:
                    row["noise_controls"][name] = {
                        "pcm_rms_fs_after_trim": round(float(np.std(pcm[TRIM_PCM:])), 7),
                        "pcm_samples": len(pcm),
                    }
                else:
                    fs = meta["groups_per_second"] * GROUP_WEIGHT / GROUP_DENOMINATOR
                    row["tone_results"][str(hz)] = fit_tone(pcm, fs, hz, trim=TRIM_PCM)
                    pcm_by_mode[mode][str(hz)] = pcm
            if mode.startswith("fir") and mode.endswith("_kaiser3800"):
                length = int(mode[3:].split("_")[0])
                row["analytical_response"] = response_metrics(quantized_kernel(length))
            result["filters"][mode] = row
    baseline = result["filters"]["legacy"]
    for mode, row in result["filters"].items():
        for hz, fit in row["tone_results"].items():
            ref = baseline["tone_results"][hz]
            fit["snr_gain_vs_legacy_db"] = round(fit["snr_db"] - ref["snr_db"], 4)
            fit["tone_attenuation_vs_legacy_db"] = round(
                20 * math.log10(max(fit["tone_peak_fs"], 1e-15) /
                                max(ref["tone_peak_fs"], 1e-15)), 4)
            fit["tone_peak_fs"] = round(fit["tone_peak_fs"], 7)
            fit["residual_rms_fs"] = round(fit["residual_rms_fs"], 7)
    result["within_record_paired_bootstrap"] = {}
    base_mode = "legacy"
    for hz in ("500", "1000", "2800"):
        source_meta = next(meta for name, tone, groups, meta in inputs if str(tone) == hz)
        fs = source_meta["groups_per_second"] * GROUP_WEIGHT / GROUP_DENOMINATOR
        base_fit = result["filters"][base_mode]["tone_results"][hz]
        base_resid = tone_residual(pcm_by_mode[base_mode][hz], fs, base_fit)
        result["within_record_paired_bootstrap"][hz] = {}
        for index, mode in enumerate(result["filters"]):
            if mode == base_mode:
                continue
            fit = result["filters"][mode]["tone_results"][hz]
            resid = tone_residual(pcm_by_mode[mode][hz], fs, fit)
            amp_gain = 20 * math.log10(max(fit["tone_peak_fs"], 1e-15) /
                                       max(base_fit["tone_peak_fs"], 1e-15))
            result["within_record_paired_bootstrap"][hz][mode] = paired_block_bootstrap(
                base_resid, resid, amp_gain,
                seed=20261127 + int(hz) + index * 97)
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-root", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing metrics")
    result = analyze(args.raw_root)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    rows = []
    for name, row in result["filters"].items():
        gains = [fit["snr_gain_vs_legacy_db"] for fit in row["tone_results"].values()]
        off = row["noise_controls"].get("gen-off-20261127-01", {}).get("pcm_rms_fs_after_trim")
        rows.append((name, min(gains) if gains else 0.0,
                     float(np.mean(gains)) if gains else 0.0, off))
    for name, worst, mean, off in sorted(rows, key=lambda x: x[2], reverse=True):
        print(f"{name:20s} worst-tone-SNR {worst:+6.2f}dB mean {mean:+6.2f}dB offRMS {off}")


if __name__ == "__main__":
    main()
