#!/usr/bin/env python3
"""Estimate GP0 noise from a noise-only lead-in, then mask before Sotto v3.8.

Unlike the oracle study, masks see only a 500ms noise-only preamble and the
speech/noise mixture. They do not receive clean speech or the exact noise.
The utterance STFT is still offline/noncausal; the experiment isolates the
cost of estimating the spectrum from the extra benefit of oracle knowledge.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter
from scipy.signal import istft, stft

from k6_gp0_noise_sotto_v38_study import (
    FS, INPUT_VPP, ROOT, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec,
    encode_decode, read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

NFFT, HOP = 256, 64
PREAMBLE_SAMPLES = FS // 2
MASK_FLOOR = 0.05
PREAMBLE_SEED_OFFSET = 1_000_003


def make_masks(mixture: np.ndarray, noise_preamble: np.ndarray) -> dict[str, np.ndarray]:
    _, _, y = stft(mixture, FS, window="hann", nperseg=NFFT,
                   noverlap=NFFT-HOP, boundary="zeros", padded=True)
    _, _, n = stft(noise_preamble, FS, window="hann", nperseg=NFFT,
                   noverlap=NFFT-HOP, boundary="zeros", padded=True)
    noise_power = np.mean(np.abs(n) ** 2, axis=1, keepdims=True)
    mixture_power = np.abs(y) ** 2
    speech_power = np.maximum(mixture_power - noise_power, 0.0)
    eps = np.finfo(np.float64).tiny
    power_gain = speech_power / np.maximum(speech_power + noise_power, eps)
    magnitude_gain = np.sqrt(speech_power) / np.maximum(
        np.sqrt(speech_power) + np.sqrt(noise_power), eps)
    masks = {
        "estimated_wiener_power": power_gain,
        "estimated_magnitude_ratio": magnitude_gain,
        "estimated_wiener_power_smoothed": uniform_filter(power_gain, size=(3, 3), mode="nearest"),
        "estimated_magnitude_ratio_smoothed": uniform_filter(magnitude_gain, size=(3, 3), mode="nearest"),
    }
    outputs = {}
    for name, mask in masks.items():
        mask = np.maximum(mask, MASK_FLOOR)
        _, audio = istft(y * mask, FS, window="hann", nperseg=NFFT,
                         noverlap=NFFT-HOP, input_onesided=True, boundary=True)
        outputs[name] = audio[:len(mixture)]
    return outputs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "estimated-noise soft masks before real Sotto v3.8 8kHz codec",
        "noise_estimator": "mean STFT power from a 500ms noise-only GP0-model preamble; stationary for following speech",
        "utterance_processing": "offline STFT; full utterance available to mask; not causal implementation",
        "mask_floor": MASK_FLOOR,
        "stft": {"window": "hann", "nfft": NFFT, "hop": HOP},
        "input_vpp_scenarios": list(INPUT_VPP),
        "random_seeds": list(SEEDS),
        "preamble_noise_seed_policy": f"speech seed + {PREAMBLE_SEED_OFFSET}; independent deterministic realization, never reuses the following speech-mixture noise samples",
        "limitations": [
            "Noise preamble is synthetic model noise, not observed radio silence; speech SPL is uncalibrated.",
            "The denoiser uses whole-utterance STFT and mask application, so real-time latency/cost are not measured.",
            "Noise is stationary in this model; real microphone, PDM and room noise may vary over time.",
            "8kHz metrics omit Sotto v3.8 4-8kHz hiss and radio/channel impairments.",
            "PESQ/ESTOI are proxies, not human ratings.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-preamble-denoise-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            original = resample_poly(read_flac(path, 16000), 1, 2)
            peak = float(np.max(np.abs(original)))
            normalized = original / peak
            for vpp in INPUT_VPP:
                key = f"librispeech_{voice}__{vpp:.3f}Vpp"
                clean_pcm = k6_gp0_pcm(normalized, input_vpp=vpp, noise_rms_fs=0,
                                       response="measured", clip=True)
                clean_decoded = encode_decode(encoder, decoder, clean_pcm, temp, key+"-clean")
                target_rms = float(np.std(clean_decoded))
                baseline = score(original, clean_decoded, target_rms)
                seeds = []
                for seed in SEEDS:
                    noisy = k6_gp0_pcm(normalized, input_vpp=vpp,
                                       noise_rms_fs=OFF_PCM_RMS_FS, seed=seed,
                                       response="measured", noise_model="banded", clip=True)
                    preamble = generate_gp0_noise(PREAMBLE_SAMPLES,
                                                  seed=seed + PREAMBLE_SEED_OFFSET,
                                                  model="banded", rms_fs=OFF_PCM_RMS_FS)
                    decoded_noisy = encode_decode(encoder, decoder, noisy, temp,
                                                  f"{key}-noisy-{seed}")
                    row = {"seed": seed,
                           "measured_noise": score(original, decoded_noisy, target_rms),
                           "masks": {}}
                    for name, filtered in make_masks(noisy, preamble).items():
                        decoded = encode_decode(encoder, decoder, filtered, temp,
                                                f"{key}-{name}-{seed}")
                        row["masks"][name] = score(original, decoded, target_rms)
                    seeds.append(row)
                report["conditions"][key] = {
                    "reference_file": path.name,
                    "clean_peak_normalization_gain_db": round(-20*math.log10(peak), 3),
                    "noise_free_gp0_model_baseline": baseline,
                    "measured_source_snr_db": round(20*math.log10(max(float(np.std(clean_pcm)), 1e-15)/OFF_PCM_RMS_FS), 3),
                    "seeds": seeds,
                }
                print(f"{key}: baseline {baseline['estoi']:.4f}; "
                      f"noise {np.mean([r['measured_noise']['estoi'] for r in seeds]):.4f}; "
                      + ", ".join(f"{name} {np.mean([r['masks'][name]['estoi'] for r in seeds]):.4f}"
                                  for name in seeds[0]["masks"]))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
