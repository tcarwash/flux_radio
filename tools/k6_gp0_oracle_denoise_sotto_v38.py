#!/usr/bin/env python3
"""Oracle upper bound for GP0 source-noise removal before Sotto v3.8.

The denoiser sees the exact clean speech and noise used to synthesize each
case. Its masks are intentionally noncausal and unavailable in a real radio;
results measure spectral-denoising headroom only, not an implementable filter.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy.signal import istft, stft

from k6_gp0_noise_sotto_v38_study import (
    FS, INPUT_VPP, ROOT, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec,
    encode_decode, read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, k6_gp0_pcm

NFFT = 256  # 32ms at 8kHz, matching Sotto's 20ms analysis scale reasonably.
HOP = 64


def speech_noise(clean: np.ndarray, noisy: np.ndarray) -> np.ndarray:
    """Reference-aligned synthesized noise, including any source-model clipping."""
    n = min(len(clean), len(noisy))
    return noisy[:n] - clean[:n]


def oracle_masks(mixture: np.ndarray, clean: np.ndarray, noise: np.ndarray) -> dict[str, np.ndarray]:
    _, _, y = stft(mixture, FS, window="hann", nperseg=NFFT,
                   noverlap=NFFT-HOP, boundary="zeros", padded=True)
    _, _, s = stft(clean, FS, window="hann", nperseg=NFFT,
                   noverlap=NFFT-HOP, boundary="zeros", padded=True)
    _, _, n = stft(noise, FS, window="hann", nperseg=NFFT,
                   noverlap=NFFT-HOP, boundary="zeros", padded=True)
    sp, npow = np.abs(s) ** 2, np.abs(n) ** 2
    eps = np.finfo(np.float64).tiny
    # Oracle ideal ratio masks; each uses exact clean/noise power, then retains
    # the observed mixture phase. No temporal/frequency smoothing is applied.
    wiener = sp / np.maximum(sp + npow, eps)
    irm = np.sqrt(sp) / np.maximum(np.sqrt(sp) + np.sqrt(npow), eps)
    binary = (sp >= npow).astype(np.float64)
    outputs = {}
    for name, mask in (("oracle_wiener_power", wiener),
                       ("oracle_ideal_magnitude_ratio", irm),
                       ("oracle_ideal_binary", binary)):
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
        "experiment": "oracle source-noise denoising upper bound before real Sotto v3.8 8kHz codec",
        "oracle": "uses exact generated clean reference and exact synthesized noise; noncausal; mixture phase retained",
        "stft": {"window": "hann", "nfft": NFFT, "hop": HOP, "window_ms": NFFT * 1000 / FS, "hop_ms": HOP * 1000 / FS},
        "input_vpp_scenarios": list(INPUT_VPP),
        "noise_rms_fs": OFF_PCM_RMS_FS,
        "random_seeds": list(SEEDS),
        "metrics": "narrowband PESQ and ESTOI against clean speech, level-matched to noise-free GP0-model Sotto decode",
        "limitations": [
            "The masks have oracle access to clean speech and exact noise, so they cannot be deployed as written.",
            "Noisy signal phase is retained; results are an oracle spectral-mask comparison, not a strict mathematical maximum.",
            "Input Vpp values are not calibrated to speech SPL; GP0 remains a statistical model from sparse physical measurements.",
            "8kHz analysis excludes the Sotto v3.8 4-8kHz hiss path and RF/channel errors.",
            "PESQ and ESTOI are objective proxies, not human listening results.",
        ],
        "conditions": {},
    }
    with __import__("tempfile").TemporaryDirectory(prefix="gp0-oracle-denoise-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            source16 = read_flac(path, 16000)
            reference = resample_poly(source16, 1, 2)
            peak = float(np.max(np.abs(reference)))
            clean_input = reference / peak
            for vpp in INPUT_VPP:
                key = f"librispeech_{voice}__{vpp:.3f}Vpp"
                clean_pcm = k6_gp0_pcm(clean_input, input_vpp=vpp, noise_rms_fs=0,
                                       seed=0, response="measured", clip=True)
                clean_decoded = encode_decode(encoder, decoder, clean_pcm, temp, key+"-clean")
                target_rms = float(np.std(clean_decoded))
                baseline = score(reference, clean_decoded, target_rms)
                seed_rows = []
                for seed in SEEDS:
                    noisy_pcm = k6_gp0_pcm(clean_input, input_vpp=vpp,
                                           noise_rms_fs=OFF_PCM_RMS_FS, seed=seed,
                                           response="measured", noise_model="banded", clip=True)
                    noise = speech_noise(clean_pcm, noisy_pcm)
                    noisy_decoded = encode_decode(encoder, decoder, noisy_pcm, temp,
                                                  f"{key}-noisy-{seed}")
                    row = {"seed": seed,
                           "measured_noise": score(reference, noisy_decoded, target_rms),
                           "source_clip_fraction": round(float(np.mean(np.abs(noisy_pcm) >= 0.999)), 8),
                           "oracle": {}}
                    for name, denoised in oracle_masks(noisy_pcm, clean_pcm, noise).items():
                        denoised_decoded = encode_decode(encoder, decoder, denoised, temp,
                                                         f"{key}-{name}-{seed}")
                        row["oracle"][name] = score(reference, denoised_decoded, target_rms)
                    seed_rows.append(row)
                report["conditions"][key] = {
                    "reference_file": path.name,
                    "clean_peak_normalization_gain_db": round(-20*math.log10(peak), 3),
                    "noise_free_gp0_model_baseline": baseline,
                    "measured_noise_snr_db": round(20*math.log10(max(float(np.std(clean_pcm)), 1e-15)/OFF_PCM_RMS_FS), 3),
                    "seeds": seed_rows,
                }
                print(f"{key}: baseline ESTOI {baseline['estoi']:.4f}; "
                      f"measured {np.mean([r['measured_noise']['estoi'] for r in seed_rows]):.4f}; "
                      + ", ".join(f"{n} {np.mean([r['oracle'][n]['estoi'] for r in seed_rows]):.4f}"
                                  for n in seed_rows[0]["oracle"]))
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
