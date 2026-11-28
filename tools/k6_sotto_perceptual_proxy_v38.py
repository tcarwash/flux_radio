#!/usr/bin/env python3
"""PESQ/ESTOI for playback filters on public Sotto v3.8 speech samples.

Builds the existing C decoder and repository Codec2 source in temporary
directories, decodes public no-channel bitstreams (and optionally paired
radio-channel bitstreams), scores the 8kHz core, and applies playback filters.
No repository build outputs are written; this is codec-output evidence, not
GP0/hardware evidence.
"""
from __future__ import annotations
import argparse
import io
import json
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
SAMPLE_ROOT = ROOT / "sotto/docs/samples/v38"
VOICE_IDS = ("2428_m", "251_m", "3081_f", "3536_f", "6313_f", "652_m", "777_m", "8842_f")
RADIO_CASES = (
    ("female_libri198_14s", "146_MHz_30_kmh_56_dB-Hz"),
    ("female_libri198_14s", "446_MHz_60_kmh_52_dB-Hz"),
    ("male_libri3436_15s", "146_MHz_30_kmh_56_dB-Hz"),
    ("male_libri3436_15s", "446_MHz_60_kmh_52_dB-Hz"),
)
sys.path.insert(0, str(ROOT))
pkg = sys.modules.get("tools")
if pkg is None or str(ROOT / "tools") not in getattr(pkg, "__path__", []):
    pkg = types.ModuleType("tools")
    pkg.__path__ = [str(ROOT / "tools")]
    sys.modules["tools"] = pkg
from tools.k6_sotto_fidelity_previews import FS, VARIANTS, normalize_for_audition, variants


def read_flac(path: Path, expected_rate: int) -> np.ndarray:
    proc = subprocess.run(["flac", "--decode", "--stdout", "--silent", str(path)],
                          capture_output=True, check=True)
    rate, data = wavfile.read(io.BytesIO(proc.stdout))
    if rate != expected_rate or data.dtype != np.int16 or data.ndim != 1:
        raise ValueError(f"expected mono signed16 {expected_rate}Hz FLAC: {path}")
    return data.astype(np.float64) / 32768.0


def build_decoder(tmp: Path) -> Path:
    source = ROOT / "sotto/c"
    target = tmp / "c"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.o", "sotto_enc", "sotto_dec", "libsotto.*"))
    codec2_source = ROOT / "sotto/codec/codec2"
    c2build = tmp / "codec2-build"
    configured = subprocess.run(["cmake", "-S", str(codec2_source), "-B", str(c2build),
                                 "-DCMAKE_BUILD_TYPE=Release", "-DUNITTEST=OFF"],
                                capture_output=True, text=True, timeout=90)
    if configured.returncode:
        raise RuntimeError(f"temporary Codec2 configure failed:\n{configured.stderr[-2000:]}")
    built = subprocess.run(["cmake", "--build", str(c2build), "--target", "codec2", "-j2"],
                           capture_output=True, text=True, timeout=180)
    if built.returncode:
        raise RuntimeError(f"temporary Codec2 build failed:\n{built.stderr[-2500:]}")
    c2libdir = c2build / "src"
    if not (c2libdir / "libcodec2.so").is_file():
        raise RuntimeError("expected temporary libcodec2.so was not produced")
    p = subprocess.run(["make", "-C", str(target), "-j2", "sotto_dec",
                        f"CODEC2_SRC={codec2_source / 'src'}",
                        f"CODEC2_LIB={c2libdir}"],
                       capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError(f"temporary Sotto decoder build failed:\n{p.stderr[-3000:]}")
    return target / "sotto_dec"


def main() -> None:
    # Only the report-generation path needs objective-metric packages; keep
    # decoding/audio helpers importable by ASR-only offline experiments.
    from pesq import pesq
    from pystoi import stoi
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--include-radio", action="store_true",
                    help="also score the four channel-decoded bitstreams with paired clean references and .good masks")
    args = ap.parse_args()
    if args.json_out.exists():
        ap.error("refuse overwrite")
    codec2_revision = subprocess.run(["git", "-C", str(ROOT / "sotto/codec/codec2"),
                                      "rev-parse", "HEAD"], text=True, capture_output=True,
                                     check=True).stdout.strip()
    report = {
        "schema": 1,
        "offline_only": True,
        "codec": "Sotto v3.8, existing C decoder and repository Codec2 source compiled in temporary directories",
        "codec2_source_revision": codec2_revision,
        "scores": "PESQ narrowband and ESTOI, 8kHz speech core",
        "alignment": "C -16 decoder output is aligned with input; 16kHz clean/decode audio downsampled to 8kHz as in render_page_v38.py",
        "conditions": {},
        "limitations": [
            "Public Sotto v3.8 codec outputs, including eight held-out no-channel voices and optionally four paired channel-decoded clips; not GP0/K6 recordings.",
            "PESQ/ESTOI compare codec speech with clean source and may disagree with human listener preference.",
            "Filters are downstream playback treatments; they cannot establish GP0 input fidelity.",
            "Each voice is a single clip; no across-session or population confidence intervals.",
            "The 4-8kHz v3.8 hiss band is excluded by the repository's 8kHz narrowband scoring path.",
        ],
    }
    with tempfile.TemporaryDirectory(prefix="sotto-v38-perceptual-") as td:
        decoder = build_decoder(Path(td))
        cases = [(f"librispeech_{voice}", "No_radio_channel", None)
                 for voice in VOICE_IDS]
        if args.include_radio:
            cases += [(stem, condition, f"{stem}__{condition}.good")
                      for stem, condition in RADIO_CASES]
        for stem, condition, good_name in cases:
            original16 = read_flac(SAMPLE_ROOT / f"{stem}__original16.flac", 16000)
            bits = SAMPLE_ROOT / f"{stem}__{condition}.bit"
            if not bits.is_file():
                raise FileNotFoundError(bits)
            if good_name:
                good_path = SAMPLE_ROOT / good_name
                frame_count = bits.stat().st_size // 6
                if bits.stat().st_size % 6 or good_path.stat().st_size != frame_count:
                    raise ValueError(f"bitstream/good-mask frame count mismatch: {bits}")
                good = np.fromfile(good_path, dtype=np.uint8)
                good_count = int(np.count_nonzero(good))
            else:
                good_path = None
                frame_count = None
                good_count = None
            with tempfile.TemporaryDirectory(prefix="sotto-v38-decode-") as rawdir:
                decoded_path = Path(rawdir) / "decoded16.raw"
                command = [str(decoder), "-16", str(bits), str(decoded_path)]
                if good_path:
                    command.append(str(good_path))
                subprocess.run(command,
                               check=True, capture_output=True, timeout=30)
                decoded16 = np.fromfile(decoded_path, dtype="<i2").astype(np.float64) / 32768.0
            reference = resample_poly(original16, 1, 2)
            decoded = resample_poly(decoded16, 1, 2)
            n = min(len(reference), len(decoded))
            reference, decoded = reference[:n], decoded[:n]
            target_rms = float(np.std(decoded))
            row = {"reference_file": f"{stem}__original16.flac",
                   "bitstream_file": bits.name,
                   "condition": condition,
                   "good_frame_count": good_count,
                   "frame_count": frame_count,
                   "duration_s": round(n / FS, 3), "variants": {}}
            for name, candidate in variants(decoded).items():
                matched, gain = normalize_for_audition(candidate, target_rms)
                estimate = matched.astype(np.float64) / 32768.0
                row["variants"][name] = {
                    "pesq_nb": round(float(pesq(FS, reference, estimate, "nb")), 4),
                    "estoi": round(float(stoi(reference, estimate, FS, extended=True)), 5),
                    "level_match_gain_db": round(float(20 * np.log10(max(gain, 1e-12))), 3),
                    "clipped_samples": int(np.count_nonzero(np.abs(matched.astype(np.int32)) >= 32767)),
                }
            condition_key = f"{stem}__{condition}"
            report["conditions"][condition_key] = row
            base = row["variants"]["original"]
            print(f"{condition_key}: base PESQ {base['pesq_nb']:.3f} ESTOI {base['estoi']:.4f}")
            for name, score in row["variants"].items():
                print(f"  {name:9s} PESQ {score['pesq_nb']:.3f} "
                      f"({score['pesq_nb']-base['pesq_nb']:+.3f}) ESTOI {score['estoi']:.4f} "
                      f"({score['estoi']-base['estoi']:+.4f})")
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    with args.json_out.open("x") as f:
        json.dump(report, f, indent=2, sort_keys=True)
        f.write("\n")


if __name__ == "__main__":
    main()
