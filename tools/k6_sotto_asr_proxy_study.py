#!/usr/bin/env python3
"""Offline ASR proxy for Sotto playback-filter speech readability.

Runs a locally cached Whisper model on public clean speech and paired
no-channel Sotto v3.5 decodes, then measures the preview filters' word
recognition relative to the same model's clean-source transcript. This is a
within-model proxy on public codec samples, not a GP0 capture or human
intelligibility result. The model is required locally; no download is allowed.
"""
from __future__ import annotations
import argparse
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import types

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pkg = sys.modules.get("tools")
if pkg is None or str(ROOT / "tools") not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules["tools"] = pkg
from tools.k6_sotto_fidelity_previews import FS, VARIANTS, normalize_for_audition, variants

SAMPLE_ROOT = ROOT / "sotto/docs/samples"
PAIRS = {
    "female_librispeech_198": (
        "female_libri198_14s__original.flac",
        "encoder/female_libri198_14s__No_radio_channel__sotto_v35.flac",
    ),
    "male_librispeech_3436": (
        "male_libri3436_15s__original.flac",
        "encoder/male_libri3436_15s__No_radio_channel__sotto_v35.flac",
    ),
    "male_ve9qrp": (
        "male_ve9qrp_15s__original.flac",
        "encoder/male_ve9qrp_15s__No_radio_channel__sotto_v35.flac",
    ),
}


def read_flac(path: Path) -> np.ndarray:
    proc = subprocess.run(["flac", "--decode", "--stdout", "--silent", str(path)],
                          capture_output=True, check=True)
    rate, data = wavfile.read(io.BytesIO(proc.stdout))
    if rate != FS or data.dtype != np.int16 or data.ndim != 1:
        raise ValueError(f"expected mono signed16 {FS}Hz FLAC: {path}")
    return data.astype(np.float32) / 32768.0


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z0-9]+)?", text.lower())


def word_error_rate(reference: list[str], candidate: list[str]) -> float:
    prev = list(range(len(candidate) + 1))
    for i, ref_word in enumerate(reference, 1):
        row = [i]
        for j, hyp_word in enumerate(candidate, 1):
            row.append(min(row[-1] + 1, prev[j] + 1,
                           prev[j - 1] + (ref_word != hyp_word)))
        prev = row
    return prev[-1] / max(len(reference), 1)


def transcribe(model, audio_8k: np.ndarray, beam_size: int) -> list[str]:
    audio_16k = resample_poly(audio_8k.astype(np.float32), 2, 1).astype(np.float32)
    audio_16k = np.clip(audio_16k, -.999, .999)
    segments, _ = model.transcribe(audio_16k, language="en", beam_size=beam_size,
                                   condition_on_previous_text=False)
    return words(" ".join(segment.text for segment in segments))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-path", type=Path, required=True,
                    help="local faster-whisper CTranslate2 model directory")
    ap.add_argument("--beam-size", type=int, choices=(1, 5), default=5)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite existing report")
    if not args.model_path.is_dir() or not (args.model_path / "model.bin").is_file():
        ap.error("model path must exist locally and contain model.bin; downloads are disabled")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        ap.error(f"faster-whisper runtime is not importable in this Python: {exc}")
    model = WhisperModel(str(args.model_path), device="cpu", compute_type="int8",
                         local_files_only=True, cpu_threads=4)
    report = {
        "schema": 1,
        "offline_only": True,
        "asr_model": f"faster-whisper base.en, local CTranslate2 model, CPU int8, beam {args.beam_size}",
        "reference_method": "clean-source transcript from the same ASR model (pseudo-reference; not a human-verified LibriSpeech transcript)",
        "filter_source": "existing playback-only preview filters applied to public no-channel Sotto v3.5 decoded samples",
        "sample_rate_input_hz": FS,
        "level_matching": "preview variants RMS-matched to their unfiltered decoded sample before ASR",
        "conditions": {},
        "limitations": [
            "ASR word error rate is only a comparative proxy; it is not human intelligibility or a speech-quality metric.",
            "The clean transcript is model-generated, so ASR mistakes in the pseudo-reference affect every comparison.",
            "These are public Sotto v3.5 no-channel decodes, not GP0 captures or Flux/K6 recordings.",
            "Playback filters are downstream of the codec and cannot establish GP0 sampling or decimation fidelity.",
            "Only three speakers, one codec condition and one recognizer/model configuration are included.",
        ],
    }
    for speaker, (reference_name, decoded_name) in PAIRS.items():
        clean = read_flac(SAMPLE_ROOT / reference_name)
        decoded = read_flac(SAMPLE_ROOT / decoded_name)
        n = min(len(clean), max(0, len(decoded) - 160))
        clean = clean[:n]
        decoded = decoded[160:160 + n]
        clean_words = transcribe(model, clean, args.beam_size)
        clean_rms = float(np.std(decoded))
        condition = {
            "reference_file": reference_name,
            "decoded_file": decoded_name,
            "duration_s": round(n / FS, 3),
            "clean_asr_reference_word_count": len(clean_words),
            "variants": {},
        }
        for name, samples in variants(decoded.astype(np.float64)).items():
            level_matched, gain = normalize_for_audition(samples, clean_rms)
            candidate_words = transcribe(model, level_matched.astype(np.float32) / 32768.0,
                                         args.beam_size)
            condition["variants"][name] = {
                "word_count": len(candidate_words),
                "word_error_rate_vs_clean_asr": round(word_error_rate(clean_words, candidate_words), 4),
                "level_match_gain_db": round(20 * np.log10(max(gain, 1e-12)), 3),
                "clipped_samples": int(np.count_nonzero(np.abs(level_matched.astype(np.int32)) >= 32767)),
            }
        report["conditions"][speaker] = condition
        base = condition["variants"]["original"]["word_error_rate_vs_clean_asr"]
        print(f"{speaker}: decoded baseline WER {base:.3f}")
        for name, score in condition["variants"].items():
            print(f"  {name:9s} WER {score['word_error_rate_vs_clean_asr']:.3f} "
                  f"delta-vs-decoded {score['word_error_rate_vs_clean_asr'] - base:+.3f}")
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
