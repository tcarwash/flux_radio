#!/usr/bin/env python3
"""Measure fixed-point Sotto slim encoder voicing decisions on GP0 vectors.

All DSP and codec work runs on the host in temporary build directories. The
source file is copied to a temporary path and receives a read-only class-count
hook; repository firmware and radio state are not changed.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

from k6_gp0_causal_denoise_sotto_v38 import causal_denoise
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, SEEDS, VOICE_IDS, read_flac, resample_poly,
)
from k6_gp0_nonstationary_noise_study import MASK_FLOOR, NOISE_SCALE
from rfsim.k6_mic import OFF_PCM_RMS_FS, k6_gp0_pcm

ROOT = Path(__file__).resolve().parents[1]
FIXED = ROOT / "c/fixed"
VPP = 0.10
TARGET_SNRS = (0.0, 6.0, 12.0)

HOOK = r'''
unsigned gp0_class_counts[4] = {0, 0, 0, 0};
void gp0_class_reset(void) { memset(gp0_class_counts, 0, sizeof gp0_class_counts); }
void gp0_class_read(unsigned *out) { memcpy(out, gp0_class_counts, sizeof gp0_class_counts); }
'''


def build_instrumented_codec(tmp: Path) -> Path:
    src = (FIXED / "sotto_fx_slim.c").read_text()
    marker = "unsigned slim_enc_bytes(void) { return (unsigned)sizeof(struct slim_enc); }"
    if src.count(marker) != 1:
        raise RuntimeError("encoder state marker changed; refusing to instrument")
    src = src.replace(marker, HOOK + "\n" + marker)
    point = "  wi = wo_index(p8);"
    hook = "  if ((unsigned)cls < 4u) gp0_class_counts[cls]++;\n" + point
    if src.count(point) != 1:
        raise RuntimeError("voicing decision marker changed; refusing to instrument")
    src = src.replace(point, hook)
    instrumented = tmp / "sotto_fx_slim_instrumented.c"
    instrumented.write_text(src)
    so = tmp / "libsotto_slim_instrumented.so"
    subprocess.run([
        "cc", "-O2", "-shared", "-fPIC", "-DSOTTO_V36",
        "-DSOTTO_PITCH_THIRD", "-DFX_NARROW_MUL",
        "-I", str(FIXED), "-I", str(FIXED.parent),
        str(instrumented), str(FIXED / "fx_tables.c"), "-o", str(so),
    ], check=True, capture_output=True, text=True)
    return so


class SlimEncoder:
    def __init__(self, so: Path):
        import ctypes as C
        self.C = C
        self.lib = C.CDLL(str(so))
        self.lib.slim_enc_bytes.restype = C.c_uint
        self.lib.slim_enc_init.argtypes = [C.c_void_p]
        self.lib.slim_encode10.argtypes = [C.c_void_p, C.POINTER(C.c_int16), C.POINTER(C.c_ubyte)]
        self.lib.slim_encode10.restype = C.c_int
        self.lib.slim_flush.argtypes = [C.c_void_p, C.POINTER(C.c_ubyte)]
        self.lib.slim_flush.restype = C.c_int
        self.lib.gp0_class_reset.argtypes = []
        self.lib.gp0_class_read.argtypes = [C.POINTER(C.c_uint)]

    def counts(self, samples: np.ndarray) -> list[int]:
        C = self.C
        x = np.asarray(np.clip(np.rint(samples * 32767.0), -32768, 32767), dtype=np.int16)
        x = np.pad(x, (0, (-len(x)) % 80))
        state = C.create_string_buffer(self.lib.slim_enc_bytes())
        pcm = (C.c_int16 * 80)()
        out = (C.c_ubyte * 6)()
        self.lib.slim_enc_init(state)
        self.lib.gp0_class_reset()
        for start in range(0, len(x), 80):
            block = x[start:start + 80]
            for i, value in enumerate(block):
                pcm[i] = int(value)
            self.lib.slim_encode10(state, pcm, out)
        while self.lib.slim_flush(state, out):
            pass
        counts = (C.c_uint * 4)()
        self.lib.gp0_class_read(counts)
        return [int(value) for value in counts]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--voices", nargs="*", default=list(VOICE_IDS))
    ap.add_argument("--snrs", nargs="*", type=float, default=list(TARGET_SNRS))
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    if any(v not in VOICE_IDS for v in args.voices):
        ap.error(f"voices must be selected from {VOICE_IDS}")
    report = {
        "schema": 1,
        "offline_only": True,
        "experiment": "fixed-point Sotto slim encoder voiced/unvoiced decision response to GP0 noise and causal Wiener prefilter",
        "encoder_source": "repository c/fixed/sotto_fx_slim.c, temporary host build with class-count-only instrumentation",
        "noise_source": "RFSim measured-response GP0 PCM model plus seeded GP0 noise",
        "input_vpp": VPP,
        "target_snr_db": args.snrs,
        "conditions": {},
        "limitations": [
            "The injected GP0 noise is modeled, not sample-exact physical capture noise.",
            "A class count does not establish decoded speech intelligibility or quality.",
            "Host execution does not qualify Cortex-M0 memory, cycles, or firmware integration.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="gp0-slim-voicing-") as td:
        tmp = Path(td)
        encoder = SlimEncoder(build_instrumented_codec(tmp))
        for voice in args.voices:
            ref16 = read_flac(SAMPLE_ROOT / f"librispeech_{voice}__original16.flac", 16000)
            reference = resample_poly(ref16, 1, 2)
            speech = reference / max(float(np.max(np.abs(reference))), 1e-15)
            clean = k6_gp0_pcm(speech, input_vpp=VPP, noise_rms_fs=0,
                               seed=0, response="measured", clip=True)
            cases = {"clean_model": {"counts": encoder.counts(clean)}}
            clean_rms = float(np.std(clean))
            for snr in args.snrs:
                snr_cases = []
                for seed in SEEDS:
                    rng_noise = k6_gp0_pcm(
                        np.zeros_like(speech), input_vpp=VPP,
                        noise_rms_fs=OFF_PCM_RMS_FS,
                        seed=seed, response="measured", clip=False,
                    )
                    scale = clean_rms / (10 ** (snr / 20)) / max(float(np.std(rng_noise)), 1e-15)
                    noise = rng_noise * scale
                    mixture = clean + noise
                    np.clip(mixture, -1.0, 1.0, out=mixture)
                    noise_preamble = k6_gp0_pcm(
                        np.zeros(FS // 2), input_vpp=VPP,
                        noise_rms_fs=OFF_PCM_RMS_FS * scale,
                        seed=seed + 991, response="measured", clip=False,
                    )
                    denoised = causal_denoise(
                        mixture, noise_preamble, "wiener",
                        noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR,
                    )
                    snr_cases.append({
                        "seed": seed,
                        "actual_input_snr_db": round(20 * math.log10(
                            max(clean_rms, 1e-15) / max(float(np.std(noise)), 1e-15)), 4),
                        "clip_fraction": round(float(np.mean(np.abs(mixture) >= 0.999)), 8),
                        "noisy_counts": encoder.counts(mixture),
                        "wiener_counts": encoder.counts(denoised),
                    })
                cases[f"snr_{snr:g}db"] = snr_cases
            report["conditions"][voice] = cases
            print(f"{voice}: completed clean and {len(args.snrs)} SNR levels", flush=True)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")
    for voice, cases in report["conditions"].items():
        for name, rows in cases.items():
            if name == "clean_model":
                rows = [rows]
            def percentages(counts: list[int]) -> list[float]:
                total = max(sum(counts), 1)
                return [round(100.0 * n / total, 1) for n in counts]
            noisy = np.mean([percentages(r["noisy_counts"]) for r in rows], axis=0) if name != "clean_model" else None
            if name == "clean_model":
                print(f"SUMMARY {voice} {name}: {percentages(rows[0]['counts'])}")
            else:
                wiener = np.mean([percentages(r["wiener_counts"]) for r in rows], axis=0)
                print(f"SUMMARY {voice} {name}: noisy={np.round(noisy, 1).tolist()} wiener={np.round(wiener, 1).tolist()}")


if __name__ == "__main__":
    main()
