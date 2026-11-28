#!/usr/bin/env python3
"""Screen causal Wiener STFT frame sizes through the real Sotto v3.8 codec.

This is a host-side, held-out synthetic GP0 study. It asks whether shorter
STFT frames reduce algorithmic delay while retaining speech-quality proxy gains.
It is not an MCU cycle/RAM or physical-radio qualification.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from tools import k6_gp0_causal_denoise_sotto_v38 as denoise
from tools.k6_gp0_noise_sotto_v38_study import (
    FS, INPUT_VPP, ROOT, SAMPLE_ROOT, build_codec, encode_decode, read_flac, score,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm

HELDOUT = ("6313_f", "652_m", "777_m", "8842_f")
SEEDS = (17, 83, 211)
FRAME_SIZES = (64, 128, 256)
VPP = 0.1
PREAMBLE_SECONDS = 0.5
PREAMBLE_SEED_OFFSET = 1_000_003
NOISE_SCALE = 2.5
MASK_FLOOR = 0.03


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refusing to overwrite an existing report")

    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "causal Wiener frame-size screen through actual Sotto v3.8 8kHz codec",
        "heldout_voices": list(HELDOUT),
        "seeds": list(SEEDS),
        "input_vpp": VPP,
        "target_snr_db": -1.0,
        "frame_sizes_samples": list(FRAME_SIZES),
        "frame_latency_ms_upper_bound": {str(n): n * 1000 / FS for n in FRAME_SIZES},
        "hop_fraction": "one quarter of frame",
        "noise_preamble_ms": int(PREAMBLE_SECONDS * 1000),
        "mask": {"type": "Wiener power", "noise_scale": NOISE_SCALE, "floor": MASK_FLOOR},
        "limitations": [
            "Statistical RFSim GP0 source model and independent synthetic noise preamble; no physical speech capture.",
            "PESQ and ESTOI are proxies, not listening scores.",
            "NumPy FFT is host-side; no K6 cycle, stack, or RAM measurements.",
            "Frame latency is a transform bound; the streaming module schedule has not been implemented.",
        ],
        "cases": [],
    }

    with tempfile.TemporaryDirectory(prefix="gp0-wiener-framesize-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in HELDOUT:
            src = read_flac(SAMPLE_ROOT / f"librispeech_{voice}__original16.flac", 16000)
            reference = resample_poly(src, 1, 2)
            normalized = reference / float(np.max(np.abs(reference)))
            clean_pcm = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                                   response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean_pcm, temp,
                                          f"{voice}-clean")
            target_rms = float(np.std(clean_decoded))
            for seed in SEEDS:
                noisy_pcm = k6_gp0_pcm(normalized, input_vpp=VPP,
                                       noise_rms_fs=OFF_PCM_RMS_FS, seed=seed,
                                       response="measured", noise_model="banded", clip=True)
                noisy_decoded = encode_decode(encoder, decoder, noisy_pcm, temp,
                                              f"{voice}-{seed}-noisy")
                row = {"voice": voice, "seed": seed,
                       "noisy": score(reference, noisy_decoded, target_rms),
                       "frames": {}}
                preamble = generate_gp0_noise(
                    int(FS * PREAMBLE_SECONDS), seed=seed + PREAMBLE_SEED_OFFSET,
                    model="banded", rms_fs=OFF_PCM_RMS_FS)
                for nfft in FRAME_SIZES:
                    denoise.NFFT = nfft
                    denoise.HOP = nfft // 4
                    processed = denoise.causal_denoise(
                        noisy_pcm, preamble, "wiener", noise_scale=NOISE_SCALE,
                        mask_floor=MASK_FLOOR)
                    decoded = encode_decode(encoder, decoder, processed, temp,
                                            f"{voice}-{seed}-{nfft}")
                    row["frames"][str(nfft)] = score(reference, decoded, target_rms)
                report["cases"].append(row)
                print(f"{voice}/{seed}: noisy={row['noisy']['estoi']:.5f} " +
                      " ".join(f"n{n}={row['frames'][str(n)]['estoi']:.5f}" for n in FRAME_SIZES),
                      flush=True)

    report["summary"] = {}
    for name in ("noisy", *(str(n) for n in FRAME_SIZES)):
        cells = [c["noisy"] if name == "noisy" else c["frames"][name]
                 for c in report["cases"]]
        report["summary"][name] = {
            "case_count": len(cells),
            "mean_estoi": round(float(np.mean([x["estoi"] for x in cells])), 5),
            "mean_pesq_nb": round(float(np.mean([x["pesq_nb"] for x in cells])), 4),
            "cases_better_estoi_than_noisy": sum(
                report["cases"][i]["frames"][name]["estoi"] > report["cases"][i]["noisy"]["estoi"]
                for i in range(len(report["cases"]))) if name != "noisy" else 0,
        }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    print(json.dumps(report["summary"], indent=2), flush=True)


if __name__ == "__main__":
    main()
