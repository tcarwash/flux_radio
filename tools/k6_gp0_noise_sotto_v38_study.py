#!/usr/bin/env python3
"""Measure a modeled GP0 noise floor through the Sotto v3.8 8kHz codec path.

Uses the limited, measured-inspired PCM noise model from RFSim on held-out
clean speech, then runs the repository's real C Sotto encoder and decoder in
temporary directories. The experiment links a GP0 source model to codec
metrics; it is not a physical-microphone model, hardware result, or listening
test.
"""
from __future__ import annotations

import argparse
import io
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
RFSIM_ROOT = Path("/storage5/dev/rfsim-k6-gp0-20261127")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(RFSIM_ROOT))
pkg = sys.modules.get("tools")
if pkg is None or str(ROOT / "tools") not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules["tools"] = pkg

from tools.k6_sotto_perceptual_proxy_v38 import (
    ROOT as FLUX_ROOT, SAMPLE_ROOT, VOICE_IDS, read_flac,
)
from tools.k6_sotto_fidelity_previews import FS, normalize_for_audition
from rfsim.k6_mic import OFF_PCM_RMS_FS, k6_gp0_pcm

SEEDS = (17, 83, 211)
INPUT_VPP = (0.025, 0.05, 0.10)
NOISE_REDUCTIONS_DB = (0.0, 0.6, 1.4, 8.0, 10.0)


def build_codec(tmp: Path) -> tuple[Path, Path]:
    source = FLUX_ROOT / "sotto/c"
    target = tmp / "c"
    shutil.copytree(source, target,
                    ignore=shutil.ignore_patterns("*.o", "sotto_enc", "sotto_dec", "libsotto.*"))
    codec2_source = FLUX_ROOT / "sotto/codec/codec2"
    c2build = tmp / "codec2-build"
    configured = subprocess.run(
        ["cmake", "-S", str(codec2_source), "-B", str(c2build),
         "-DCMAKE_BUILD_TYPE=Release", "-DUNITTEST=OFF"],
        capture_output=True, text=True, timeout=90)
    if configured.returncode:
        raise RuntimeError(f"temporary Codec2 configure failed:\n{configured.stderr[-2000:]}")
    built = subprocess.run(["cmake", "--build", str(c2build), "--target", "codec2", "-j2"],
                           capture_output=True, text=True, timeout=180)
    if built.returncode:
        raise RuntimeError(f"temporary Codec2 build failed:\n{built.stderr[-2500:]}")
    c2lib = c2build / "src"
    if not (c2lib / "libcodec2.so").is_file():
        raise RuntimeError("temporary libcodec2.so was not produced")
    p = subprocess.run(
        ["make", "-C", str(target), "-j2", "sotto_enc", "sotto_dec",
         f"CODEC2_SRC={codec2_source / 'src'}", f"CODEC2_LIB={c2lib}", "VARIANT=v3.8"],
        capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f"temporary Sotto codec build failed:\n{p.stderr[-3000:]}")
    return target / "sotto_enc", target / "sotto_dec"


def encode_decode(encoder: Path, decoder: Path, pcm: np.ndarray, tmp: Path, tag: str) -> np.ndarray:
    source = tmp / f"{tag}.raw"
    bits = tmp / f"{tag}.bit"
    decoded = tmp / f"{tag}-decoded.raw"
    samples = np.clip(np.rint(np.asarray(pcm) * 32768.0), -32768, 32767).astype("<i2")
    if len(samples) % 80:
        samples = np.pad(samples, (0, 80 - len(samples) % 80))
    samples.tofile(source)
    subprocess.run([str(encoder), str(source), str(bits)], check=True,
                   capture_output=True, timeout=90)
    subprocess.run([str(decoder), str(bits), str(decoded)], check=True,
                   capture_output=True, timeout=90)
    return np.fromfile(decoded, dtype="<i2").astype(np.float64) / 32768.0


def score(reference: np.ndarray, candidate: np.ndarray, target_rms: float) -> dict:
    # Keep source/codec helpers importable in offline consumers that only need
    # the model and codec harness; the objective metrics remain optional until
    # score() is actually called.
    from pesq import pesq
    from pystoi import stoi
    n = min(len(reference), len(candidate))
    ref = reference[:n]
    got = candidate[:n]
    # normalize_for_audition accepts normalized float audio and performs the
    # PCM16 quantization itself. Passing int16 here accidentally divided its
    # reported gain by 32768 (90.309dB); the resulting samples were effectively
    # level-matched already, but gain metadata was wrong and quantization was
    # needlessly performed twice.
    matched, gain = normalize_for_audition(got, target_rms)
    estimate = matched.astype(np.float64) / 32768.0
    return {
        "pesq_nb": round(float(pesq(FS, ref, estimate, "nb")), 4),
        "estoi": round(float(stoi(ref, estimate, FS, extended=True)), 5),
        "level_match_gain_db": round(20 * math.log10(max(gain, 1e-12)), 3),
        "clipped_samples_after_level_match": int(np.count_nonzero(np.abs(matched.astype(np.int32)) >= 32767)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--noise-model", choices=("banded", "white"), default="banded",
                    help="statistical GP0 PCM noise spectrum assumed by the RFSim model")
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    try:
        codec_revision = subprocess.run(["git", "-C", str(FLUX_ROOT / "sotto/codec/codec2"),
                                         "rev-parse", "HEAD"], check=True, capture_output=True,
                                        text=True).stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("could not identify repository Codec2 revision") from exc
    report = {
        "schema": 1,
        "offline_only": True,
        "codec": "Sotto v3.8 float C encoder/decoder, 8kHz input path; temporary build",
        "codec2_source_revision": codec_revision,
        "source_model": f"rfsim.k6_mic.k6_gp0_pcm with measured response points and {args.noise_model} simulated noise",
        "noise_model": args.noise_model,
        "source_model_noise_rms_fs": OFF_PCM_RMS_FS,
        "noise_reductions_db_vs_measured_model": list(NOISE_REDUCTIONS_DB),
        "input_vpp_scenario_values": list(INPUT_VPP),
        "random_seeds": list(SEEDS),
        "reference_and_metrics": "clean 16kHz reference downsampled to 8kHz; narrowband PESQ and ESTOI",
        "speech_level_normalization": "each clean speech excerpt is peak-normalized to unit amplitude before applying input_vpp, as required by the source-model convention",
        "limitations": [
            "The GP0 model is a statistical approximation from three tone points and short noise captures; it is not sample-exact hardware noise.",
            "Input Vpp values only scale the laboratory reference; they are not a calibrated mapping from acoustic speech SPL to mic voltage.",
            "Peak-normalizing each speech excerpt removes its dataset recording gain; speech crest factor and source level still vary by excerpt.",
            "Noise reductions emulate a lower broadband PCM noise RMS; they do not simulate a particular filter's frequency response or MCU implementation.",
            "The 8kHz codec path omits the v3.8 4-8kHz hiss band and does not include radio-channel loss.",
            "PESQ and ESTOI are objective proxies, not human listening results.",
            "No firmware, radio, or production build was changed; Codec2/Sotto are compiled only in a temporary directory.",
        ],
        "conditions": {},
    }
    with tempfile.TemporaryDirectory(prefix="gp0-noise-sotto-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for voice in VOICE_IDS:
            source_path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            original16 = read_flac(source_path, 16000)
            reference = resample_poly(original16, 1, 2)
            source_peak = float(np.max(np.abs(reference)))
            if not np.isfinite(source_peak) or source_peak <= 0:
                raise ValueError(f"invalid clean reference amplitude: {source_path}")
            model_input = reference / source_peak
            for vpp in INPUT_VPP:
                vpp_key = f"{vpp:.3f}Vpp"
                cell_key = f"librispeech_{voice}__{vpp_key}"
                clean_source = k6_gp0_pcm(model_input, input_vpp=vpp, noise_rms_fs=0,
                                          seed=0, response="measured", noise_model=args.noise_model)
                clean_decoded = encode_decode(encoder, decoder, clean_source, temp,
                                              f"{voice}-{vpp_key}-clean")
                target_rms = float(np.std(clean_decoded))
                clean_score = score(reference, clean_decoded, target_rms)
                scenarios: dict[str, list[dict]] = {"noise_free": [clean_score]}
                for reduction_db in NOISE_REDUCTIONS_DB:
                    if reduction_db == 0:
                        label = "measured_noise"
                    else:
                        label = f"noise_minus_{reduction_db:g}dB"
                    rows = []
                    for seed in SEEDS:
                        rms = OFF_PCM_RMS_FS * (10 ** (-reduction_db / 20))
                        source = k6_gp0_pcm(model_input, input_vpp=vpp, noise_rms_fs=rms,
                                             seed=seed, response="measured", noise_model=args.noise_model)
                        clip_fraction = float(np.count_nonzero(np.abs(source) >= 0.999) / len(source))
                        decoded = encode_decode(encoder, decoder, source, temp,
                                                f"{voice}-{vpp_key}-{label}-{seed}")
                        row = score(reference, decoded, target_rms)
                        row["seed"] = seed
                        row["source_clip_fraction"] = round(clip_fraction, 7)
                        rows.append(row)
                    scenarios[label] = rows
                report["conditions"][cell_key] = {
                    "reference_file": source_path.name,
                    "source_peak_normalization_gain_db": round(-20 * math.log10(source_peak), 3),
                    "modeled_clean_signal_rms_fs": round(float(np.std(clean_source)), 7),
                    "modeled_source_snr_db_at_measured_noise": round(
                        20 * math.log10(max(float(np.std(clean_source)), 1e-15) / OFF_PCM_RMS_FS), 3),
                    "duration_s": round(min(len(reference), len(clean_decoded)) / FS, 3),
                    "noise_free_gp0_model_baseline": clean_score,
                    "scenarios": scenarios,
                }
                print(f"{cell_key}: clean PESQ {clean_score['pesq_nb']:.3f} ESTOI {clean_score['estoi']:.4f}")
                for label, rows in scenarios.items():
                    if label == "noise_free":
                        continue
                    p = np.array([r["pesq_nb"] for r in rows])
                    e = np.array([r["estoi"] for r in rows])
                    print(f"  {label:18s} PESQ {p.mean():.3f} ESTOI {e.mean():.4f}")
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
