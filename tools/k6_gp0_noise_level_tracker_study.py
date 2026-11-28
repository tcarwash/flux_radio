#!/usr/bin/env python3
"""Compare a causal spectral-quantile noise-level tracker with an oracle.

The tracker gets only a 500ms independent noise preamble for the baseline
spectrum. At each frame it estimates a scalar level change from a lower
quantile of current-to-baseline spectral power ratios, then smooths that level
causally. This is an offline prototype, not a qualified speech VAD or radio
receiver implementation.
"""
from __future__ import annotations

import argparse
import json
import math
import tempfile
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import NFFT, HOP, _frames, _gain, _noise_power, causal_denoise
from k6_gp0_nonstationary_noise_study import (
    MASK_FLOOR, NOISE_SCALE, PREAMBLE_MS, PREAMBLE_SEED_OFFSET,
    TARGET_SNR_DB, VPP, envelope, oracle_track_denoise,
)
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

TRACKS = ((0.20, 0.75), (0.35, 0.75))  # spectral quantile, previous-scale EMA weight
F_MIN_HZ, F_MAX_HZ = 200.0, 3400.0


def causal_quantile_tracker_denoise(mixture: np.ndarray, noise_preamble: np.ndarray, *,
                                    spectral_quantile: float,
                                    previous_weight: float) -> tuple[np.ndarray, dict]:
    """Use the lower current spectral-power quantile as a scalar noise proxy."""
    if not 0.05 <= spectral_quantile <= 0.50:
        raise ValueError("spectral_quantile must be in [0.05, 0.50]")
    if not 0.0 <= previous_weight < 1.0:
        raise ValueError("previous_weight must be in [0, 1)")
    x = np.asarray(mixture, dtype=np.float64)
    noise_spectrum = _noise_power(noise_preamble)
    frequencies = np.fft.rfftfreq(NFFT, 1.0 / FS)
    use_bins = (frequencies >= F_MIN_HZ) & (frequencies <= F_MAX_HZ)
    if np.count_nonzero(use_bins) < 16:
        raise ValueError("not enough in-band bins for tracking")

    preamble_power = np.abs(np.fft.rfft(_frames(noise_preamble), axis=1)) ** 2
    baseline_ratios = preamble_power[:, use_bins] / np.maximum(noise_spectrum[use_bins], 1e-30)
    baseline_q = float(np.median(np.quantile(baseline_ratios, spectral_quantile, axis=1)))
    if not np.isfinite(baseline_q) or baseline_q <= 0:
        raise ArithmeticError("invalid preamble quantile calibration")

    pad_left = NFFT - HOP
    work = np.pad(x, (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    ola = np.zeros(len(work), dtype=np.float64)
    norm = np.zeros(len(work), dtype=np.float64)
    scale_hat = 1.0
    scales = []
    for start in starts:
        frame = work[start:start + NFFT] * window
        spectrum = np.fft.rfft(frame)
        power = np.abs(spectrum) ** 2
        current_ratios = power[use_bins] / np.maximum(noise_spectrum[use_bins], 1e-30)
        measured_scale = float(np.quantile(current_ratios, spectral_quantile) / baseline_q)
        measured_scale = float(np.clip(measured_scale, 0.20, 5.0))
        scale_hat = previous_weight * scale_hat + (1.0 - previous_weight) * measured_scale
        scales.append(scale_hat)
        gain = _gain(power, noise_spectrum * scale_hat, "wiener",
                     noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
        filtered = np.fft.irfft(spectrum * gain, n=NFFT)
        ola[start:start + NFFT] += filtered * window
        norm[start:start + NFFT] += window * window

    valid = norm > 1e-10
    ola[valid] /= norm[valid]
    out = ola[pad_left:pad_left + len(x)]
    if len(out) != len(x) or not np.isfinite(out).all():
        raise ArithmeticError("quantile-tracker reconstruction failed")
    return out, {
        "preamble_calibration_quantile": baseline_q,
        "estimated_noise_scale_min": float(np.min(scales)),
        "estimated_noise_scale_median": float(np.median(scales)),
        "estimated_noise_scale_max": float(np.max(scales)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    profiles = ("stationary", "step_up_6db", "step_down_6db",
                "bursts_9p5db", "swell_6db")
    static_name = f"static_preamble_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}"
    oracle_name = f"oracle_envelope_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}"
    tracker_names = {
        q: f"causal_quantile_tracker/q={q:g}/previous_weight=0.75"
        for q, _ in TRACKS
    }
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "causal scalar noise-level tracking from lower spectral quantiles",
        "source_model": "RFSim measured-response GP0 PCM plus the same nonstationary banded profiles as the baseline study",
        "codec": "Sotto v3.8 C CLI, 8kHz input; temporary build",
        "input_vpp": VPP,
        "target_global_source_snr_db": TARGET_SNR_DB,
        "preamble_ms": PREAMBLE_MS,
        "noise_profiles": list(profiles),
        "tracker": {
            "frequency_bins_hz": [F_MIN_HZ, F_MAX_HZ],
            "spectral_quantiles": [q for q, _ in TRACKS],
            "previous_scale_weight": 0.75,
            "scale_limits": [0.20, 5.0],
            "uses_future_samples": False,
        },
        "filters": ["noisy", static_name, *tracker_names.values(), oracle_name],
        "speech_vectors": list(VOICE_IDS),
        "noise_seeds": list(SEEDS),
        "limitations": [
            "The lower-quantile scalar tracker is a simple prototype; it has no explicit speech VAD and may mistake dense speech for noise.",
            "The envelope oracle knows the exact simulated current noise level and is not deployable.",
            "Noise spectral shape remains fixed and banded; only its level varies over time.",
            "Speech/noise are simulated; PESQ-NB and ESTOI are proxies, not human readability or hardware evidence.",
            "The 8kHz host codec run omits RF channel loss and 4-8kHz hiss and is not MCU memory/timing qualification.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-level-tracker-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            ref_path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            reference = resample_poly(read_flac(ref_path, 16000), 1, 2)
            normalized = reference / float(np.max(np.abs(reference)))
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp, f"{voice}-clean")
            clean_pre_rms, clean_post_rms = float(np.std(clean)), float(np.std(clean_decoded))
            voice_profiles = {}
            for profile in profiles:
                profile_rows = []
                for seed in SEEDS:
                    env = envelope(profile, len(clean))
                    raw_noise = generate_gp0_noise(
                        len(clean), seed=seed, model="banded", rms_fs=OFF_PCM_RMS_FS)
                    scale = (clean_pre_rms / (10.0 ** (TARGET_SNR_DB / 20.0))) / float(np.std(raw_noise * env))
                    noisy = clean + raw_noise * env * scale
                    np.clip(noisy, -1.0, 1.0, out=noisy)
                    actual_snr = 20.0 * math.log10(
                        max(clean_pre_rms, 1e-15) / max(float(np.std(noisy - clean)), 1e-15))
                    initial_level = float(np.sqrt(np.mean(env[:FS * PREAMBLE_MS // 1000] ** 2)))
                    preamble = generate_gp0_noise(
                        FS * PREAMBLE_MS // 1000,
                        seed=seed + PREAMBLE_SEED_OFFSET,
                        model="banded", rms_fs=OFF_PCM_RMS_FS * initial_level * scale)
                    decoded_noisy = encode_decode(encoder, decoder, noisy, temp,
                                                  f"{voice}-{profile}-{seed}-noisy")
                    static = causal_denoise(noisy, preamble, "wiener",
                                            noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
                    oracle = oracle_track_denoise(
                        noisy, preamble, env, reference_envelope_rms=initial_level)
                    decoded_static = encode_decode(encoder, decoder, static, temp,
                                                   f"{voice}-{profile}-{seed}-static")
                    decoded_oracle = encode_decode(encoder, decoder, oracle, temp,
                                                   f"{voice}-{profile}-{seed}-oracle")
                    treatments = {
                        "noisy": {"pre_codec": score(reference, noisy, clean_pre_rms),
                                  "post_codec": score(reference, decoded_noisy, clean_post_rms)},
                        static_name: {"pre_codec": score(reference, static, clean_pre_rms),
                                      "post_codec": score(reference, decoded_static, clean_post_rms)},
                        oracle_name: {"pre_codec": score(reference, oracle, clean_pre_rms),
                                      "post_codec": score(reference, decoded_oracle, clean_post_rms)},
                    }
                    tracker_meta = {}
                    for q, _ in TRACKS:
                        tracked, track_info = causal_quantile_tracker_denoise(
                            noisy, preamble, spectral_quantile=q, previous_weight=0.75)
                        decoded = encode_decode(encoder, decoder, tracked, temp,
                                                f"{voice}-{profile}-{seed}-q{q:g}")
                        treatments[tracker_names[q]] = {
                            "pre_codec": score(reference, tracked, clean_pre_rms),
                            "post_codec": score(reference, decoded, clean_post_rms),
                        }
                        tracker_meta[tracker_names[q]] = track_info
                    profile_rows.append({
                        "seed": seed,
                        "actual_global_source_snr_db": round(actual_snr, 4),
                        "source_clip_fraction": round(float(np.mean(np.abs(noisy) >= 0.999)), 8),
                        "tracker_diagnostics": tracker_meta,
                        "treatments": treatments,
                    })
                voice_profiles[profile] = profile_rows
                print(f"{voice} {profile}: completed {len(profile_rows)} seeds", flush=True)
            report["conditions"][voice] = {
                "reference_file": ref_path.name,
                "clean_model_baseline": {
                    "pre_codec": score(reference, clean, clean_pre_rms),
                    "post_codec": score(reference, clean_decoded, clean_post_rms),
                },
                "profiles": voice_profiles,
            }

    names = ("noisy", static_name, *tracker_names.values(), oracle_name)
    summary = {}
    for profile in profiles:
        summary[profile] = {}
        for method in names:
            pre, post, pesq_values, noisy_post, wins = [], [], [], [], 0
            for cell in report["conditions"].values():
                for row in cell["profiles"][profile]:
                    pre.append(row["treatments"][method]["pre_codec"]["estoi"])
                    value = row["treatments"][method]["post_codec"]["estoi"]
                    post.append(value)
                    pesq_values.append(row["treatments"][method]["post_codec"]["pesq_nb"])
                    base = row["treatments"]["noisy"]["post_codec"]["estoi"]
                    noisy_post.append(base)
                    wins += value > base
            summary[profile][method] = {
                "case_count": len(post),
                "mean_pre_codec_estoi": round(float(np.mean(pre)), 5),
                "mean_post_codec_estoi": round(float(np.mean(post)), 5),
                "mean_post_codec_pesq_nb": round(float(np.mean(pesq_values)), 4),
                "mean_post_codec_gain_vs_noisy": round(float(np.mean(np.asarray(post) - noisy_post)), 5),
                "cases_better_than_noisy": int(wins),
            }
    report["summary"] = summary
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    for profile in profiles:
        print(f"SUMMARY {profile}: " + "; ".join(
            f"{method}={summary[profile][method]['mean_post_codec_estoi']:.4f}"
            for method in names), flush=True)


if __name__ == "__main__":
    main()
