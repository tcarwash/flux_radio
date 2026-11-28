#!/usr/bin/env python3
"""Sweep decision-directed Wiener and Log-MMSE temporal constants offline."""
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
from k6_gp0_snr_logmmse_study import decision_directed_denoise
from rfsim.k6_mic import generate_gp0_noise, k6_gp0_pcm

VPP = 0.1
TARGET_SNR_DB = -1.0
FLOOR = 0.05
ALPHAS = (0.50, 0.70, 0.85, 0.95, 0.98)
PREAMBLE_MS = 500
PREAMBLE_SEED_OFFSET = 1_000_003
BASELINES = ("noisy", "wiener/scale=2/floor=0.05", "wiener/scale=2.5/floor=0.03")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")

    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "decision-directed alpha sweep at modeled -1dB source SNR",
        "source_model": "RFSim measured-response GP0 PCM plus independent stationary banded noise",
        "codec": "Sotto v3.8 C CLI, 8kHz input; temporary build",
        "input_vpp": VPP,
        "target_source_snr_db": TARGET_SNR_DB,
        "preamble_ms": PREAMBLE_MS,
        "noise_seeds": list(SEEDS),
        "speech_vectors": list(VOICE_IDS),
        "estimators": ["dd_wiener", "dd_logmmse"],
        "alpha_values": list(ALPHAS),
        "mask_floor": FLOOR,
        "noise_power_scale": 1.0,
        "limitations": [
            "Statistical noise model, normalized speech and laboratory 0.1Vpp reference; not physical spoken audio.",
            "PESQ/ESTOI are proxies; no human listening result or K6 MCU qualification.",
            "The 8kHz codec path excludes the Sotto 4-8kHz hiss band and RF channel effects.",
        ],
        "conditions": {},
    }
    names = [f"dd_{estimator}/alpha={alpha:g}/floor={FLOOR:g}"
             for estimator in ("wiener", "logmmse") for alpha in ALPHAS]
    with tempfile.TemporaryDirectory(prefix="gp0-dd-alpha-sweep-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            ref_path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            reference = resample_poly(read_flac(ref_path, 16000), 1, 2)
            normalized = reference / float(np.max(np.abs(reference)))
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp, f"{voice}-clean")
            target_rms = float(np.std(clean_decoded))
            clean_pre_rms = float(np.std(clean))
            rows = []
            for seed in SEEDS:
                noise_rms = clean_pre_rms / (10 ** (TARGET_SNR_DB / 20))
                noisy = k6_gp0_pcm(normalized, input_vpp=VPP,
                                   noise_rms_fs=noise_rms, seed=seed,
                                   response="measured", noise_model="banded", clip=True)
                actual_snr = 20 * np.log10(
                    max(clean_pre_rms, 1e-15) /
                    max(float(np.std(noisy - clean)), 1e-15))
                preamble = generate_gp0_noise(
                    FS * PREAMBLE_MS // 1000, seed=seed + PREAMBLE_SEED_OFFSET,
                    model="banded", rms_fs=noise_rms)
                noisy_decoded = encode_decode(encoder, decoder, noisy, temp,
                                              f"{voice}-noisy-{seed}")
                setting_scores = {
                    "noisy": {
                        "pre_codec": score(reference, noisy, clean_pre_rms),
                        "post_codec": score(reference, noisy_decoded, target_rms),
                    }
                }
                for name, scale, floor in ((BASELINES[1], 2.0, 0.05),
                                           (BASELINES[2], 2.5, 0.03)):
                    out = causal_denoise(noisy, preamble, "wiener",
                                         noise_scale=scale, mask_floor=floor)
                    dec = encode_decode(encoder, decoder, out, temp,
                                        f"{voice}-{seed}-{name.replace('/', '-')}")
                    setting_scores[name] = {
                        "pre_codec": score(reference, out, clean_pre_rms),
                        "post_codec": score(reference, dec, target_rms),
                    }
                for estimator in ("wiener", "logmmse"):
                    for alpha in ALPHAS:
                        name = f"dd_{estimator}/alpha={alpha:g}/floor={FLOOR:g}"
                        out = decision_directed_denoise(
                            noisy, preamble, alpha=alpha, mask_floor=FLOOR,
                            estimator=estimator)
                        dec = encode_decode(encoder, decoder, out, temp,
                                            f"{voice}-{seed}-dd-{estimator}-{alpha:g}")
                        setting_scores[name] = {
                            "pre_codec": score(reference, out, clean_pre_rms),
                            "post_codec": score(reference, dec, target_rms),
                        }
                rows.append({
                    "seed": seed,
                    "actual_source_snr_db": round(float(actual_snr), 4),
                    "source_clip_fraction": round(float(np.mean(np.abs(noisy) >= 0.999)), 8),
                    "settings": setting_scores,
                })
            report["conditions"][voice] = rows
            print(f"{voice}: completed {len(rows)} seeds", flush=True)

    summary = {}
    for name in ("noisy", *BASELINES[1:], *names):
        pre, post, noisy_post = [], [], []
        wins = 0
        for rows in report["conditions"].values():
            for row in rows:
                pre.append(row["settings"][name]["pre_codec"]["estoi"])
                value = row["settings"][name]["post_codec"]["estoi"]
                post.append(value)
                base = row["settings"]["noisy"]["post_codec"]["estoi"]
                noisy_post.append(base)
                wins += value > base
        summary[name] = {
            "case_count": len(post),
            "mean_pre_codec_estoi": round(float(np.mean(pre)), 5),
            "mean_post_codec_estoi": round(float(np.mean(post)), 5),
            "mean_post_codec_gain_vs_noisy": round(float(np.mean(np.asarray(post) - noisy_post)), 5),
            "cases_better_than_noisy_post_codec": int(wins),
        }
    report["summary"] = summary
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    for name, result in sorted(summary.items(), key=lambda item: item[1]["mean_post_codec_estoi"], reverse=True):
        print(f"{name}: post ESTOI {result['mean_post_codec_estoi']:.5f}; "
              f"gain {result['mean_post_codec_gain_vs_noisy']:+.5f}; "
              f"wins {result['cases_better_than_noisy_post_codec']}/{result['case_count']}", flush=True)


if __name__ == "__main__":
    main()
