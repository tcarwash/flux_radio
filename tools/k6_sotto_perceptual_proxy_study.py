#!/usr/bin/env python3
"""PESQ/ESTOI proxy for playback treatments on public Sotto speech samples.

Uses the repo's PESQ/ESTOI-compatible clean speech pairs and fixed 160-sample
codec delay, then measures the same level-matched preview variants as the
blind listening set. This is public no-channel Sotto-v3.5 evidence, not a
GP0 capture, hardware result, or human readability verdict.
"""
from __future__ import annotations
import argparse
import io
import json
from pathlib import Path
import subprocess
import sys
import types

import numpy as np
from scipy.io import wavfile
from pesq import pesq
from pystoi import stoi

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pkg = sys.modules.get("tools")
if pkg is None or str(ROOT / "tools") not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules["tools"] = pkg
from tools.k6_sotto_fidelity_previews import FS, VARIANTS, normalize_for_audition, variants

SAMPLE_ROOT = ROOT / "sotto/docs/samples"
DELAY_SAMPLES = 160  # Same fixed Codec2/Sotto delay used by sotto/sim/audio_race.py.
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


def load_flac(path: Path) -> np.ndarray:
    proc = subprocess.run(["flac", "--decode", "--stdout", "--silent", str(path)],
                          capture_output=True, check=True)
    rate, data = wavfile.read(io.BytesIO(proc.stdout))
    if rate != FS or data.dtype != np.int16 or data.ndim != 1:
        raise ValueError(f"expected mono signed16 {FS}Hz FLAC: {path}")
    return data.astype(np.float64) / 32768.0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite")
    report = {
        "schema": 1,
        "offline_only": True,
        "metrics": "PESQ narrowband and extended STOI at 8kHz",
        "codec_delay_alignment_samples": DELAY_SAMPLES,
        "variant_level_matching": "Each filtered decoded output is matched to its own unfiltered decoded RMS using the existing preview limiter.",
        "reference": "Original clean public speech sample; paired no-channel Sotto v3.5 decode",
        "conditions": {},
        "limitations": [
            "These are codec-output playback filters on public no-channel Sotto v3.5 samples, not GP0/K6 recordings.",
            "PESQ/STOI are objective quality/readability proxies and can disagree with human preference.",
            "Only three voices and one codec/no-channel condition are included; there are no confidence intervals.",
            "Filtered playback variants are downstream treatments and cannot establish GP0 sampling or decimation fidelity.",
        ],
    }
    for speaker, (ref_name, dec_name) in PAIRS.items():
        reference = load_flac(SAMPLE_ROOT / ref_name)
        decoded = load_flac(SAMPLE_ROOT / dec_name)
        n = min(len(reference), max(0, len(decoded) - DELAY_SAMPLES))
        reference = reference[:n]
        decoded = decoded[DELAY_SAMPLES:DELAY_SAMPLES + n]
        target_rms = float(np.std(decoded))
        row = {
            "reference_file": ref_name,
            "decoded_file": dec_name,
            "duration_s": round(n / FS, 3),
            "variants": {},
        }
        for name, candidate in variants(decoded).items():
            matched, gain = normalize_for_audition(candidate, target_rms)
            estimate = matched.astype(np.float64) / 32768.0
            if np.max(np.abs(reference)) > 1 or np.max(np.abs(estimate)) > 1:
                raise ValueError("PESQ/STOI inputs must be within full scale")
            row["variants"][name] = {
                "pesq_nb": round(float(pesq(FS, reference, estimate, "nb")), 4),
                "estoi": round(float(stoi(reference, estimate, FS, extended=True)), 5),
                "level_match_gain_db": round(float(20 * np.log10(max(gain, 1e-12))), 3),
                "clipped_samples": int(np.count_nonzero(np.abs(matched.astype(np.int32)) >= 32767)),
            }
        report["conditions"][speaker] = row
        base = row["variants"]["original"]
        print(f"{speaker}: baseline PESQ {base['pesq_nb']:.3f} ESTOI {base['estoi']:.4f}")
        for name, m in row["variants"].items():
            print(f"  {name:9s} PESQ {m['pesq_nb']:.3f} ({m['pesq_nb']-base['pesq_nb']:+.3f}) "
                  f"ESTOI {m['estoi']:.4f} ({m['estoi']-base['estoi']:+.4f})")
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
