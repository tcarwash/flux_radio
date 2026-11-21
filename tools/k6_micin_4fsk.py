#!/usr/bin/env python3
"""Reproducible, low-level 4-FSK MICIN baseband waveform for an isolated RF bench.

Creates WAV/metadata only. Does not play audio, access the radio, or transmit.
The WAV levels represent a desired RF frequency control *voltage*, not audio
tones: this only works if the chip's TX analog path passes the signal cleanly.
A commodity AC-coupled soundcard can ruin the DC levels.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import wave

import numpy as np
from scipy.ndimage import gaussian_filter1d

LEVELS = np.array([-1.0, -1.0 / 3, 1.0 / 3, 1.0])
SYNC = np.array([0, 3, 0, 3, 1, 2, 1, 2] * 2, dtype=np.uint8)


def make_symbols(n, seed=17):
    if not (32 <= n <= 2400):
        raise ValueError("symbols must be between 32 and 2400")
    rng = np.random.default_rng(seed)
    return np.concatenate([SYNC, rng.integers(0, 4, n - len(SYNC),
                                             dtype=np.uint8)])


def make_shaped_4fsk(symbols, *, sample_rate=48000, symbol_baud=1200,
                     bt=0.5, amplitude=0.03, guard_ms=40):
    if not (1 <= symbol_baud <= 4800 and sample_rate >= 8 * symbol_baud):
        raise ValueError("invalid symbol baud or sample rate")
    if sample_rate % symbol_baud:
        raise ValueError("integer samples per symbol required")
    if not (0.15 <= bt <= 1.5 and 0.001 <= amplitude <= 0.1):
        raise ValueError("BT and level must be within conservative ranges")
    if not (0 <= guard_ms <= 200):
        raise ValueError("guard_ms out of range")
    symbols = np.asarray(symbols)
    if (symbols.ndim != 1 or len(symbols) < 1
            or not np.issubdtype(symbols.dtype, np.integer)
            or np.any(symbols < 0) or np.any(symbols > 3)):
        raise ValueError("need nonempty 4-FSK symbol indices (0..3)")
    sps = sample_rate // symbol_baud
    lead = round(guard_ms * sample_rate / 1000)
    raw = np.concatenate([np.zeros(lead), np.repeat(LEVELS[symbols], sps),
                          np.zeros(lead)])
    # sigma = sqrt(ln 2) / (2*pi*BT*Rs); continuous frequency-control shaping.
    sigma = sps * math.sqrt(math.log(2)) / (2 * math.pi * bt)
    shaped = gaussian_filter1d(raw, sigma=sigma, truncate=5, mode="constant")
    return np.asarray(shaped * amplitude, dtype=np.float64)


def calibration_tone(*, sample_rate=48000, amplitude=0.03):
    if not (0.001 <= amplitude <= 0.1):
        raise ValueError("unsafe test amplitude")
    silence = np.zeros(sample_rate // 10)
    t = np.arange(sample_rate // 4) / sample_rate
    # 5 ms fade avoids start/end clicks.
    fade = np.minimum(1.0, np.minimum(t, (t[-1] - t)) / 0.005)
    return np.r_[silence, amplitude * fade * np.sin(2*np.pi*1000*t), silence]


def write_wav(path, data, fs):
    samples = np.asarray(data, dtype=float)
    if samples.ndim != 1 or not np.all(np.isfinite(samples)) or np.max(np.abs(samples)) > 0.1:
        raise ValueError("invalid or excessive amplitude")
    out = np.round(samples * 32767).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(fs)
        f.writeframes(out.tobytes())


def run(out_dir, *, n_symbols=128, sample_rate=48000, symbol_baud=1200,
        bt=0.5, amplitude=0.03, seed=17):
    if n_symbols > 2 * symbol_baud:  # <=2 seconds of keyed payload
        raise ValueError("reference payload must be <=2 seconds")
    ids = make_symbols(n_symbols, seed)
    baseband = make_shaped_4fsk(ids, sample_rate=sample_rate,
                                symbol_baud=symbol_baud, bt=bt,
                                amplitude=amplitude)
    tone = calibration_tone(sample_rate=sample_rate, amplitude=amplitude)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_wav(out_dir / "micin-4fsk.wav", baseband, sample_rate)
    write_wav(out_dir / "micin-1khz.wav", tone, sample_rate)
    meta = {
        "offline_only": True,
        "transmitted": False,
        "signal_type": "signed baseband frequency-control waveform (not audio-over-FM tones)",
        "sample_rate": sample_rate, "symbol_baud": symbol_baud,
        "gaussian_bt": bt, "fullscale_amplitude": amplitude,
        "lead_and_tail_guard_ms": 40,
        "symbol_ids": ids.tolist(), "symbol_levels": LEVELS.tolist(),
        "sync_symbols": SYNC.tolist(),
        "expected_duration_s": float(len(baseband) / sample_rate),
        "waveform_mean": float(baseband.mean()),
        "waveform_rms": float(np.sqrt(np.mean(baseband**2))),
        "caution": ("WAV level is not MICIN millivolts. Use isolated calibrated "
                    "attenuation and verify DC coupling. No automatic playback or TX."),
    }
    (out_dir / "micin-4fsk.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--n-symbols", type=int, default=128)
    ap.add_argument("--sample-rate", type=int, default=48000)
    ap.add_argument("--symbol-baud", type=int, default=1200)
    ap.add_argument("--bt", type=float, default=0.5)
    ap.add_argument("--amplitude", type=float, default=0.03)
    ap.add_argument("--seed", type=int, default=17)
    args = ap.parse_args(argv)
    info = run(args.out_dir, n_symbols=args.n_symbols, sample_rate=args.sample_rate,
               symbol_baud=args.symbol_baud, bt=args.bt,
               amplitude=args.amplitude, seed=args.seed)
    print(json.dumps({k:v for k,v in info.items() if k not in
                      ("symbol_ids", "sync_symbols")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
