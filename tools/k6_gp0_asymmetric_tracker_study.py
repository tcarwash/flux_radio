#!/usr/bin/env python3
"""Evaluate asymmetric causal level tracking on GP0 nonstationary noise."""
from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import (
    FS as DENOISE_FS, HOP, NFFT, _frames, _gain, _noise_power, causal_denoise,
)
from k6_gp0_noise_level_tracker_study import F_MAX_HZ, F_MIN_HZ
from k6_gp0_nonstationary_noise_study import (
    MASK_FLOOR, NOISE_SCALE, PREAMBLE_MS, PREAMBLE_SEED_OFFSET,
    TARGET_SNR_DB, VPP, envelope,
)
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, build_codec, encode_decode,
    read_flac, resample_poly, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

ASYM = (0.50, 0.90)  # previous-scale weight after rises, after falls
SYMMETRIC = (0.75, 0.90)
PROFILES = ("stationary", "step_up_6db", "step_down_6db",
            "bursts_9p5db", "swell_6db")


def asymmetric_denoise(mixture: np.ndarray, preamble: np.ndarray) -> tuple[np.ndarray, dict]:
    """Track upward noise changes quickly and downward changes conservatively."""
    q = 0.20
    preamble_power = np.abs(np.fft.rfft(_frames(preamble), axis=1)) ** 2
    noise_spectrum = _noise_power(preamble)
    frequencies = np.fft.rfftfreq(NFFT, 1.0 / DENOISE_FS)
    use_bins = (frequencies >= F_MIN_HZ) & (frequencies <= F_MAX_HZ)
    baseline_ratios = preamble_power[:, use_bins] / np.maximum(noise_spectrum[use_bins], 1e-30)
    baseline_q = float(np.median(np.quantile(baseline_ratios, q, axis=1)))
    pad_left = NFFT - HOP
    x = np.asarray(mixture, dtype=np.float64)
    work = np.pad(x, (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    ola = np.zeros(len(work), dtype=np.float64)
    norm = np.zeros(len(work), dtype=np.float64)
    scale_hat = 1.0
    scales = []
    attack_count = release_count = 0
    for start in starts:
        frame = work[start:start + NFFT] * window
        spectrum = np.fft.rfft(frame)
        power = np.abs(spectrum) ** 2
        measured = float(np.quantile(
            power[use_bins] / np.maximum(noise_spectrum[use_bins], 1e-30), q
        ) / baseline_q)
        measured = float(np.clip(measured, 0.20, 5.0))
        if measured > scale_hat:
            weight = ASYM[0]
            attack_count += 1
        else:
            weight = ASYM[1]
            release_count += 1
        scale_hat = weight * scale_hat + (1.0 - weight) * measured
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
        raise ArithmeticError("asymmetric tracker reconstruction failed")
    return out, {
        "scale_min": float(np.min(scales)),
        "scale_median": float(np.median(scales)),
        "scale_max": float(np.max(scales)),
        "attack_frames": attack_count,
        "release_frames": release_count,
    }


def main() -> None:
    out = Path("recovery/gp0_raw/gp0-asymmetric-tracker-study-20261128.json")
    if out.exists():
        raise SystemExit(f"refusing to overwrite {out}")
    static_name = f"static_preamble_wiener/scale={NOISE_SCALE:g}/floor={MASK_FLOOR:g}"
    asym_name = "causal_quantile_asymmetric/q=0.20/rise_weight=0.50/fall_weight=0.90"
    sym_names = {
        w: f"causal_quantile_symmetric/q=0.20/previous_weight={w:g}"
        for w in SYMMETRIC
    }
    methods = ("noisy", static_name, *sym_names.values(), asym_name)
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "asymmetric attack-release causal GP0 noise level tracking",
        "source_model": "RFSim measured-response GP0 PCM plus banded statistical noise with level envelopes",
        "codec": "Sotto v3.8 host encoder/decoder, temporary build, 8kHz",
        "target_global_source_snr_db": TARGET_SNR_DB,
        "profiles": list(PROFILES),
        "asymmetric_tracker": {"spectral_quantile": 0.20,
                                "rise_previous_weight": ASYM[0],
                                "fall_previous_weight": ASYM[1],
                                "rise_detection": "measured_scale > previous estimate"},
        "symmetric_comparators": [{"quantile": 0.20, "previous_weight": w}
                                   for w in SYMMETRIC],
        "speech_vectors": list(VOICE_IDS),
        "noise_seeds": list(SEEDS),
        "limitations": [
            "The attack/release switch has no speech VAD or persistence threshold; speech-driven upward estimates can trigger attack mode.",
            "Noise spectrum is fixed and banded; envelopes alter only its amplitude.",
            "One global SNR and eight speech vectors are used; metrics are objective proxies, not human preference.",
            "Host filtering is not MCU timing, RAM, or firmware qualification.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-asym-tracker-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            ref_path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            reference = resample_poly(read_flac(ref_path, 16000), 1, 2)
            normalized = reference / max(float(np.max(np.abs(reference))), 1e-15)
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            decoded_clean = encode_decode(encoder, decoder, clean, temp, f"{voice}-clean")
            clean_rms, clean_out_rms = float(np.std(clean)), float(np.std(decoded_clean))
            profile_rows = {}
            for profile in PROFILES:
                rows = []
                env = envelope(profile, len(clean))
                initial_level = float(np.sqrt(np.mean(env[:FS * PREAMBLE_MS // 1000] ** 2)))
                for seed in SEEDS:
                    base_noise = generate_gp0_noise(
                        len(clean), seed=seed, model="banded", rms_fs=OFF_PCM_RMS_FS)
                    scale = clean_rms / (10.0 ** (TARGET_SNR_DB / 20.0)) / max(float(np.std(base_noise * env)), 1e-15)
                    noise = base_noise * env * scale
                    noisy = clean + noise
                    np.clip(noisy, -1.0, 1.0, out=noisy)
                    preamble = generate_gp0_noise(
                        FS * PREAMBLE_MS // 1000,
                        seed=seed + PREAMBLE_SEED_OFFSET,
                        model="banded", rms_fs=OFF_PCM_RMS_FS * initial_level * scale)
                    decoded_noisy = encode_decode(encoder, decoder, noisy, temp,
                                                  f"{voice}-{profile}-{seed}-noisy")
                    static = causal_denoise(noisy, preamble, "wiener",
                                            noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
                    decoded_static = encode_decode(encoder, decoder, static, temp,
                                                   f"{voice}-{profile}-{seed}-static")
                    treatments = {
                        "noisy": {"pre_codec": score(reference, noisy, clean_rms),
                                  "post_codec": score(reference, decoded_noisy, clean_out_rms)},
                        static_name: {"pre_codec": score(reference, static, clean_rms),
                                      "post_codec": score(reference, decoded_static, clean_out_rms)},
                    }
                    for weight, name in sym_names.items():
                        tracked = symmetric_denoise(
                            noisy, preamble, quantile=0.20, previous_weight=weight)
                        decoded = encode_decode(encoder, decoder, tracked, temp,
                                                f"{voice}-{profile}-{seed}-sym{weight:g}")
                        treatments[name] = {
                            "pre_codec": score(reference, tracked, clean_rms),
                            "post_codec": score(reference, decoded, clean_out_rms),
                        }
                    asym, asym_info = asymmetric_denoise(noisy, preamble)
                    decoded_asym = encode_decode(encoder, decoder, asym, temp,
                                                 f"{voice}-{profile}-{seed}-asym")
                    treatments[asym_name] = {
                        "pre_codec": score(reference, asym, clean_rms),
                        "post_codec": score(reference, decoded_asym, clean_out_rms),
                    }
                    rows.append({"seed": seed,
                                 "source_clip_fraction": float(np.mean(np.abs(noisy) >= 0.999)),
                                 "attack_release": asym_info,
                                 "treatments": treatments})
                profile_rows[profile] = rows
                print(f"{voice} {profile}: completed {len(rows)} seeds", flush=True)
            report["conditions"][voice] = {"profiles": profile_rows}

    summary = {}
    for profile in PROFILES:
        summary[profile] = {}
        for method in methods:
            estoi, pesq, wins_static, wins_noisy = [], [], 0, 0
            for voice in report["conditions"].values():
                for row in voice["profiles"][profile]:
                    current = row["treatments"][method]["post_codec"]
                    static = row["treatments"][static_name]["post_codec"]
                    noisy = row["treatments"]["noisy"]["post_codec"]
                    estoi.append(current["estoi"])
                    pesq.append(current["pesq_nb"])
                    wins_static += current["estoi"] > static["estoi"]
                    wins_noisy += current["estoi"] > noisy["estoi"]
            summary[profile][method] = {
                "case_count": len(estoi),
                "mean_post_codec_estoi": round(float(np.mean(estoi)), 6),
                "mean_post_codec_pesq_nb": round(float(np.mean(pesq)), 5),
                "cases_better_than_static": int(wins_static),
                "cases_better_than_noisy": int(wins_noisy),
            }
    report["summary"] = summary
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    for profile in PROFILES:
        print(profile)
        for method, values in summary[profile].items():
            print(f"  {method}: ESTOI={values['mean_post_codec_estoi']:.6f} "
                  f"PESQ-NB={values['mean_post_codec_pesq_nb']:.5f} "
                  f"wins_static={values['cases_better_than_static']}/24")
    print(f"wrote {out}")


def symmetric_denoise(mixture: np.ndarray, preamble: np.ndarray, *,
                      quantile: float, previous_weight: float) -> np.ndarray:
    """Same quantile tracker as prior study, parameterized for paired sweep."""
    noise_spectrum = _noise_power(preamble)
    frequencies = np.fft.rfftfreq(NFFT, 1.0 / FS)
    use_bins = (frequencies >= F_MIN_HZ) & (frequencies <= F_MAX_HZ)
    preamble_power = np.abs(np.fft.rfft(_frames(preamble), axis=1)) ** 2
    base = preamble_power[:, use_bins] / np.maximum(noise_spectrum[use_bins], 1e-30)
    baseline_q = float(np.median(np.quantile(base, quantile, axis=1)))
    pad_left = NFFT - HOP
    x = np.asarray(mixture, dtype=np.float64)
    work = np.pad(x, (pad_left, NFFT))
    window = np.hanning(NFFT + 1)[:-1]
    starts = np.arange(0, len(work) - NFFT + 1, HOP)
    ola, norm = np.zeros(len(work)), np.zeros(len(work))
    scale_hat = 1.0
    for start in starts:
        frame = work[start:start + NFFT] * window
        spectrum = np.fft.rfft(frame)
        power = np.abs(spectrum) ** 2
        measured = float(np.clip(np.quantile(
            power[use_bins] / np.maximum(noise_spectrum[use_bins], 1e-30), quantile
        ) / baseline_q, 0.20, 5.0))
        scale_hat = previous_weight * scale_hat + (1.0 - previous_weight) * measured
        gain = _gain(power, noise_spectrum * scale_hat, "wiener",
                     noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
        filtered = np.fft.irfft(spectrum * gain, n=NFFT)
        ola[start:start + NFFT] += filtered * window
        norm[start:start + NFFT] += window * window
    valid = norm > 1e-10
    ola[valid] /= norm[valid]
    return ola[pad_left:pad_left + len(x)]


if __name__ == "__main__":
    main()
