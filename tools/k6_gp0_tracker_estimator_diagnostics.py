#!/usr/bin/env python3
"""Measure causal noise-scale tracker error with and without speech present."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import NFFT, HOP, _frames, _noise_power
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, read_flac, resample_poly,
)
from k6_gp0_nonstationary_noise_study import (
    PREAMBLE_MS, PREAMBLE_SEED_OFFSET, TARGET_SNR_DB, VPP, envelope,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

PROFILES = ("stationary", "step_down_6db", "step_up_6db", "bursts_9p5db")
PARAMS = tuple((q, w) for q in (0.20, 0.35) for w in (0.50, 0.75, 0.90))
F_MIN_HZ, F_MAX_HZ = 200.0, 3400.0


def track_level(signal: np.ndarray, preamble: np.ndarray, q: float,
                weight: float) -> np.ndarray:
    """Duplicate the study estimator and return one estimated scale per STFT frame."""
    noise_spectrum = _noise_power(preamble)
    frequencies = np.fft.rfftfreq(NFFT, 1.0 / FS)
    bins = (frequencies >= F_MIN_HZ) & (frequencies <= F_MAX_HZ)
    preamble_power = np.abs(np.fft.rfft(_frames(preamble), axis=1)) ** 2
    baseline_ratios = preamble_power[:, bins] / np.maximum(noise_spectrum[bins], 1e-30)
    baseline_q = float(np.median(np.quantile(baseline_ratios, q, axis=1)))
    pad_left = NFFT - HOP
    work = np.pad(np.asarray(signal, dtype=np.float64), (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    estimate = 1.0
    values = []
    for start in starts:
        frame = work[start:start + NFFT] * window
        power = np.abs(np.fft.rfft(frame)) ** 2
        current_q = float(np.quantile(
            power[bins] / np.maximum(noise_spectrum[bins], 1e-30), q))
        measured = float(np.clip(current_q / baseline_q, 0.20, 5.0))
        estimate = weight * estimate + (1.0 - weight) * measured
        values.append(estimate)
    return np.asarray(values)


def main() -> None:
    out = Path("recovery/gp0_raw/gp0-tracker-estimator-diagnostics-20261128.json")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-only", action="store_true",
                        help="print the existing report summary without recomputing")
    args = parser.parse_args()
    if args.summary_only:
        report = json.loads(out.read_text())
        for profile, methods in report["summary"].items():
            print(profile)
            for name, item in methods.items():
                corr = ("n/a" if item["mean_scale_correlation"] is None else
                        f"{item['mean_scale_correlation']:.4f}")
                print(f"  {name}: RMSE={item['mean_scale_rmse']:.4f} "
                      f"bias={item['mean_scale_bias']:+.4f} corr={corr}")
        return
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    rows = []
    for voice in VOICE_IDS:
        path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
        reference = resample_poly(read_flac(path, 16000), 1, 2)
        normalized = reference / max(float(np.max(np.abs(reference))), 1e-15)
        clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                           response="measured", clip=True)
        clean_rms = float(np.std(clean))
        for profile in PROFILES:
            env = envelope(profile, len(clean))
            initial_level = float(np.sqrt(np.mean(env[:FS * PREAMBLE_MS // 1000] ** 2)))
            true_scale_samples = (env / initial_level) ** 2
            centers = np.arange(0, len(env) + (NFFT - HOP) + 1, HOP) + NFFT // 2 - (NFFT - HOP)
            centers = np.clip(centers, 0, len(env) - 1)
            true_frame_scale = true_scale_samples[centers]
            for seed in SEEDS:
                raw_noise = generate_gp0_noise(
                    len(clean), seed=seed, model="banded", rms_fs=OFF_PCM_RMS_FS)
                noise_amplitude = (clean_rms / (10.0 ** (TARGET_SNR_DB / 20.0)) /
                                   max(float(np.std(raw_noise * env)), 1e-15))
                noise = raw_noise * env * noise_amplitude
                mixture = clean + noise
                np.clip(mixture, -1.0, 1.0, out=mixture)
                preamble = generate_gp0_noise(
                    FS * PREAMBLE_MS // 1000,
                    seed=seed + PREAMBLE_SEED_OFFSET,
                    model="banded",
                    rms_fs=OFF_PCM_RMS_FS * initial_level * noise_amplitude)
                for q, w in PARAMS:
                    for mode, signal in (("noise_only", noise), ("speech_plus_noise", mixture)):
                        estimated = track_level(signal, preamble, q, w)
                        n = min(len(estimated), len(true_frame_scale))
                        estimated = estimated[:n]
                        truth = true_frame_scale[:n]
                        delta = estimated - truth
                        rows.append({
                            "voice": voice,
                            "profile": profile,
                            "seed": seed,
                            "quantile": q,
                            "previous_weight": w,
                            "mode": mode,
                            "scale_rmse": float(np.sqrt(np.mean(delta * delta))),
                            "scale_bias": float(np.mean(delta)),
                            "scale_correlation": float(np.corrcoef(estimated, truth)[0, 1])
                            if np.std(truth) > 0 else None,
                            "median_estimate": float(np.median(estimated)),
                            "median_truth": float(np.median(truth)),
                        })
            print(f"{voice} {profile}: completed {len(SEEDS)} seeds", flush=True)

    summary = {}
    for profile in PROFILES:
        summary[profile] = {}
        for q, w in PARAMS:
            for mode in ("noise_only", "speech_plus_noise"):
                selected = [r for r in rows if r["profile"] == profile and
                            r["quantile"] == q and r["previous_weight"] == w and
                            r["mode"] == mode]
                correlations = [r["scale_correlation"] for r in selected
                                if r["scale_correlation"] is not None]
                summary[profile][f"q={q:g}/w={w:g}/{mode}"] = {
                    "case_count": len(selected),
                    "mean_scale_rmse": float(np.mean([r["scale_rmse"] for r in selected])),
                    "mean_scale_bias": float(np.mean([r["scale_bias"] for r in selected])),
                    "mean_scale_correlation": float(np.mean(correlations)) if correlations else None,
                    "mean_median_estimate": float(np.mean([r["median_estimate"] for r in selected])),
                    "mean_median_truth": float(np.mean([r["median_truth"] for r in selected])),
                }
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "causal lower-spectral-quantile level estimation error with/without speech",
        "source_model": "RFSim measured-response GP0 PCM plus banded synthetic GP0 noise",
        "target_global_source_snr_db": TARGET_SNR_DB,
        "profiles": list(PROFILES),
        "speech_vectors": list(VOICE_IDS),
        "noise_seeds": list(SEEDS),
        "parameters": [{"quantile": q, "previous_weight": w} for q, w in PARAMS],
        "limitations": [
            "Noise remains a statistical banded model with scalar level envelopes.",
            "This measures the estimator's level error, not denoised speech quality or intelligibility.",
            "The spectral estimator is duplicated to expose trajectories; it matches the tracker-study calibration and EMA equations.",
        ],
        "summary": summary,
        "cases": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for profile, methods in summary.items():
        print(profile)
        for name, item in methods.items():
            corr = ("n/a" if item["mean_scale_correlation"] is None else
                    f"{item['mean_scale_correlation']:.4f}")
            print(f"  {name}: RMSE={item['mean_scale_rmse']:.4f} "
                  f"bias={item['mean_scale_bias']:+.4f} "
                  f"corr={corr}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
