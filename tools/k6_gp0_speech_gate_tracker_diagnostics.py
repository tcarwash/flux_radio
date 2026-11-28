#!/usr/bin/env python3
"""Offline diagnostic for spectral-contrast gating of GP0 noise tracking."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import NFFT, HOP, _frames, _noise_power
from k6_gp0_noise_sotto_v38_study import FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, read_flac, resample_poly
from k6_gp0_nonstationary_noise_study import PREAMBLE_MS, PREAMBLE_SEED_OFFSET, TARGET_SNR_DB, VPP, envelope
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

Q_LOW, Q_HIGH = 0.20, 0.80
F_MIN_HZ, F_MAX_HZ = 200.0, 3400.0
WEIGHT = 0.90
THRESHOLDS = (1.25, 1.5, 2.0, 3.0, 5.0)
PROFILES = ("stationary", "step_down_6db", "step_up_6db", "bursts_9p5db", "swell_6db")


def track(signal: np.ndarray, preamble: np.ndarray, threshold: float | None,
          oracle_mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Return scale estimates and high/low spectral-contrast statistic."""
    noise = _noise_power(preamble)
    freq = np.fft.rfftfreq(NFFT, 1.0 / FS)
    bins = (freq >= F_MIN_HZ) & (freq <= F_MAX_HZ)
    baseline = np.abs(np.fft.rfft(_frames(preamble), axis=1)) ** 2
    ratios = baseline[:, bins] / np.maximum(noise[bins], 1e-30)
    low0 = float(np.median(np.quantile(ratios, Q_LOW, axis=1)))
    high0 = float(np.median(np.quantile(ratios, Q_HIGH, axis=1)))
    if low0 <= 0 or high0 <= 0:
        raise ArithmeticError("invalid spectral calibration")
    pad = NFFT - HOP
    work = np.pad(np.asarray(signal, dtype=np.float64), (pad, NFFT))
    win = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    estimate = 1.0
    values, contrasts = [], []
    for start in starts:
        frame = work[start:start + NFFT] * win
        p = np.abs(np.fft.rfft(frame)) ** 2
        r = p[bins] / np.maximum(noise[bins], 1e-30)
        low = float(np.quantile(r, Q_LOW) / low0)
        high = float(np.quantile(r, Q_HIGH) / high0)
        contrast = max(high, 1e-12) / max(low, 1e-12)
        contrasts.append(contrast)
        measured = float(np.clip(low, 0.20, 5.0))
        should_update = (threshold is None or contrast <= threshold)
        if oracle_mask is not None:
            should_update = not bool(oracle_mask[len(values)])
        if should_update:
            estimate = WEIGHT * estimate + (1.0 - WEIGHT) * measured
        values.append(estimate)
    return np.asarray(values), np.asarray(contrasts)


def main() -> None:
    out = Path("recovery/gp0_raw/gp0-speech-gate-tracker-diagnostics-20261128-oracle.json")
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    rows = []
    for voice in VOICE_IDS:
        ref = resample_poly(read_flac(SAMPLE_ROOT / f"librispeech_{voice}__original16.flac", 16000), 1, 2)
        normalized = ref / max(float(np.max(np.abs(ref))), 1e-15)
        clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0, response="measured", clip=True)
        clean_rms = float(np.std(clean))
        for profile in PROFILES:
            env = envelope(profile, len(clean))
            initial = float(np.sqrt(np.mean(env[:FS * PREAMBLE_MS // 1000] ** 2)))
            truth_samples = (env / initial) ** 2
            centers = np.arange(0, len(env) + (NFFT - HOP) + 1, HOP) + NFFT // 2 - (NFFT - HOP)
            truth = truth_samples[np.clip(centers, 0, len(env) - 1)]
            for seed in SEEDS:
                raw = generate_gp0_noise(len(clean), seed=seed, model="banded", rms_fs=OFF_PCM_RMS_FS)
                scale = clean_rms / (10 ** (TARGET_SNR_DB / 20)) / max(float(np.std(raw * env)), 1e-15)
                noise = raw * env * scale
                mix = np.clip(clean + noise, -1.0, 1.0)
                pre = generate_gp0_noise(FS * PREAMBLE_MS // 1000, seed=seed + PREAMBLE_SEED_OFFSET,
                                         model="banded", rms_fs=OFF_PCM_RMS_FS * initial * scale)
                pad = NFFT - HOP
                speech_work = np.pad(clean, (pad, NFFT))
                starts = np.arange(0, len(speech_work) - NFFT + 1, HOP)
                speech_rms = np.array([
                    np.sqrt(np.mean(speech_work[s:s + NFFT] ** 2))
                    for s in starts
                ])
                activity = speech_rms > (0.12 * max(float(np.max(speech_rms)), 1e-15))
                for threshold in (None, *THRESHOLDS):
                    est, contrast = track(mix, pre, threshold)
                    n = min(len(est), len(truth))
                    delta = est[:n] - truth[:n]
                    rows.append({
                        "voice": voice, "profile": profile, "seed": seed,
                        "threshold": threshold, "rmse": float(np.sqrt(np.mean(delta ** 2))),
                        "bias": float(np.mean(delta)),
                        "corr": float(np.corrcoef(est[:n], truth[:n])[0, 1]) if np.std(truth[:n]) else None,
                        "gated_fraction": float(np.mean(contrast[:n] > threshold)) if threshold is not None else 0.0,
                        "contrast_median": float(np.median(contrast[:n])),
                    })
                est, contrast = track(mix, pre, None, oracle_mask=activity)
                n = min(len(est), len(truth))
                delta = est[:n] - truth[:n]
                rows.append({
                    "voice": voice, "profile": profile, "seed": seed,
                    "threshold": "oracle_activity", "rmse": float(np.sqrt(np.mean(delta ** 2))),
                    "bias": float(np.mean(delta)),
                    "corr": float(np.corrcoef(est[:n], truth[:n])[0, 1]) if np.std(truth[:n]) else None,
                    "gated_fraction": float(np.mean(activity[:n])),
                    "contrast_median": float(np.median(contrast[:n])),
                })
            print(f"{voice} {profile}: completed", flush=True)
    summary = {}
    for profile in PROFILES:
        summary[profile] = {}
        for threshold in (None, *THRESHOLDS, "oracle_activity"):
            rs = [r for r in rows if r["profile"] == profile and r["threshold"] == threshold]
            corrs = [r["corr"] for r in rs if r["corr"] is not None]
            name = ("ungated" if threshold is None else
                    "oracle_activity_freeze" if threshold == "oracle_activity" else
                    f"contrast_gt_{threshold:g}_freeze")
            summary[profile][name] = {
                "case_count": len(rs), "mean_rmse": float(np.mean([r["rmse"] for r in rs])),
                "mean_bias": float(np.mean([r["bias"] for r in rs])),
                "mean_correlation": float(np.mean(corrs)) if corrs else None,
                "mean_gated_fraction": float(np.mean([r["gated_fraction"] for r in rs])),
                "median_contrast": float(np.mean([r["contrast_median"] for r in rs])),
            }
    report = {"schema": 1, "offline_only": True,
              "experiment": "spectral contrast speech gate for causal scalar noise tracking",
              "source_model": "RFSim measured-response GP0 PCM plus banded synthetic GP0 noise",
              "tracker": {"low_quantile": Q_LOW, "high_quantile": Q_HIGH,
                          "ema_previous_weight": WEIGHT, "freeze_thresholds": list(THRESHOLDS),
                          "statistic": "normalized q80/q20 current-to-preamble spectral ratios"},
              "profiles": list(PROFILES), "speech_vectors": list(VOICE_IDS), "noise_seeds": list(SEEDS),
              "limitations": ["This is an offline heuristic diagnostic, not a validated VAD.",
                              "A gated estimator may miss real noise-level changes during speech.",
                              "Noise has fixed banded spectral shape with scalar level envelopes.",
                              "Scale-estimation accuracy is not denoised speech quality or human intelligibility."],
              "summary": summary, "cases": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for profile, methods in summary.items():
        print(profile)
        for name, value in methods.items():
            corr = ("n/a" if value["mean_correlation"] is None
                    else f"{value['mean_correlation']:.4f}")
            print(f"  {name}: RMSE={value['mean_rmse']:.4f} bias={value['mean_bias']:+.4f} "
                  f"corr={corr} gated={value['mean_gated_fraction']:.1%}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
