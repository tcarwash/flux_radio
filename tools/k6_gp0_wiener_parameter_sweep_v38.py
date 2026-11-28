#!/usr/bin/env python3
"""Tune causal Wiener-mask noise scale/floor through Sotto v3.8 offline.

Uses the RFSim banded GP0 noise model, an independent 500ms noise preamble,
eight speech vectors and three fixed noise seeds at 0.1Vpp. This is a broad
parameter screen, not a physical speech or MCU qualification.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import causal_denoise
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

VPP = 0.1
PREAMBLE_MS = 500
PREAMBLE_SEED_OFFSET = 1_000_003
DEFAULT_SCALES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)
DEFAULT_FLOORS = (0.02, 0.05)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--noise-scales", type=float, nargs="+", default=list(DEFAULT_SCALES))
    ap.add_argument("--mask-floors", type=float, nargs="+", default=list(DEFAULT_FLOORS))
    ap.add_argument("--include-smoothed", action="store_true",
                    help="also screen frequency and causal temporal-smoothed Wiener masks at floor 0.05")
    ap.add_argument("--time-previous-weights", type=float, nargs="+", default=[0.65],
                    help="previous-frame EMA weights used with --include-smoothed (0 disables temporal smoothing)")
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    if any(not 0 <= w < 1 for w in args.time_previous_weights):
        ap.error("time-previous weights must be in [0, 1)")
    cases = [("wiener", s, f, None) for s in args.noise_scales for f in args.mask_floors]
    if args.include_smoothed:
        cases += [("wiener_smoothed", s, 0.05, w)
                  for s in args.noise_scales for w in args.time_previous_weights]
    if any(s <= 0 for s in args.noise_scales) or any(not 0 <= f <= 1 for f in args.mask_floors):
        ap.error("noise scales must be positive and mask floors must be in [0, 1]")
    report = {
        "schema": 1,
        "offline_only": True,
        "source_model": "RFSim measured-response GP0 PCM with stationary banded OFF noise",
        "codec": "Sotto v3.8 C CLI, 8kHz",
        "input_vpp": VPP,
        "noise_rms_fs": OFF_PCM_RMS_FS,
        "preamble_ms": PREAMBLE_MS,
        "random_seeds": list(SEEDS),
        "sweep": [{"method": m, "noise_scale": s, "mask_floor": f,
                   **({"time_previous_weight": w} if w is not None else {})}
                  for m, s, f, w in cases],
        "limitations": [
            "Synthetic/statistical GP0 noise and laboratory 0.1Vpp input, not physical speech capture or calibrated acoustic speech.",
            "PESQ and ESTOI are proxies; no human listening result is included.",
            "Host-side processing has up to 32ms frame lookahead and is not qualified on the K6 MCU.",
            "The sweep fixes a 500ms independent stationary-noise preamble and does not test changing background noise.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-wiener-tune-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            original = resample_poly(read_flac(SAMPLE_ROOT / f"librispeech_{voice}__original16.flac", 16000), 1, 2)
            normalized = original / float(np.max(np.abs(original)))
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp, f"{voice}-clean")
            target_rms = float(np.std(clean_decoded))
            seed_rows = []
            for seed in SEEDS:
                noisy = k6_gp0_pcm(normalized, input_vpp=VPP,
                                   noise_rms_fs=OFF_PCM_RMS_FS, seed=seed,
                                   response="measured", noise_model="banded", clip=True)
                preamble = generate_gp0_noise(FS * PREAMBLE_MS // 1000,
                                              seed=seed + PREAMBLE_SEED_OFFSET,
                                              model="banded", rms_fs=OFF_PCM_RMS_FS)
                noisy_decoded = encode_decode(encoder, decoder, noisy, temp,
                                              f"{voice}-noisy-{seed}")
                row = {"seed": seed,
                       "noisy": score(original, noisy_decoded, target_rms),
                       "settings": {}}
                for method, scale, floor, time_previous in cases:
                    filtered = causal_denoise(noisy, preamble, method,
                                              noise_scale=scale, mask_floor=floor,
                                              **({"time_previous": time_previous}
                                                 if time_previous is not None else {}))
                    decoded = encode_decode(encoder, decoder, filtered, temp,
                                            f"{voice}-{seed}-{method}-{scale}-{floor}-{time_previous}")
                    name = f"{method}/scale={scale:g}/floor={floor:g}"
                    if time_previous is not None:
                        name += f"/time_previous={time_previous:g}"
                    row["settings"][name] = score(
                        original, decoded, target_rms)
                seed_rows.append(row)
            report["conditions"][voice] = seed_rows
            vals = [row["noisy"]["estoi"] for row in seed_rows]
            print(f"{voice}: noisy ESTOI {np.mean(vals):.4f}; finished {len(cases)} settings", flush=True)

    summary = {}
    setting_names = []
    for method, scale, floor, time_previous in cases:
        name = f"{method}/scale={scale:g}/floor={floor:g}"
        if time_previous is not None:
            name += f"/time_previous={time_previous:g}"
        setting_names.append(name)
    noisy_all = [r["noisy"]["estoi"] for rows in report["conditions"].values() for r in rows]
    for name in setting_names:
        vals = [r["settings"][name]["estoi"] for rows in report["conditions"].values() for r in rows]
        pesq = [r["settings"][name]["pesq_nb"] for rows in report["conditions"].values() for r in rows]
        summary[name] = {
            "mean_estoi": round(float(np.mean(vals)), 5),
            "mean_estoi_gain_vs_noisy": round(float(np.mean(np.asarray(vals) - noisy_all)), 5),
            "cases_better_than_noisy": int(sum(a > b for a, b in zip(vals, noisy_all))),
            "mean_pesq_nb": round(float(np.mean(pesq)), 4),
            "case_count": len(vals),
        }
    report["summary"] = summary
    ranked = sorted(summary.items(), key=lambda item: item[1]["mean_estoi_gain_vs_noisy"], reverse=True)
    report["ranked_by_mean_estoi_gain"] = [name for name, _ in ranked]
    report["ranked_by_mean_pesq_nb"] = [name for name, _ in
                                         sorted(summary.items(), key=lambda item: item[1]["mean_pesq_nb"], reverse=True)]
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    print(json.dumps({name: summary[name] for name, _ in ranked[:8]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
