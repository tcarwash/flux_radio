#!/usr/bin/env python3
"""Build a blinded, level-matched listening set for causal GP0 mask variants.

The audio is synthetic RFSim GP0 source noise passed through real Sotto v3.8
codec executables. This pack is for human comparison of modeled candidates,
not a substitute for capturing spoken audio from the K6.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from k6_gp0_causal_denoise_sotto_v38 import (
    METHODS, PREAMBLE_SEED_OFFSET, causal_denoise,
)
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, VOICE_IDS, build_codec, encode_decode, read_flac, resample_poly,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm
from k6_sotto_fidelity_previews import normalize_for_audition

VPP = 0.1
NOISE_SEED = 83
TREATMENTS = ("clean_codec", "noisy_codec", *METHODS)
TUNED_TREATMENTS = (
    "clean_codec", "noisy_codec", "wiener/scale=1/floor=0.02",
    "wiener/scale=2/floor=0.02", "wiener/scale=2/floor=0.05",
    "oversub_1p5",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--tuned-wiener", action="store_true",
                        help="compare the new scale/floor candidates with clean/noisy and oversubtraction")
    parser.add_argument("--wiener-settings", nargs="+", metavar="SCALE/FLOOR",
                        help="Wiener scale/floor pairs for --tuned-wiener, e.g. 2/0.05 2.5/0.03")
    parser.add_argument("--asymmetric-settings", nargs="+", metavar="SCALE/FLOOR/OPEN/CLOSE",
                        help="causal asymmetric Wiener settings for --tuned-wiener, e.g. 2.5/0.03/0.1/0.85")
    args = parser.parse_args()
    if (args.wiener_settings or args.asymmetric_settings) and not args.tuned_wiener:
        parser.error("custom Wiener settings require --tuned-wiener")
    wiener_settings = []
    for item in args.wiener_settings or ("1/0.02", "2/0.02", "2/0.05"):
        try:
            scale_text, floor_text = item.split("/", 1)
            scale, floor = float(scale_text), float(floor_text)
        except ValueError:
            parser.error(f"invalid Wiener setting {item!r}; expected SCALE/FLOOR")
        if not np.isfinite(scale) or scale <= 0 or not np.isfinite(floor) or not 0 <= floor <= 1:
            parser.error(f"invalid Wiener setting {item!r}; scale must be positive and floor in [0, 1]")
        wiener_settings.append((scale, floor))
    if len(set(wiener_settings)) != len(wiener_settings):
        parser.error("duplicate Wiener settings are not allowed")
    asymmetric_settings = []
    for item in args.asymmetric_settings or ():
        try:
            scale_text, floor_text, open_text, close_text = item.split("/", 3)
            scale, floor, open_previous, close_previous = map(
                float, (scale_text, floor_text, open_text, close_text))
        except ValueError:
            parser.error(f"invalid asymmetric setting {item!r}; expected SCALE/FLOOR/OPEN/CLOSE")
        if (not np.isfinite(scale) or scale <= 0 or not np.isfinite(floor)
                or not 0 <= floor <= 1 or not 0 <= open_previous < 1
                or not 0 <= close_previous < 1):
            parser.error(f"invalid asymmetric setting {item!r}; weights must be in [0, 1)")
        asymmetric_settings.append((scale, floor, open_previous, close_previous))
    if len(set(asymmetric_settings)) != len(asymmetric_settings):
        parser.error("duplicate asymmetric settings are not allowed")
    tuned_filter_settings = [
        (f"wiener/scale={scale:g}/floor={floor:g}", "wiener", scale, floor, {})
        for scale, floor in wiener_settings
    ]
    tuned_filter_settings += [
        (f"wiener_asymmetric/scale={scale:g}/floor={floor:g}/open_previous={opening:g}/close_previous={closing:g}",
         "wiener_smoothed", scale, floor,
         {"time_open_previous": opening, "time_close_previous": closing})
        for scale, floor, opening, closing in asymmetric_settings
    ]
    tuned_treatments = ("clean_codec", "noisy_codec",
                        *(item[0] for item in tuned_filter_settings), "oversub_1p5")
    if len(set(tuned_treatments)) != len(tuned_treatments):
        parser.error("custom settings produce duplicate treatment names")
    treatments = tuned_treatments if args.tuned_wiener else TREATMENTS
    root = args.out_dir
    if root.exists():
        parser.error("output directory already exists; choose a new directory")
    blind = root / "blind"
    blind.mkdir(parents=True)
    storage_key = f"gp0-blind-listening-causal-{secrets.token_hex(12)}"
    key = {"offline_only": True, "source_level_vpp": VPP, "noise_seed": NOISE_SEED,
           "parameter_sweep_pack": args.tuned_wiener,
           "ui_storage_key": storage_key, "treatments": {},
           "level_match": "each rendered sample RMS-matched to same voice clean-codec output; peak clipping prevented"}
    instructions = """# Blind listening comparison

All files are 8kHz mono, 16-bit PCM. Compare only clips sharing a `speakerNN`
prefix. Each set is independently randomized; the same letter does not mean
the same processing across speakers. Files are RMS level-matched to the
noise-free Sotto-codec rendition for that voice, with clipping prevented.

Open `blind/index.html` for an in-browser player and score sheet. It never
loads the answer key. You can also play the WAV files directly.

For each speaker, rank the six clips for (1) words understood, (2) effort to
understand, and (3) naturalness. Also note any harshness, pumping, or musical
noise. A tie is useful. The set contains a clean-codec anchor and modeled GP0
noise/mask conditions, but labels are hidden. Do not treat results as a
physical K6 speech capture: the source impairment is statistical, the input
reference is the 0.1Vpp laboratory convention, and there is no RF channel.
"""
    with tempfile.TemporaryDirectory(prefix="gp0-causal-listenpack-v38-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        rng = secrets.SystemRandom()
        for speaker_index, voice in enumerate(VOICE_IDS, 1):
            source_path = SAMPLE_ROOT / f"librispeech_{voice}__original16.flac"
            original = resample_poly(read_flac(source_path, 16000), 1, 2)
            normalized = original / float(np.max(np.abs(original)))
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp,
                                          f"{voice}-clean")
            target_rms = float(np.std(clean_decoded))
            noisy = k6_gp0_pcm(normalized, input_vpp=VPP,
                               noise_rms_fs=OFF_PCM_RMS_FS, seed=NOISE_SEED,
                               response="measured", noise_model="banded", clip=True)
            preamble = generate_gp0_noise(
                FS // 2, seed=NOISE_SEED + PREAMBLE_SEED_OFFSET,
                model="banded", rms_fs=OFF_PCM_RMS_FS)
            decoded = {
                "clean_codec": clean_decoded,
                "noisy_codec": encode_decode(encoder, decoder, noisy, temp,
                                              f"{voice}-noisy"),
            }
            if args.tuned_wiener:
                settings = [*tuned_filter_settings,
                            ("oversub_1p5", "oversub_1p5", 1.0, 0.05, {})]
                for name, method, scale, floor, extra_kwargs in settings:
                    filtered = causal_denoise(noisy, preamble, method,
                                              noise_scale=scale, mask_floor=floor,
                                              **extra_kwargs)
                    decoded[name] = encode_decode(encoder, decoder, filtered, temp,
                                                  f"{voice}-{name.replace('/', '-')}")
            else:
                for method in METHODS:
                    filtered = causal_denoise(noisy, preamble, method)
                    decoded[method] = encode_decode(encoder, decoder, filtered, temp,
                                                    f"{voice}-{method}")
            labels = ["A", "B", "C", "D", "E", "F"]
            rng.shuffle(labels)
            speaker_key = {}
            for treatment, label in zip(treatments, labels):
                matched, gain = normalize_for_audition(decoded[treatment], target_rms)
                filename = f"speaker{speaker_index:02d}_{label}.wav"
                path = blind / filename
                wavfile.write(path, FS, matched)
                speaker_key[filename] = {
                    "treatment": treatment,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "samples": len(matched),
                    "duration_s": round(len(matched) / FS, 3),
                    "rms_gain_db": round(20 * np.log10(max(gain, 1e-12)), 3),
                    "clipped_samples": int(np.count_nonzero(np.abs(matched.astype(np.int32)) >= 32767)),
                }
            key["treatments"][f"speaker{speaker_index:02d}"] = speaker_key
            print(f"speaker{speaker_index:02d}: wrote 6 blinded clips", flush=True)
    (root / "instructions.md").write_text(instructions)
    (root / "answer-key.json").write_text(json.dumps(key, indent=2, sort_keys=True) + "\n")
    template = Path(__file__).resolve().parent / "templates" / "k6_gp0_causal_listenpack.html"
    html = template.read_text()
    if args.tuned_wiener:
        html = html.replace("Blind GP0 audio comparison", "Blind GP0 Wiener tuning comparison")
    html = html.replace("gp0-blind-listening-causal-v1", storage_key)
    (blind / "index.html").write_text(html)
    print(f"blind listening set: {blind}")


if __name__ == "__main__":
    main()
