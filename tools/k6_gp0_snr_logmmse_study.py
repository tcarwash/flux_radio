#!/usr/bin/env python3
"""Separate GP0 source-SNR, denoiser, and Sotto codec effects offline.

Compares two existing causal Wiener settings with causal decision-directed
Ephraim-Malah Log-MMSE and Wiener gains at fixed modeled input SNRs. Metrics
are retained both before and after the real Sotto v3.8 codec. This is a host
experiment on synthetic RFSim noise, not physical radio or MCU qualification.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

import numpy as np
from scipy.special import exp1

from k6_gp0_causal_denoise_sotto_v38 import causal_denoise, _noise_power
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import generate_gp0_noise, k6_gp0_pcm

VPP = 0.1
TARGET_SNR_DB = (-1.0, 2.0, 5.0, 8.0)
PREAMBLE_MS = 500
PREAMBLE_SEED_OFFSET = 1_000_003
NFFT, HOP = 256, 64
METHODS = (
    "noisy",
    "wiener/scale=2/floor=0.05",
    "wiener/scale=2.5/floor=0.03",
    "dd_wiener/alpha=0.98/floor=0.05",
    "dd_logmmse/alpha=0.98/floor=0.05",
)


def decision_directed_denoise(mixture: np.ndarray, noise_preamble: np.ndarray, *,
                              alpha: float, mask_floor: float,
                              estimator: str, noise_scale: float = 1.0) -> np.ndarray:
    """Causal decision-directed spectral suppression with a noise preamble."""
    if not 0.0 <= alpha < 1.0:
        raise ValueError("alpha must be in [0, 1)")
    if not 0.0 <= mask_floor <= 1.0:
        raise ValueError("mask_floor must be in [0, 1]")
    if not np.isfinite(noise_scale) or noise_scale <= 0:
        raise ValueError("noise_scale must be positive")
    if estimator not in ("wiener", "logmmse"):
        raise ValueError("estimator must be wiener or logmmse")

    x = np.asarray(mixture, dtype=np.float64)
    noise_power = _noise_power(noise_preamble) * noise_scale
    pad_left = NFFT - HOP
    work = np.pad(x, (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    ola = np.zeros(len(work), dtype=np.float64)
    norm = np.zeros(len(work), dtype=np.float64)
    eps = np.finfo(np.float64).tiny
    prev_gain = np.ones(NFFT // 2 + 1, dtype=np.float64)
    prev_gamma = np.ones(NFFT // 2 + 1, dtype=np.float64)

    for start in starts:
        frame = work[start:start + NFFT] * window
        spectrum = np.fft.rfft(frame)
        power = np.abs(spectrum) ** 2
        gamma = np.maximum(power / np.maximum(noise_power, eps), 1e-12)
        xi = alpha * (prev_gain ** 2) * prev_gamma + (1.0 - alpha) * np.maximum(gamma - 1.0, 0.0)
        xi = np.maximum(xi, 1e-12)
        if estimator == "wiener":
            gain = xi / (1.0 + xi)
        else:
            v = np.maximum(gamma * xi / (1.0 + xi), 1e-12)
            log_gain = np.log(xi / (1.0 + xi)) + 0.5 * exp1(v)
            gain = np.exp(np.minimum(log_gain, 0.0))
        gain = np.clip(gain, mask_floor, 1.0)
        prev_gain, prev_gamma = gain, gamma
        filtered = np.fft.irfft(spectrum * gain, n=NFFT)
        ola[start:start + NFFT] += filtered * window
        norm[start:start + NFFT] += window * window

    valid = norm > 1e-10
    ola[valid] /= norm[valid]
    out = ola[pad_left:pad_left + len(x)]
    if len(out) != len(x) or not np.isfinite(out).all():
        raise ArithmeticError("decision-directed reconstruction failed")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")

    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "fixed modeled GP0 input-SNR ladder; causal denoisers scored pre/post Sotto v3.8",
        "source_model": "RFSim measured-response GP0 PCM plus independent stationary banded noise",
        "codec": "Sotto v3.8 C CLI, 8kHz input; temporary build",
        "input_vpp": VPP,
        "target_source_snr_db": list(TARGET_SNR_DB),
        "preamble_ms": PREAMBLE_MS,
        "preamble_seed_offset": PREAMBLE_SEED_OFFSET,
        "speech_vectors": list(VOICE_IDS),
        "noise_seeds": list(SEEDS),
        "stft": {"window": "periodic Hann", "nfft": NFFT, "hop": HOP,
                 "lookahead_ms_max": NFFT * 1000 / FS},
        "treatments": list(METHODS),
        "limitations": [
            "Speech vectors are peak-normalized corpus clips; 0.1Vpp is a laboratory convention, not calibrated acoustic speech.",
            "Noise is a statistical stationary RFSim approximation, not sample-exact K6 noise or captured spoken audio.",
            "Target SNRs are imposed in the modeled source PCM before the Sotto codec.",
            "PESQ-NB and ESTOI are proxies, not human intelligibility scores.",
            "The 8kHz path omits Sotto v3.8 4-8kHz hiss and RF/channel impairments.",
            "Host-side filters are not qualified for K6 MCU memory, timing, or firmware deployment.",
        ],
        "conditions": {},
    }

    with tempfile.TemporaryDirectory(prefix="gp0-logmmse-study-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            ref_path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            reference = resample_poly(read_flac(ref_path, 16000), 1, 2)
            normalized = reference / float(np.max(np.abs(reference)))
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp, f"{voice}-clean")
            clean_pre_rms = float(np.std(clean))
            clean_post_rms = float(np.std(clean_decoded))
            voice_snr = {}
            for target_snr in TARGET_SNR_DB:
                snr_key = f"{target_snr:+g}dB"
                seed_rows = []
                for seed in SEEDS:
                    noise_rms = clean_pre_rms / (10.0 ** (target_snr / 20.0))
                    noisy = k6_gp0_pcm(normalized, input_vpp=VPP,
                                       noise_rms_fs=noise_rms, seed=seed,
                                       response="measured", noise_model="banded", clip=True)
                    observed_noise = noisy - clean
                    actual_snr = 20.0 * math.log10(
                        max(clean_pre_rms, 1e-15) / max(float(np.std(observed_noise)), 1e-15))
                    preamble = generate_gp0_noise(
                        FS * PREAMBLE_MS // 1000,
                        seed=seed + PREAMBLE_SEED_OFFSET,
                        model="banded", rms_fs=noise_rms)
                    row = {
                        "seed": seed,
                        "actual_source_snr_db": round(actual_snr, 4),
                        "source_clip_fraction": round(float(np.mean(np.abs(noisy) >= 0.999)), 8),
                        "treatments": {},
                    }
                    filtered = {
                        "noisy": noisy,
                        "wiener/scale=2/floor=0.05": causal_denoise(
                            noisy, preamble, "wiener", noise_scale=2.0, mask_floor=0.05),
                        "wiener/scale=2.5/floor=0.03": causal_denoise(
                            noisy, preamble, "wiener", noise_scale=2.5, mask_floor=0.03),
                        "dd_wiener/alpha=0.98/floor=0.05": decision_directed_denoise(
                            noisy, preamble, alpha=0.98, mask_floor=0.05, estimator="wiener"),
                        "dd_logmmse/alpha=0.98/floor=0.05": decision_directed_denoise(
                            noisy, preamble, alpha=0.98, mask_floor=0.05, estimator="logmmse"),
                    }
                    for method, pcm in filtered.items():
                        pre = score(reference, pcm, clean_pre_rms)
                        decoded = encode_decode(encoder, decoder, pcm, temp,
                                                f"{voice}-{snr_key}-{seed}-{method.replace('/', '-')}")
                        post = score(reference, decoded, clean_post_rms)
                        row["treatments"][method] = {"pre_codec": pre, "post_codec": post}
                    seed_rows.append(row)
                voice_snr[snr_key] = seed_rows
                print(f"{voice} {snr_key}: actual mean SNR "
                      f"{np.mean([r['actual_source_snr_db'] for r in seed_rows]):.2f} dB",
                      flush=True)
            report["conditions"][voice] = {
                "reference_file": ref_path.name,
                "clean_model_baseline": {
                    "pre_codec": score(reference, clean, clean_pre_rms),
                    "post_codec": score(reference, clean_decoded, clean_post_rms),
                },
                "snr_scenarios": voice_snr,
            }

    summary = {}
    for target_snr in TARGET_SNR_DB:
        snr_key = f"{target_snr:+g}dB"
        summary[snr_key] = {}
        for method in METHODS:
            pre_scores, post_scores, noisy_post, clean_post = [], [], [], []
            for cell in report["conditions"].values():
                clean_post.append(cell["clean_model_baseline"]["post_codec"]["estoi"])
                for row in cell["snr_scenarios"][snr_key]:
                    pre_scores.append(row["treatments"][method]["pre_codec"]["estoi"])
                    post_scores.append(row["treatments"][method]["post_codec"]["estoi"])
                    noisy_post.append(row["treatments"]["noisy"]["post_codec"]["estoi"])
            paired_gain = np.asarray(post_scores) - np.asarray(noisy_post)
            summary[snr_key][method] = {
                "case_count": len(post_scores),
                "mean_pre_codec_estoi": round(float(np.mean(pre_scores)), 5),
                "mean_post_codec_estoi": round(float(np.mean(post_scores)), 5),
                "mean_post_codec_gain_vs_noisy": round(float(np.mean(paired_gain)), 5),
                "cases_better_than_noisy_post_codec": int(np.sum(paired_gain > 0)),
                "mean_post_codec_gap_vs_clean": round(float(np.mean(post_scores) - np.mean(clean_post)), 5),
            }
    report["summary"] = summary
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    for snr_key, results in summary.items():
        ranked = sorted(results.items(), key=lambda item: item[1]["mean_post_codec_estoi"], reverse=True)
        print(f"SUMMARY {snr_key}: " + "; ".join(
            f"{name}={metrics['mean_post_codec_estoi']:.4f}"
            for name, metrics in ranked), flush=True)


if __name__ == "__main__":
    main()
