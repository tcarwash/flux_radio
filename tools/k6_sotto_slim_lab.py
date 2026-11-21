#!/usr/bin/env python3
"""Offline Flux MICVOICE WAV -> Sotto 1200 slim frames (host validation only).

The same slim codec is intended for eventual on-device ARM use, but THIS
tool invokes the pinned upstream host binary. It never opens a serial port.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SOTTO = ROOT / "sotto"
SLIM = SOTTO / "c/fixed/sotto_slim_enc"
DECODER = SOTTO / "c/fixed/sotto_dec_fx"
INPUT_RATE = 8000
SUPERFRAME_BYTES = 6
SUPERFRAME_MS = 40
CODEC_BPS = 1200


def read_mono_pcm16(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getcomptype() != "NONE":
            raise ValueError("input must be uncompressed mono 16-bit PCM WAV")
        fs = w.getframerate()
        if fs < 6000 or fs > 48000:
            raise ValueError("unsupported input sample rate")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").copy(), fs


def resample_to_sotto(pcm: np.ndarray, fs: int) -> np.ndarray:
    if fs == INPUT_RATE:
        return np.asarray(pcm, dtype="<i2")
    try:
        from scipy.signal import resample_poly
    except ImportError as e:
        raise RuntimeError("scipy required to resample PA11 sample rates to 8 kHz") from e
    divisor = math.gcd(fs, INPUT_RATE)
    out = resample_poly(pcm.astype(np.float64), INPUT_RATE // divisor, fs // divisor)
    return np.clip(np.round(out), -32768, 32767).astype("<i2")


def encode_wav(wav_path: Path, out_prefix: Path, *, slim: Path = SLIM,
               decoder: Path | None = None) -> dict:
    """Produce ordinary Sotto 1200 superframes; no RF framing or transmission."""
    samples, source_fs = read_mono_pcm16(wav_path)
    if samples.size < 80:
        raise ValueError("need at least 10 ms of input")
    if not slim.is_file():
        raise FileNotFoundError(f"build encoder first: make -C {slim.parents[1]} fixed/sotto_slim_enc")
    pcm = resample_to_sotto(samples, source_fs)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    bit_path = out_prefix.with_suffix(".sotto")
    with tempfile.TemporaryDirectory(prefix="flux-sotto-") as td:
        raw_path = Path(td) / "input.raw"
        raw_path.write_bytes(pcm.tobytes())
        subprocess.run([str(slim), str(raw_path), str(bit_path)], check=True)
    bitstream = bit_path.read_bytes()
    if not bitstream or len(bitstream) % SUPERFRAME_BYTES:
        raise ValueError("slim encoder produced invalid superframe count")
    frames = len(bitstream) // SUPERFRAME_BYTES
    audio_sec = len(pcm) / INPUT_RATE
    # 600 symbols/s * 3 raw bits per eight-tone symbol; overhead not included.
    gross = 600 * 3
    manifest = {
        "codec": "Sotto 1200 slim", "codec_bitrate_bps": CODEC_BPS,
        "source_rate_hz": source_fs, "encoder_rate_hz": INPUT_RATE,
        "source_samples": len(samples), "encoder_samples": len(pcm),
        "audio_seconds": audio_sec,
        "superframe_ms": SUPERFRAME_MS, "superframe_bytes": SUPERFRAME_BYTES,
        "superframes": frames, "encoded_bytes": len(bitstream),
        "encoded_sha256": hashlib.sha256(bitstream).hexdigest(),
        "modem_gross_bps_600baud_8fsk": gross,
        "codec_fraction_of_raw_channel": CODEC_BPS / gross,
        "available_bps_for_all_fec_and_framing_before_coding": gross - CODEC_BPS,
        "remaining_bps_after_rate_half_fec": gross // 2 - CODEC_BPS,
        "realtime_possible_at_rate_half_fec": gross // 2 >= CODEC_BPS,
        "physical_rf_test": False, "mcu_codec_integrated": False,
    }
    if decoder is not None:
        if not decoder.is_file():
            raise FileNotFoundError(f"build decoder first: make -C {decoder.parents[1]} fixed/sotto_dec_fx")
        with tempfile.TemporaryDirectory(prefix="flux-sotto-decode-") as td:
            decoded_raw = Path(td) / "decoded.raw"
            subprocess.run([str(decoder), "-1200", str(bit_path), str(decoded_raw)], check=True)
            restored = np.frombuffer(decoded_raw.read_bytes(), dtype="<i2")
        dec_path = out_prefix.with_suffix(".decoded.wav")
        with wave.open(str(dec_path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(INPUT_RATE)
            w.writeframes(restored.tobytes())
        manifest["decoded_samples"] = int(restored.size)
    out_prefix.with_suffix(".json").write_text(json.dumps(manifest, indent=2) + "\n",
                                                encoding="utf-8")
    return manifest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("wav", type=Path, help="mono 16-bit PCM WAV, including k6_micvoice.py output")
    p.add_argument("--out", type=Path, required=True, help="output prefix for .sotto, .json")
    p.add_argument("--decode", action="store_true", help="also decode to .decoded.wav")
    args = p.parse_args()
    manifest = encode_wav(args.wav, args.out, decoder=DECODER if args.decode else None)
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
