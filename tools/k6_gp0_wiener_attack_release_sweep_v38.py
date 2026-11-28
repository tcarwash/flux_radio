#!/usr/bin/env python3
"""Compare causal Wiener-mask opening/closing behavior through Sotto v3.8.

Uses the RFSim banded GP0 noise model and the same eight speech vectors and
three deterministic noise seeds as the other offline studies. Fast mask
opening protects new speech onsets; slower closing may suppress musical bursts
at the cost of leaving residual noise. Host-only proxy experiment, not radio
or MCU qualification.
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
BASE_PAIRS = ((2.0, 0.05), (2.5, 0.03))
ASYMMETRIC_WEIGHTS = ((0.0, 0.70), (0.10, 0.70), (0.10, 0.85), (0.25, 0.75))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    if args.json_out.exists():
        parser.error("refuse overwrite existing report")

    cases = []
    for scale, floor in BASE_PAIRS:
        cases.append({"name": f"wiener/scale={scale:g}/floor={floor:g}",
                      "method": "wiener", "scale": scale, "floor": floor,
                      "time_previous": None, "open_previous": None,
                      "close_previous": None})
    for scale, floor in BASE_PAIRS:
        for opening, closing in ASYMMETRIC_WEIGHTS:
            cases.append({
                "name": (f"wiener_asymmetric/scale={scale:g}/floor={floor:g}"
                         f"/open_previous={opening:g}/close_previous={closing:g}"),
                "method": "wiener_smoothed", "scale": scale, "floor": floor,
                "time_previous": None, "open_previous": opening,
                "close_previous": closing,
            })
    for previous in (0.0, 0.3):
        cases.append({
            "name": f"wiener_smoothed/scale=2.5/floor=0.05/time_previous={previous:g}",
            "method": "wiener_smoothed", "scale": 2.5, "floor": 0.05,
            "time_previous": previous, "open_previous": None,
            "close_previous": None,
        })

    report = {
        "schema": 1,
        "offline_only": True,
        "source_model": "RFSim measured-response GP0 PCM with stationary banded OFF noise",
        "codec": "Sotto v3.8 C CLI, 8kHz",
        "input_vpp": VPP,
        "noise_rms_fs": OFF_PCM_RMS_FS,
        "preamble_ms": PREAMBLE_MS,
        "preamble_seed_offset": PREAMBLE_SEED_OFFSET,
        "noise_seeds": list(SEEDS),
        "speech_vectors": list(VOICE_IDS),
        "cases": cases,
        "interpretation_limits": [
            "Synthetic/statistical GP0 source model, not captured speech or calibrated acoustic speech.",
            "PESQ and ESTOI are objective proxies and do not substitute for listener scores.",
            "The Wiener noise estimate comes from an independent stationary 500ms preamble.",
            "The STFT has up to 32ms lookahead and was not measured on K6 MCU hardware.",
            "The Sotto codec and radio channel are modeled only by the repository Sotto v3.8 host codec path.",
        ],
        "conditions": {},
    }

    with tempfile.TemporaryDirectory(prefix="gp0-wiener-open-close-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            original = resample_poly(
                read_flac(SAMPLE_ROOT / f"librispeech_{voice}__original16.flac", 16000),
                1, 2)
            normalized = original / float(np.max(np.abs(original)))
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp,
                                          f"{voice}-clean")
            target_rms = float(np.std(clean_decoded))
            seed_rows = []
            for seed in SEEDS:
                noisy = k6_gp0_pcm(normalized, input_vpp=VPP,
                                   noise_rms_fs=OFF_PCM_RMS_FS, seed=seed,
                                   response="measured", noise_model="banded", clip=True)
                preamble = generate_gp0_noise(
                    FS * PREAMBLE_MS // 1000,
                    seed=seed + PREAMBLE_SEED_OFFSET,
                    model="banded", rms_fs=OFF_PCM_RMS_FS)
                noisy_decoded = encode_decode(encoder, decoder, noisy, temp,
                                              f"{voice}-noisy-{seed}")
                row = {"seed": seed,
                       "noisy": score(original, noisy_decoded, target_rms),
                       "settings": {}}
                for case in cases:
                    kwargs = {"noise_scale": case["scale"],
                              "mask_floor": case["floor"]}
                    if case["time_previous"] is not None:
                        kwargs["time_previous"] = case["time_previous"]
                    if case["open_previous"] is not None:
                        kwargs["time_open_previous"] = case["open_previous"]
                        kwargs["time_close_previous"] = case["close_previous"]
                    filtered = causal_denoise(noisy, preamble, case["method"], **kwargs)
                    decoded = encode_decode(encoder, decoder, filtered, temp,
                                            f"{voice}-{seed}-{len(row['settings'])}")
                    row["settings"][case["name"]] = score(original, decoded, target_rms)
                seed_rows.append(row)
            report["conditions"][voice] = seed_rows
            mean_noisy = float(np.mean([row["noisy"]["estoi"] for row in seed_rows]))
            print(f"{voice}: noisy ESTOI {mean_noisy:.4f}; finished {len(cases)} settings",
                  flush=True)

    noisy_values = [row["noisy"]["estoi"]
                    for rows in report["conditions"].values() for row in rows]
    summary = {}
    for case in cases:
        name = case["name"]
        values = [row["settings"][name]["estoi"]
                  for rows in report["conditions"].values() for row in rows]
        pesq_values = [row["settings"][name]["pesq_nb"]
                       for rows in report["conditions"].values() for row in rows]
        summary[name] = {
            "mean_estoi": round(float(np.mean(values)), 5),
            "mean_estoi_gain_vs_noisy": round(float(np.mean(np.asarray(values) - noisy_values)), 5),
            "cases_better_than_noisy": int(sum(x > y for x, y in zip(values, noisy_values))),
            "mean_pesq_nb": round(float(np.mean(pesq_values)), 4),
            "case_count": len(values),
        }
    report["summary"] = summary
    report["ranked_by_estoi_gain"] = sorted(
        summary, key=lambda name: summary[name]["mean_estoi_gain_vs_noisy"], reverse=True)
    report["ranked_by_pesq_nb"] = sorted(
        summary, key=lambda name: summary[name]["mean_pesq_nb"], reverse=True)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    print(json.dumps({name: summary[name] for name in report["ranked_by_estoi_gain"][:8]},
                     indent=2), flush=True)


if __name__ == "__main__":
    main()
