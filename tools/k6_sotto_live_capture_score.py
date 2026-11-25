#!/usr/bin/env python3
"""OFFLINE-only Sotto FDV1 physical IQ scorer; never opens SDR/serial or transmits.

Reads an already recorded numpy IQ file. An independent compiled ARM FSK
receiver recovers physical 66-byte bursts; strict FDV1 integrity/sequence
is checked before optional native Sotto PCM reconstruction.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.k6_fsk_emulator_replay import replay
from tools.k6_sotto_virtual_link import decode_session


def score_frames(frames: list[bytes]) -> tuple[bytes, dict]:
    if not frames or len(frames) > 144:
        raise ValueError("expected 1..144 bounded physical RF frames")
    if any(len(frame) != 66 for frame in frames):
        raise ValueError("each physical guarded FSK packet must be 66 bytes")
    codec, session = decode_session(frames)
    if not session["complete"] or session["invalid"] or not codec:
        raise ValueError(f"RF capture has no complete, unambiguous FDV1 session: {session}")
    if len(codec) % 6 or len(codec) > 144 * 8 * 6:
        raise ValueError("unexpected Sotto codec payload length")
    return codec, {
        "result": "PASS",
        "frames": len(frames),
        "codec_frames": len(codec) // 6,
        "session": session,
        "codec_sha256": hashlib.sha256(codec).hexdigest(),
        "physical_rf_capture_checked": True,
        "on_radio_decoder_checked": False,
        "live_audio_quality_qualified": False,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="npy:FILE complex-IQ or npy-u8:FILE interleaved RTL-SDR uint8")
    p.add_argument("--rate", type=float, required=True, help="IQ samples per second")
    p.add_argument("--center", type=float, required=True, help="calibrated/offset-corrected IQ center in Hz")
    p.add_argument("--tune", type=int, required=True, help="expected actual RF frequency in Hz")
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--json", type=Path, help="new path for hash-only report")
    p.add_argument("--wav", type=Path, help="new private path for decoded audio (never publish raw audio)")
    args = p.parse_args()
    if not args.source.startswith(("npy:", "npy-u8:")):
        p.error("requires an existing offline .npy IQ recording; live SDR prohibited")
    raw_path = Path(args.source.split(":", 1)[1])
    if not raw_path.is_file():
        p.error("recorded IQ file not found")
    if args.wav and args.wav.exists() or args.json and args.json.exists():
        p.error("refusing to overwrite existing output")
    frames, fsk = replay(args.source, rate=args.rate, center=args.center,
                         tune=args.tune, duration=args.duration,
                         frame_bytes=66, threshold=4, poll_us=500)
    codec, report = score_frames(frames)
    report.update(
        receiver=fsk,
        input_iq_sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        input_rate_hz=args.rate,
        input_center_hz=args.center,
        expected_rf_hz=args.tune,
        capture_duration_s=args.duration,
    )
    if args.wav:
        from tools.k6_sotto_e2e_mock import native_decoder, decode_pcm, write_wav
        with tempfile.TemporaryDirectory(prefix="flux-sotto-score-") as td:
            native = native_decoder(ROOT, Path(td) / "decoder.so")
            pcm = decode_pcm(native, codec)
            args.wav.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_wav(args.wav, pcm)
            report["pcm_sha256"] = hashlib.sha256(pcm.tobytes()).hexdigest()
            report["pcm_samples"] = int(len(pcm))
            report["wav_path"] = str(args.wav)
            report["audio_privacy"] = "local file only; not safe to publish without review"
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with args.json.open("x") as f:
            json.dump(report, f, indent=2)
            f.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
