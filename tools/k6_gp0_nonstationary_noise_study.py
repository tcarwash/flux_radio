#!/usr/bin/env python3
"""Stress static GP0 preamble suppression under changing modeled noise levels.

Compares the current fixed-preamble Wiener filter with a diagnostic oracle
that knows the current synthetic noise envelope. The oracle is an attribution
ceiling, not a deployable estimator. The study holds total source-noise RMS
constant across stationary, step, burst, and swell profiles.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import NFFT, HOP, _gain, _noise_power, causal_denoise
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

VPP = 0.1
TARGET_SNR_DB = -1.0
PREAMBLE_MS = 500
PREAMBLE_SEED_OFFSET = 2_000_003
NOISE_SCALE = 2.5
MASK_FLOOR = 0.03


def envelope(profile: str, n: int) -> np.ndarray:
    t = np.arange(n, dtype=np.float64) / FS
    duration = n / FS
    if profile == "stationary":
        return np.ones(n)
    if profile in ("step_up_6db", "step_down_6db"):
        ratio = 2.0  # 6.02dB level change
        low = 1.0 / math.sqrt((1.0 + ratio * ratio) / 2.0)
        high = ratio * low
        e = np.where(t < duration / 2.0, low, high)
        return e if profile == "step_up_6db" else e[::-1].copy()
    if profile == "bursts_9p5db":
        e = np.full(n, 0.7453559925)  # normalized for 10% duty, 3x burst ratio
        burst = (t >= 1.0) & (np.mod(t - 1.0, 2.0) < 0.25)
        e[burst] = 3.0 * 0.7453559925
        # Finite clip length may not contain an integral number of cycles.
        return e / np.sqrt(float(np.mean(e * e)))
    if profile == "swell_6db":
        e = 1.0 + (1.0 / 3.0) * np.sin(2.0 * np.pi * t / 4.0)
        return e / np.sqrt(float(np.mean(e * e)))
    raise ValueError(f"unknown envelope profile: {profile}")


def oracle_track_denoise(mixture: np.ndarray, noise_preamble: np.ndarray,
                         envelope_samples: np.ndarray, *,
                         reference_envelope_rms: float) -> np.ndarray:
    """Apply causal Wiener gains with exact current envelope power (oracle)."""
    if len(mixture) != len(envelope_samples):
        raise ValueError("mixture and envelope lengths differ")
    if reference_envelope_rms <= 0:
        raise ValueError("reference envelope RMS must be positive")
    noise = _noise_power(noise_preamble)
    pad_left = NFFT - HOP
    x = np.asarray(mixture, dtype=np.float64)
    work = np.pad(x, (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    centers = starts + NFFT // 2 - pad_left
    centers = np.clip(centers, 0, len(envelope_samples) - 1)
    ratio = envelope_samples[centers] / reference_envelope_rms
    noise_scale_track = ratio * ratio
    ola = np.zeros(len(work), dtype=np.float64)
    norm = np.zeros(len(work), dtype=np.float64)
    for i, start in enumerate(starts):
        frame = work[start:start + NFFT] * window
        spectrum = np.fft.rfft(frame)
        gain = _gain(np.abs(spectrum) ** 2, noise * noise_scale_track[i], "wiener",
                     noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
        filtered = np.fft.irfft(spectrum * gain, n=NFFT)
        ola[start:start + NFFT] += filtered * window
        norm[start:start + NFFT] += window * window
    valid = norm > 1e-10
    ola[valid] /= norm[valid]
    out = ola[pad_left:pad_left + len(x)]
    if len(out) != len(x) or not np.isfinite(out).all():
        raise ArithmeticError("oracle noise-track reconstruction failed")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")

    profiles = ("stationary", "step_up_6db", "step_down_6db",
                "bursts_9p5db", "swell_6db")
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "fixed-preamble GP0 Wiener under nonstationary synthetic banded noise",
        "source_model": "RFSim measured-response GP0 PCM plus shaped synthetic noise multiplied by a known envelope",
        "codec": "Sotto v3.8 C CLI, 8kHz input; temporary build",
        "input_vpp": VPP,
        "target_global_source_snr_db": TARGET_SNR_DB,
        "noise_model_rms_fs": OFF_PCM_RMS_FS,
        "noise_profiles": list(profiles),
        "profile_definitions": {
            "stationary": "constant amplitude",
            "step_up_6db": "6.02dB amplitude step at utterance midpoint; envelope normalized to unit RMS",
            "step_down_6db": "reverse of step_up_6db",
            "bursts_9p5db": "250ms bursts every 2s, 3x amplitude over floor; finite envelope normalized to unit RMS",
            "swell_6db": "4s-period sinusoidal amplitude from 2/3x to 4/3x nominal; 6.02dB peak-to-peak before unit-RMS normalization",
        },
        "preamble_ms": PREAMBLE_MS,
        "preamble_seed_offset": PREAMBLE_SEED_OFFSET,
        "stft": {"window": "periodic Hann", "nfft": NFFT, "hop": HOP,
                 "lookahead_ms_max": NFFT * 1000 / FS},
        "filters": [
            "noisy",
            f"static_preamble_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}",
            f"oracle_envelope_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}",
        ],
        "speech_vectors": list(VOICE_IDS),
        "noise_seeds": list(SEEDS),
        "limitations": [
            "All speech and noise are simulated from the existing statistical RFSim model; there is no physical spoken-speech capture.",
            "The oracle filter knows the exact time-varying noise envelope and is an unattainable diagnostic ceiling, not an implementable receiver filter.",
            "Each profile is normalized to the same total noise RMS and nominal source SNR; local SNR intentionally varies over time.",
            "Noise spectral shape remains banded and fixed; this study tests level nonstationarity, not spectral-shape mismatch.",
            "The envelope-informed oracle uses the current frame-center envelope and preamble PSD; it has no future speech content but has otherwise unavailable exact level information.",
            "PESQ-NB and ESTOI are proxies, not human intelligibility results.",
            "Host-side filtering is not K6 MCU timing/RAM qualification; 8kHz scoring omits radio-channel effects and 4-8kHz hiss.",
        ],
        "conditions": {},
    }
    static_name = f"static_preamble_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}"
    oracle_name = f"oracle_envelope_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}"

    with tempfile.TemporaryDirectory(prefix="gp0-noise-nonstationary-") as td:
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
            voice_profiles = {}
            for profile in profiles:
                profile_rows = []
                for seed in SEEDS:
                    env = envelope(profile, len(clean))
                    raw_noise = generate_gp0_noise(
                        len(clean), seed=seed, model="banded", rms_fs=OFF_PCM_RMS_FS)
                    scale = (clean_pre_rms / (10.0 ** (TARGET_SNR_DB / 20.0))) / float(np.std(raw_noise * env))
                    modulated_noise = raw_noise * env * scale
                    noisy = clean + modulated_noise
                    np.clip(noisy, -1.0, 1.0, out=noisy)
                    actual_snr = 20.0 * math.log10(
                        max(clean_pre_rms, 1e-15) / max(float(np.std(noisy - clean)), 1e-15))
                    preamble_level_rms = float(np.sqrt(np.mean(env[:FS * PREAMBLE_MS // 1000] ** 2)))
                    preamble_rms = OFF_PCM_RMS_FS * preamble_level_rms * scale
                    preamble = generate_gp0_noise(
                        FS * PREAMBLE_MS // 1000,
                        seed=seed + PREAMBLE_SEED_OFFSET,
                        model="banded", rms_fs=preamble_rms)
                    decoded_noisy = encode_decode(encoder, decoder, noisy, temp,
                                                  f"{voice}-{profile}-{seed}-noisy")
                    static = causal_denoise(noisy, preamble, "wiener",
                                            noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
                    oracle = oracle_track_denoise(
                        noisy, preamble, env, reference_envelope_rms=preamble_level_rms)
                    decoded_static = encode_decode(encoder, decoder, static, temp,
                                                   f"{voice}-{profile}-{seed}-static")
                    decoded_oracle = encode_decode(encoder, decoder, oracle, temp,
                                                   f"{voice}-{profile}-{seed}-oracle")
                    row = {
                        "seed": seed,
                        "actual_global_source_snr_db": round(actual_snr, 4),
                        "source_clip_fraction": round(float(np.mean(np.abs(noisy) >= 0.999)), 8),
                        "preamble_noise_rms_fs": round(float(np.std(preamble)), 8),
                        "envelope_min": round(float(np.min(env)), 5),
                        "envelope_max": round(float(np.max(env)), 5),
                        "treatments": {
                            "noisy": {
                                "pre_codec": score(reference, noisy, clean_pre_rms),
                                "post_codec": score(reference, decoded_noisy, clean_post_rms),
                            },
                            static_name: {
                                "pre_codec": score(reference, static, clean_pre_rms),
                                "post_codec": score(reference, decoded_static, clean_post_rms),
                            },
                            oracle_name: {
                                "pre_codec": score(reference, oracle, clean_pre_rms),
                                "post_codec": score(reference, decoded_oracle, clean_post_rms),
                            },
                        },
                    }
                    profile_rows.append(row)
                voice_profiles[profile] = profile_rows
                mean = np.mean([r["treatments"][static_name]["post_codec"]["estoi"]
                                for r in profile_rows])
                print(f"{voice} {profile}: static post-codec ESTOI {mean:.4f}", flush=True)
            report["conditions"][voice] = {
                "reference_file": ref_path.name,
                "clean_model_baseline": {
                    "pre_codec": score(reference, clean, clean_pre_rms),
                    "post_codec": score(reference, clean_decoded, clean_post_rms),
                },
                "profiles": voice_profiles,
            }

    summary = {}
    for profile in profiles:
        summary[profile] = {}
        for method in ("noisy", static_name, oracle_name):
            pre, post, noisy_post, wins = [], [], [], 0
            for cell in report["conditions"].values():
                for row in cell["profiles"][profile]:
                    pre.append(row["treatments"][method]["pre_codec"]["estoi"])
                    val = row["treatments"][method]["post_codec"]["estoi"]
                    post.append(val)
                    baseline = row["treatments"]["noisy"]["post_codec"]["estoi"]
                    noisy_post.append(baseline)
                    wins += val > baseline
            summary[profile][method] = {
                "case_count": len(post),
                "mean_pre_codec_estoi": round(float(np.mean(pre)), 5),
                "mean_post_codec_estoi": round(float(np.mean(post)), 5),
                "mean_post_codec_pesq_nb": round(float(np.mean([
                    row["treatments"][method]["post_codec"]["pesq_nb"]
                    for cell in report["conditions"].values()
                    for row in cell["profiles"][profile]])), 4),
                "mean_post_codec_gain_vs_noisy": round(float(np.mean(np.asarray(post) - noisy_post)), 5),
                "cases_better_than_noisy": int(wins),
            }
    report["summary"] = summary
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as output:
        json.dump(report, output, indent=2, sort_keys=True)
        output.write("\n")
    for profile in profiles:
        print(f"SUMMARY {profile}: " + "; ".join(
            f"{method}={metrics['mean_post_codec_estoi']:.4f}"
            for method, metrics in summary[profile].items()), flush=True)


if __name__ == "__main__":
    main()
