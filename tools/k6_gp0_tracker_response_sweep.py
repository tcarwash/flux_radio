#!/usr/bin/env python3
"""Sweep causal GP0 spectral-quantile tracker response speed offline.

Uses the same RFSim GP0 source/noise and Sotto v3.8 proxy setup as the broader
tracker study, focusing on stationary and 6dB step-down profiles. No radio or
firmware interaction occurs.
"""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import causal_denoise
from k6_gp0_noise_level_tracker_study import causal_quantile_tracker_denoise
from k6_gp0_nonstationary_noise_study import (
    MASK_FLOOR, NOISE_SCALE, PREAMBLE_MS, PREAMBLE_SEED_OFFSET,
    TARGET_SNR_DB, VPP, envelope,
)
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

PROFILES = ("stationary", "step_down_6db")
PARAMS = ((0.20, 0.50), (0.20, 0.75), (0.20, 0.90),
          (0.35, 0.50), (0.35, 0.75), (0.35, 0.90))


def main() -> None:
    out = Path("recovery/gp0_raw/gp0-tracker-response-sweep-20261128.json")
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    static_name = f"static_preamble_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}"
    tracker_names = {
        (q, w): f"causal_quantile_tracker/q={q:g}/previous_weight={w:g}"
        for q, w in PARAMS
    }
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "causal scalar GP0 noise tracker response-speed sweep",
        "source_model": "RFSim measured-response GP0 PCM plus banded statistical GP0 noise",
        "codec": "Sotto v3.8 C CLI, temporary host build, 8kHz",
        "target_global_source_snr_db": TARGET_SNR_DB,
        "profiles": list(PROFILES),
        "tracker_parameters": [
            {"spectral_quantile": q, "previous_scale_weight": w}
            for q, w in PARAMS
        ],
        "speech_vectors": list(VOICE_IDS),
        "noise_seeds": list(SEEDS),
        "limitations": [
            "The tracker has no speech VAD and can mistake dense speech for noise.",
            "Noise changes only in scalar level; its banded spectrum is held fixed.",
            "ESTOI and PESQ-NB are offline proxies, not human readability or hardware evidence.",
            "Only stationary and one 6dB step-down profile are included in this parameter sweep.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-tracker-response-") as td:
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
            profile_data = {}
            for profile in PROFILES:
                rows = []
                for seed in SEEDS:
                    env = envelope(profile, len(clean))
                    raw_noise = generate_gp0_noise(
                        len(clean), seed=seed, model="banded", rms_fs=OFF_PCM_RMS_FS)
                    scale = (clean_pre_rms / (10.0 ** (TARGET_SNR_DB / 20.0))) / float(np.std(raw_noise * env))
                    noisy = clean + raw_noise * env * scale
                    np.clip(noisy, -1.0, 1.0, out=noisy)
                    actual_snr = 20.0 * math.log10(
                        max(clean_pre_rms, 1e-15) /
                        max(float(np.std(noisy - clean)), 1e-15))
                    initial_level = float(np.sqrt(np.mean(
                        env[:FS * PREAMBLE_MS // 1000] ** 2)))
                    preamble = generate_gp0_noise(
                        FS * PREAMBLE_MS // 1000,
                        seed=seed + PREAMBLE_SEED_OFFSET,
                        model="banded",
                        rms_fs=OFF_PCM_RMS_FS * initial_level * scale)
                    decoded_noisy = encode_decode(
                        encoder, decoder, noisy, temp, f"{voice}-{profile}-{seed}-noisy")
                    static = causal_denoise(
                        noisy, preamble, "wiener", noise_scale=NOISE_SCALE,
                        mask_floor=MASK_FLOOR)
                    decoded_static = encode_decode(
                        encoder, decoder, static, temp,
                        f"{voice}-{profile}-{seed}-static")
                    treatments = {
                        "noisy": {
                            "pre_codec": score(reference, noisy, clean_pre_rms),
                            "post_codec": score(reference, decoded_noisy, clean_post_rms),
                        },
                        static_name: {
                            "pre_codec": score(reference, static, clean_pre_rms),
                            "post_codec": score(reference, decoded_static, clean_post_rms),
                        },
                    }
                    diagnostics = {}
                    for (q, w), name in tracker_names.items():
                        filtered, info = causal_quantile_tracker_denoise(
                            noisy, preamble, spectral_quantile=q,
                            previous_weight=w)
                        decoded = encode_decode(
                            encoder, decoder, filtered, temp,
                            f"{voice}-{profile}-{seed}-q{q:g}-w{w:g}")
                        treatments[name] = {
                            "pre_codec": score(reference, filtered, clean_pre_rms),
                            "post_codec": score(reference, decoded, clean_post_rms),
                        }
                        diagnostics[name] = info
                    rows.append({
                        "seed": seed,
                        "actual_global_source_snr_db": round(actual_snr, 4),
                        "source_clip_fraction": round(float(np.mean(np.abs(noisy) >= 0.999)), 8),
                        "tracker_diagnostics": diagnostics,
                        "treatments": treatments,
                    })
                profile_data[profile] = rows
                print(f"{voice} {profile}: completed {len(rows)} seeds", flush=True)
            report["conditions"][voice] = {
                "reference_file": ref_path.name,
                "clean_model_baseline": {
                    "pre_codec": score(reference, clean, clean_pre_rms),
                    "post_codec": score(reference, clean_decoded, clean_post_rms),
                },
                "profiles": profile_data,
            }

    methods = ("noisy", static_name, *tracker_names.values())
    summary = {}
    for profile in PROFILES:
        summary[profile] = {}
        for method in methods:
            estoi, pesq, wins_static, wins_noisy = [], [], 0, 0
            for voice_data in report["conditions"].values():
                for row in voice_data["profiles"][profile]:
                    treatment = row["treatments"][method]["post_codec"]
                    estoi.append(treatment["estoi"])
                    pesq.append(treatment["pesq_nb"])
                    if method not in ("noisy", static_name):
                        wins_static += treatment["estoi"] > row["treatments"][static_name]["post_codec"]["estoi"]
                    wins_noisy += treatment["estoi"] > row["treatments"]["noisy"]["post_codec"]["estoi"]
            summary[profile][method] = {
                "case_count": len(estoi),
                "mean_post_codec_estoi": round(float(np.mean(estoi)), 6),
                "mean_post_codec_pesq_nb": round(float(np.mean(pesq)), 5),
                "cases_better_than_static": int(wins_static),
                "cases_better_than_noisy": int(wins_noisy),
            }
    report["summary"] = summary
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    for profile in PROFILES:
        print(f"SUMMARY {profile}")
        for method, row in summary[profile].items():
            print(f"  {method}: ESTOI={row['mean_post_codec_estoi']:.6f} "
                  f"PESQ-NB={row['mean_post_codec_pesq_nb']:.5f} "
                  f"wins_static={row['cases_better_than_static']}/24 "
                  f"wins_noisy={row['cases_better_than_noisy']}/24")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
