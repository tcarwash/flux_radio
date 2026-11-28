#!/usr/bin/env python3
"""Offline causal-frame GP0 denoising sweep through the real Sotto v3.8 codec.

Noise power is estimated only from an independent synthetic noise preamble.
During speech, each STFT mask uses the current and past frames only; a 256
sample frame implies up to 32ms algorithmic lookahead. This tests whether the
earlier whole-utterance estimated-mask gains survive a streaming-style mask,
not whether the algorithm fits the K6 MCU or improves real captured speech.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d

from k6_gp0_noise_sotto_v38_study import (
    FS, INPUT_VPP, ROOT, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec,
    encode_decode, read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

NFFT, HOP = 256, 64
PREAMBLE_MS = (100, 250, 500, 1000)
METHODS = ("wiener", "wiener_smoothed", "magnitude_smoothed", "oversub_1p5")
MASK_FLOOR = 0.05
PREAMBLE_SEED_OFFSET = 1_000_003
TIME_EMA_PREVIOUS = 0.65


def _frames(x: np.ndarray, *, pad_left: int = 0) -> np.ndarray:
    """Windowed causal frame sequence; frame k depends only on its end time."""
    # Noise statistics use only complete preamble frames, without zero-padded
    # tail frames that would bias short-preamble power estimates downward.
    left = np.pad(np.asarray(x, dtype=np.float64), (pad_left, 0))
    starts = np.arange(0, len(left) - NFFT + 1, HOP)
    idx = starts[:, None] + np.arange(NFFT)[None, :]
    return left[idx] * np.hanning(NFFT + 1)[:-1][None, :]


def _noise_power(preamble: np.ndarray) -> np.ndarray:
    frames = _frames(preamble)
    if len(frames) < 4:
        raise ValueError("noise preamble must contain at least four complete STFT frames")
    spectra = np.fft.rfft(frames, axis=1)
    return np.mean(np.abs(spectra) ** 2, axis=0)


def _gain(power: np.ndarray, noise: np.ndarray, method: str, *,
          noise_scale: float = 1.0, mask_floor: float = MASK_FLOOR) -> np.ndarray:
    eps = np.finfo(np.float64).tiny
    noise = noise * noise_scale
    if method.startswith("oversub"):
        estimate = np.maximum(power - 1.5 * noise, 0.0)
        gain = np.sqrt(estimate / np.maximum(power, eps))
    elif method.startswith("magnitude"):
        estimate = np.maximum(power - noise, 0.0)
        gain = np.sqrt(estimate) / np.maximum(np.sqrt(estimate) + np.sqrt(noise), eps)
    else:
        gain = np.maximum(power - noise, 0.0) / np.maximum(power, eps)
    if method.endswith("smoothed"):
        # Symmetric smoothing over frequency bins is available within the
        # current frame; temporal smoothing below is one-sided and causal.
        gain = uniform_filter1d(gain, size=3, axis=0, mode="nearest")
    return np.maximum(gain, mask_floor)


def causal_denoise(mixture: np.ndarray, noise_preamble: np.ndarray,
                   method: str, *, noise_scale: float = 1.0,
                   mask_floor: float = MASK_FLOOR,
                   time_previous: float = TIME_EMA_PREVIOUS,
                   time_open_previous: float | None = None,
                   time_close_previous: float | None = None) -> np.ndarray:
    """STFT overlap-add with causal frame gains and 32ms bounded lookahead."""
    if (time_open_previous is None) != (time_close_previous is None):
        raise ValueError("time_open_previous and time_close_previous must be set together")
    if time_open_previous is not None and not (
            0 <= time_open_previous < 1 and 0 <= time_close_previous < 1):
        raise ValueError("asymmetric temporal weights must be in [0, 1)")
    x = np.asarray(mixture, dtype=np.float64)
    noise = _noise_power(noise_preamble)
    pad_left = NFFT - HOP
    work = np.pad(x, (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    ola = np.zeros(len(work), dtype=np.float64)
    norm = np.zeros(len(work), dtype=np.float64)
    previous = np.ones(NFFT // 2 + 1, dtype=np.float64)
    for start in starts:
        frame = work[start:start + NFFT] * window
        spectrum = np.fft.rfft(frame)
        gain = _gain(np.abs(spectrum) ** 2, noise, method,
                     noise_scale=noise_scale, mask_floor=mask_floor)
        if "smoothed" in method:
            if time_open_previous is None:
                gain = time_previous * previous + (1.0 - time_previous) * gain
            else:
                # Rising gain opens the mask to preserve newly appearing speech
                # quickly; falling gain closes slowly to reduce musical bursts.
                prior_weight = np.where(gain >= previous,
                                        time_open_previous, time_close_previous)
                gain = prior_weight * previous + (1.0 - prior_weight) * gain
        previous = gain
        filtered = np.fft.irfft(spectrum * gain, n=NFFT)
        ola[start:start + NFFT] += filtered * window
        norm[start:start + NFFT] += window * window
    valid = norm > 1e-10
    ola[valid] /= norm[valid]
    out = ola[pad_left:pad_left + len(x)]
    if len(out) != len(x) or not np.isfinite(out).all():
        raise ArithmeticError("causal STFT reconstruction failed")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()
    if args.json_out.exists():
        parser.error("refuse overwrite existing report")
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "noise-preamble estimated masks with causal frame updates before real Sotto v3.8 8kHz codec",
        "stft": {"window": "periodic Hann", "nfft": NFFT, "hop": HOP,
                 "lookahead_ms_max": NFFT * 1000 / FS},
        "preamble_duration_ms": list(PREAMBLE_MS),
        "preamble_noise_seed_offset": PREAMBLE_SEED_OFFSET,
        "masks": list(METHODS),
        "mask_floor": MASK_FLOOR,
        "time_smoothing": {"method": "one-sided EMA", "previous_frame_weight": TIME_EMA_PREVIOUS,
                           "frequency_smoothing_bins": 3},
        "input_vpp_scenarios": list(INPUT_VPP),
        "random_seeds": list(SEEDS),
        "limitations": [
            "Noise and speech are synthetic/statistical RFSim GP0 source samples, not physical voice captures.",
            "The noise-only preamble uses the same stationary model distribution but an independent realization.",
            "STFT processing is host-side; CPU/RAM/latency are not qualified on the K6.",
            "The 8kHz codec path omits Sotto v3.8 4-8kHz hiss and radio/channel impairments.",
            "PESQ and ESTOI are proxies, not human listening results.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-causal-denoise-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            original = resample_poly(read_flac(path, 16000), 1, 2)
            peak = float(np.max(np.abs(original)))
            normalized = original / peak
            for vpp in INPUT_VPP:
                key = f"librispeech_{voice}__{vpp:.3f}Vpp"
                clean = k6_gp0_pcm(normalized, input_vpp=vpp, noise_rms_fs=0,
                                   response="measured", clip=True)
                clean_decoded = encode_decode(encoder, decoder, clean, temp, key + "-clean")
                target_rms = float(np.std(clean_decoded))
                baseline = score(original, clean_decoded, target_rms)
                cell = {"noise_free_model_baseline": baseline, "seeds": []}
                for seed in SEEDS:
                    noisy = k6_gp0_pcm(normalized, input_vpp=vpp,
                                       noise_rms_fs=OFF_PCM_RMS_FS, seed=seed,
                                       response="measured", noise_model="banded", clip=True)
                    preamble_full = generate_gp0_noise(
                        FS * max(PREAMBLE_MS) // 1000,
                        seed=seed + PREAMBLE_SEED_OFFSET,
                        model="banded", rms_fs=OFF_PCM_RMS_FS)
                    noisy_decoded = encode_decode(encoder, decoder, noisy, temp,
                                                  f"{key}-noisy-{seed}")
                    seed_row = {"seed": seed,
                                "measured_noise": score(original, noisy_decoded, target_rms),
                                "preambles": {}}
                    for duration in PREAMBLE_MS:
                        preamble = preamble_full[:FS * duration // 1000]
                        duration_row = {}
                        for method in METHODS:
                            filtered = causal_denoise(noisy, preamble, method)
                            decoded = encode_decode(encoder, decoder, filtered, temp,
                                                    f"{key}-{seed}-{duration}-{method}")
                            duration_row[method] = score(original, decoded, target_rms)
                        seed_row["preambles"][str(duration)] = duration_row
                    cell["seeds"].append(seed_row)
                report["conditions"][key] = cell
                print(f"{key}: clean ESTOI {baseline['estoi']:.4f}; "
                      f"noise {np.mean([x['measured_noise']['estoi'] for x in cell['seeds']]):.4f}",
                      flush=True)

    # Compact summary: aggregate by method and preamble duration over all
    # voices/seeds at each input level, retaining condition-level metrics.
    report["summary"] = {}
    for vpp in INPUT_VPP:
        label = f"{vpp:.3f}Vpp"
        rows = []
        for key, cell in report["conditions"].items():
            if not key.endswith(label):
                continue
            for seed in cell["seeds"]:
                for duration, methods in seed["preambles"].items():
                    for method, metrics in methods.items():
                        rows.append((duration, method, metrics["estoi"], metrics["pesq_nb"],
                                     seed["measured_noise"]["estoi"],
                                     cell["noise_free_model_baseline"]["estoi"]))
        report["summary"][label] = {}
        for duration, method in sorted({(r[0], r[1]) for r in rows}):
            k = f"{duration}ms/{method}"
            subset = [r for r in rows if r[0] == duration and r[1] == method]
            report["summary"][label][k] = {
                "mean_estoi": round(float(np.mean([r[2] for r in subset])), 5),
                "mean_pesq_nb": round(float(np.mean([r[3] for r in subset])), 4),
                "mean_estoi_gain_vs_noisy": round(float(np.mean([r[2] - r[4] for r in subset])), 5),
                "mean_estoi_gap_vs_noise_free": round(float(np.mean([r[2] - r[5] for r in subset])), 5),
                "cases_better_estoi_than_noisy": int(sum(r[2] > r[4] for r in subset)),
                "case_count": len(subset),
            }
        best = max(report["summary"][label].items(), key=lambda item: item[1]["mean_estoi_gain_vs_noisy"])
        print(f"SUMMARY {label}: best {best[0]} ESTOI gain "
              f"{best[1]['mean_estoi_gain_vs_noisy']:+.5f}", flush=True)

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")


if __name__ == "__main__":
    main()
