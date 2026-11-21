#!/usr/bin/env python3
"""Reference-assisted Bell 202 demodulation for Flux experimental audio capture.

Arbitrary measured sample rate; noncoherent 1200/2200 Hz matched tone energies;
short known synchronization word determines starting sample and carrier offset.
Outputs signed soft metrics, NOT calibrated LLRs or CRC-verified AX.25 packets.
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

BAUD = 1200.0
MARK_HZ = 1200.0
SPACE_HZ = 2200.0


def _integrated_tones(samples: np.ndarray, fs: float, freq_offset: float):
    indices = np.arange(samples.size, dtype=np.float64)
    result = []
    for freq in (MARK_HZ + freq_offset, SPACE_HZ + freq_offset):
        wave = np.exp((-2j * np.pi * freq / fs) * indices)
        result.append(np.r_[0j, np.cumsum(samples * wave)])
    return result


def _scores(prefixes, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    mark, space = prefixes
    em = np.abs(mark[ends] - mark[starts]) ** 2
    es = np.abs(space[ends] - space[starts]) ** 2
    return (em - es) / np.maximum(em + es, 1.0e-15)


def decode_known_reference(
    samples,
    sample_rate_hz: float,
    known_bits,
    *,
    sync_len: int = 48,
    max_start: int = 140,
    offset_hz_candidates=(-100.0, -50.0, 0.0, 50.0, 100.0),
    min_sync_score: float = 0.65,
) -> dict:
    """Find tone/symbol alignment using known bits, then return per-symbol soft scores.

    This is a training-sequence experiment. It is NOT blind discovery of a
    packet or HDLC frame, and does not use the data bits to choose alignment.
    """
    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    bits = np.asarray(known_bits, dtype=np.int8).reshape(-1)
    fs = float(sample_rate_hz)
    if fs < 6000 or fs > 192000 or x.size < 300:
        raise ValueError("invalid sample rate or too few samples")
    if bits.size < 24 or not np.all((bits == 0) | (bits == 1)):
        raise ValueError("require at least 24 known 0/1 symbols")
    if not 24 <= sync_len <= bits.size or not 0 <= max_start <= 20000:
        raise ValueError("invalid sync/search length")
    if not offset_hz_candidates or any(abs(float(o)) > 500 for o in offset_hz_candidates):
        raise ValueError("invalid tone-offset candidates")
    sps = fs / BAUD
    indices = np.arange(bits.size + 1) * sps
    signs = (2.0 * bits[:sync_len].astype(float)) - 1.0
    best = None
    for offset in offset_hz_candidates:
        prefixes = _integrated_tones(x, fs, float(offset))
        for start in range(max_start + 1):
            boundaries = np.rint(indices + start).astype(np.int64)
            if boundaries[-1] > len(x):
                break
            scores = _scores(prefixes, boundaries[:-1], boundaries[1:])
            corr = float(np.mean(signs * scores[:sync_len]))
            if best is None or corr > best[0]:
                best = (corr, start, float(offset), scores)
    if best is None or best[0] < min_sync_score:
        raise ValueError("no adequately correlated known synchronization word")
    corr, start, offset, soft = best
    decided = (soft >= 0).astype(np.uint8)
    errors = int(np.count_nonzero(decided != bits))
    return {
        "sync_score": corr,
        "symbol_start_sample": start,
        "tone_offset_hz": offset,
        "measured_sample_rate_hz": fs,
        "n_symbols": int(bits.size),
        "hard_bits": decided.tolist(),
        "soft_scores": soft.tolist(),
        "errors": errors,
        "ber": errors / int(bits.size),
        "payload_errors": int(np.count_nonzero(decided[sync_len:] != bits[sync_len:])),
    }


def read_wav(path: Path):
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("requires mono 16-bit PCM WAV")
        fs = w.getframerate()
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0, fs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--meta", required=True, type=Path, help="known synthetic transmission metadata (bits)")
    parser.add_argument("--sync-len", type=int, default=48)
    parser.add_argument("--max-start", type=int, default=140)
    a = parser.parse_args()
    samples, fs = read_wav(a.wav)
    meta = json.loads(a.meta.read_text(encoding="utf-8"))
    result = decode_known_reference(
        samples, fs, meta["bits"], sync_len=a.sync_len, max_start=a.max_start,
    )
    print(json.dumps({k: v for k, v in result.items() if k not in ("soft_scores", "hard_bits")}, indent=2))


if __name__ == "__main__":
    main()
