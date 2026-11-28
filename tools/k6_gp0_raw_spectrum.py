#!/usr/bin/env python3
"""CRC-gated, offline raw GP0 popcount PSD and residual spectrum analysis.

Measures count-domain energy below and above the prospective 8kHz PCM Nyquist
frequency. Tone runs have their fitted fundamental removed before residual PSD
integration. This describes the observed grouped-popcount stream; it does not
identify the physical noise mechanism or reconstruct the underlying 1-bit PDM.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
# Some Python environments have an unrelated top-level `tools` package that
# shadows this repository's namespace package. Bind internal imports to this
# checkout so direct script invocation is deterministic.
pkg = sys.modules.get("tools")
local_tools = str(ROOT / "tools")
if pkg is None or local_tools not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [local_tools]
    sys.modules["tools"] = pkg

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import welch

from tools.k6_gp0_fidelity_lab import load_groups
from tools.k6_gp0_popcount_air import parse_iq_complex64


BANDS = ((0, 300), (300, 800), (800, 1500), (1500, 2500),
         (2500, 3500), (3500, 4000), (4000, 8000), (8000, 20000),
         (20000, 46650))


def remove_tone(x: np.ndarray, fs: float, target_hz: float) -> tuple[np.ndarray, float, float]:
    t = np.arange(len(x), dtype=np.float64) / fs

    def fit(hz: float):
        basis = np.column_stack((np.sin(2 * np.pi * hz * t),
                                 np.cos(2 * np.pi * hz * t),
                                 np.ones(len(x))))
        coef = np.linalg.lstsq(basis, x, rcond=None)[0]
        err = x - basis @ coef
        return float(np.mean(err * err)), float(np.hypot(coef[0], coef[1])), err

    opt = minimize_scalar(lambda hz: fit(hz)[0],
                          bounds=(target_hz * .97, target_hz * 1.03),
                          method="bounded")
    _, peak, residual = fit(float(opt.x))
    return residual, float(opt.x), peak


def spectrum(x: np.ndarray, fs: float) -> dict:
    if len(x) < 512 or not np.isfinite(x).all():
        raise ValueError("need at least 512 finite contiguous popcount groups")
    nperseg = min(1024, len(x))
    f, p = welch(x, fs=fs, window="hann", nperseg=nperseg,
                 noverlap=nperseg // 2, detrend="constant", scaling="density")
    energies = {}
    densities = {}
    total = 0.0
    for low, high in BANDS:
        sel = (f >= low) & (f < high if high < fs / 2 else f <= high)
        value = float(np.trapz(p[sel], f[sel])) if np.count_nonzero(sel) > 1 else 0.0
        energies[f"{low}-{high}"] = value
        densities[f"{low}-{high}"] = value / (high - low)
        total += value
    below = sum(energies[f"{lo}-{hi}"] for lo, hi in BANDS if hi <= 4000)
    above = sum(energies[f"{lo}-{hi}"] for lo, hi in BANDS if lo >= 4000)
    return {
        "welch_nperseg_groups": nperseg,
        "band_power_counts_squared": {k: round(v, 8) for k, v in energies.items()},
        "mean_psd_density_counts_squared_per_hz": {
            k: round(v, 10) for k, v in densities.items()
        },
        "band_power_fraction_percent": {
            k: round(100 * v / max(total, 1e-15), 3) for k, v in energies.items()
        },
        "power_below_4khz": round(below, 8),
        "power_above_4khz": round(above, 8),
        "above_to_below_4khz_db": round(10 * math.log10(max(above, 1e-15) /
                                                            max(below, 1e-15)), 3),
        "mean_density_above_to_below_4khz_db": round(
            10 * math.log10(max(above / max(fs / 2 - 4000, 1), 1e-15) /
                            max(below / 4000, 1e-15)), 3),
        "note": "Finite-window Welch PSD of group counts; band totals are descriptive, not a calibrated analog noise PSD.",
    }


def load_air_holdout(path: Path) -> tuple[np.ndarray, float, dict]:
    frames, diag = parse_iq_complex64(path)
    valid = [p for p in frames if p]
    if not valid:
        raise ValueError("no CRC-valid GP0 packets in air-capture IQ")
    identities = {(p["token"], p["capture_100us"], p["samples"], p["fs"]) for p in valid}
    if len(identities) != 1:
        raise ValueError("mixed GP0 air-capture session identities")
    by_seq: dict[int, bytes] = {}
    for p in valid:
        if p["seq"] in by_seq and by_seq[p["seq"]] != p["payload"]:
            raise ValueError("conflicting duplicate GP0 packet")
        by_seq[p["seq"]] = p["payload"]
    runs: list[list[int]] = []
    for seq in sorted(by_seq):
        if not runs or seq != runs[-1][-1] + 1:
            runs.append([])
        runs[-1].append(seq)
    seqs = max(runs, key=lambda run: (len(run), -run[0]))
    groups = np.frombuffer(b"".join(by_seq[i] for i in seqs), dtype=np.uint8).copy()
    ticks = next(iter(identities))[1]
    rate = 4096 * 10000 / ticks
    return groups, rate, {
        "crc_valid_unique_packets": len(by_seq),
        "verified_contiguous_run": [seqs[0], seqs[-1]],
        "capture_ticks_100us": ticks,
        "decoder_diagnostics": diag,
    }


def analyze(raw_root: Path, air_c64: Path | None) -> dict:
    cases = [
        ("generator_off", "gen-off-20261127-01", None, True,
         "Confirmed generator-off condition; CRC-hole-free longest span only."),
        ("first_500hz_rf_take_tone_absent", "gen-500-20261127-01", None, False,
         "Tone was absent in the capture; generator state/routing was not independently proved."),
        ("500hz_tone", "gen-500-20261127-03", 500.0, False,
         "CRC-complete 0.1Vpp generator capture."),
        ("1000hz_tone", "gen-1000-20261127-01", 1000.0, True,
         "Longest contiguous CRC-valid packet run; two packet gaps retained."),
        ("2800hz_tone", "gen-2800-20261127-01", 2800.0, False,
         "CRC-complete 0.1Vpp generator capture."),
    ]
    result = {
        "schema": 1,
        "offline_only": True,
        "device_contact": False,
        "measurement": "Welch spectra of contiguous 0..64 group popcounts; tone fundamental removed only for residual spectra",
        "bands_hz": [list(b) for b in BANDS],
        "prospective_pcm_nyquist_hz": 4000,
        "captures": {},
        "limitations": [
            "Grouped counts are not the original individual PDM bits.",
            "This PSD cannot distinguish PDM quantization/folding, analog noise, GPIO timing or clock effects.",
            "Short physical runs and finite Welch windows limit fine spectral resolution.",
            "A 4kHz split describes possible fold-down energy, not the exact alias transfer of the fractional firmware decimator.",
        ],
    }
    for label, name, tone, partial, provenance in cases:
        x, meta = load_groups(raw_root, name, partial)
        row = {
            "provenance": provenance,
            "raw_groups": int(len(x)),
            "groups_per_second": round(float(meta["groups_per_second"]), 3),
            "crc_verified_packets": meta["crc_verified_packets"],
            "verified_packet_run": meta["verified_run"],
            "raw_count_std": round(float(np.std(x)), 6),
            "raw_spectrum": spectrum(x.astype(np.float64), meta["groups_per_second"]),
        }
        if tone is not None:
            residual, fitted, peak = remove_tone(x.astype(np.float64),
                                                  meta["groups_per_second"], tone)
            row["tone_fit"] = {
                "target_hz": tone,
                "fitted_hz": round(fitted, 4),
                "peak_group_counts": round(peak, 5),
                "residual_rms_counts": round(float(np.std(residual)), 5),
            }
            row["tone_removed_residual_spectrum"] = spectrum(
                residual, meta["groups_per_second"])
        result["captures"][label] = row
    if air_c64 is not None:
        x, rate, meta = load_air_holdout(air_c64)
        residual, fitted, peak = remove_tone(x.astype(np.float64), rate, 500.0)
        result["captures"]["matched_0db_air_500hz_holdout"] = {
            "provenance": "Independent attended 0 dB RF capture; longest CRC-valid run only.",
            "raw_groups": int(len(x)),
            "groups_per_second": round(rate, 3),
            **meta,
            "raw_count_std": round(float(np.std(x)), 6),
            "tone_fit": {"target_hz": 500, "fitted_hz": round(fitted, 4),
                         "peak_group_counts": round(peak, 5),
                         "residual_rms_counts": round(float(np.std(residual)), 5)},
            "raw_spectrum": spectrum(x.astype(np.float64), rate),
            "tone_removed_residual_spectrum": spectrum(residual, rate),
        }
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-root", type=Path, required=True)
    ap.add_argument("--air-c64", type=Path,
                    help="optional new 128ksps GP0 air-capture IQ; CRC-verified longest run is used")
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing metrics")
    result = analyze(args.raw_root, args.air_c64)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")
    for name, row in result["captures"].items():
        raw = row["raw_spectrum"]
        residual = row.get("tone_removed_residual_spectrum", raw)
        print(f"{name:38s} N={row['raw_groups']:4d} rate={row['groups_per_second']:8.1f} "
              f"raw>4k {raw['above_to_below_4khz_db']:+6.2f}dB "
              f"residual>4k {residual['above_to_below_4khz_db']:+6.2f}dB")


if __name__ == "__main__":
    main()
