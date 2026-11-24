#!/usr/bin/env python3
"""Blind public-sync acquisition + holdout scoring for Flux Whisper8's 32-tone RF fixture.

Read-only raw-complex .npy IQ capture from an SDR; no PTT, UART, SPI or radio
control. Not an Olivia decoder. Radio test has NOT yet been performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy import signal
from k6_whisper8 import (
    SAMPLE_RATE, SAMPLES_PER_SYMBOL, PREAMBLE, SYNC, FIXTURE_HOLDOUT,
    FFT_TONE_BINS, TONES_HZ,
)

SYNC_WORDS = PREAMBLE + SYNC
FIXTURE_SIZE = len(SYNC_WORDS) + len(FIXTURE_HOLDOUT)


def iq_to_discriminator_audio(
    iq: np.ndarray, *, sample_rate: int = 1024000, rf_offset_hz: int = -250000
) -> np.ndarray:
    """Downmix RF target, low-pass to 32k, differentiate FM, decimate to 8k.

    rf_offset_hz = desired TX carrier frequency minus IQ recording center.
    The FM discriminator removes residual constant carrier offset.
    """
    if sample_rate != 1024000:
        raise ValueError("v0 IQ scorer requires 1,024,000 complex samples/s")
    if not np.iscomplexobj(iq) or iq.ndim != 1:
        raise ValueError("IQ must be one-dimensional complex array")
    if iq.size < sample_rate // 2:
        raise ValueError("IQ capture shorter than 0.5s")
    if abs(rf_offset_hz) > sample_rate / 2 - 30000:
        raise ValueError("RF target too close to Nyquist")
    t = np.arange(iq.size, dtype=np.float64)
    # Fold the carrier offset modulo sample rate for numerical stability.
    mixed = iq * np.exp(-2j * np.pi * (rf_offset_hz / sample_rate) * t)
    narrowed = signal.resample_poly(mixed, 1, 32).astype(np.complex64)
    dphi = np.angle(narrowed[1:] * np.conj(narrowed[:-1]))
    discriminator_hz = dphi * (32000.0 / (2 * np.pi))
    # Only AC audio matters for detecting FM-audio tones.
    discriminator_hz -= np.median(discriminator_hz)
    return signal.resample_poly(discriminator_hz, 1, 4).astype(np.float64)


def score_fixture(audio: np.ndarray, *, phase_stride: int = 16) -> dict:
    """Search public preamble+sync only; reserved 8 holdout symbols decide PASS.

    Tries 16 sample-time phases and every possible symbol boundary. The holdout
    is never used to select sync, and no post-hoc shifting of payload bits.
    """
    if audio.ndim != 1 or len(audio) < FIXTURE_SIZE * SAMPLES_PER_SYMBOL:
        raise ValueError("not enough discriminator PCM for fixture")
    if phase_stride < 1 or SAMPLES_PER_SYMBOL % phase_stride:
        raise ValueError("invalid phase stride")
    win = np.hanning(SAMPLES_PER_SYMBOL)
    expected_sync = np.asarray(SYNC_WORDS, dtype=np.int64)
    expected_holdout = np.asarray(FIXTURE_HOLDOUT, dtype=np.int64)
    candidates = []
    for phase in range(0, SAMPLES_PER_SYMBOL, phase_stride):
        n = (audio.size - phase) // SAMPLES_PER_SYMBOL
        if n < FIXTURE_SIZE:
            continue
        rows = audio[phase : phase + n * SAMPLES_PER_SYMBOL].reshape(
            n, SAMPLES_PER_SYMBOL
        )
        powers = np.abs(np.fft.rfft(rows * win, axis=1))[:, FFT_TONE_BINS]
        ids = np.argmax(powers, axis=1)
        for at in range(n - FIXTURE_SIZE + 1):
            # Public pattern: no holdout information used for acquisition.
            sync_errors = int(np.count_nonzero(
                ids[at : at + len(SYNC_WORDS)] != expected_sync
            ))
            if sync_errors:
                continue
            end = at + len(SYNC_WORDS)
            received = ids[end : end + len(expected_holdout)]
            errors = int(np.count_nonzero(received != expected_holdout))
            holdout_powers = powers[end : end + len(expected_holdout)]
            ordered = np.sort(holdout_powers, axis=1)
            margin_ratio = (ordered[:, -1] + 1e-9) / (ordered[:, -2] + 1e-9)
            candidates.append({
                "start_sample": int(phase + at * SAMPLES_PER_SYMBOL),
                "public_sync_tones": len(SYNC_WORDS),
                "sync_errors": sync_errors,
                "holdout_tones": received.tolist(),
                "holdout_errors": errors,
                "min_peak_to_runnerup_ratio": float(np.min(margin_ratio)),
            })
    if not candidates:
        return {"verdict": "FAIL", "reason": "no exact public preamble+sync",
                "candidate_count": 0}
    # A scorer cannot select a different frame on the strength of known holdout
    # bytes. Choose the earliest public-sync acquisition in the capture.
    selected = min(candidates, key=lambda c: c["start_sample"])
    return {
        "verdict": "PASS" if selected["holdout_errors"] == 0 else "FAIL",
        "candidate_count": len(candidates),
        "expected_holdout": list(FIXTURE_HOLDOUT),
        "first_public_sync_candidate": selected,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq", type=Path, required=True, help="complex .npy IQ array")
    p.add_argument("--sample-rate", type=int, default=1024000)
    p.add_argument("--rf-offset-hz", type=int, default=-250000)
    args = p.parse_args()
    iq = np.load(args.iq, allow_pickle=False)
    audio = iq_to_discriminator_audio(
        iq, sample_rate=args.sample_rate, rf_offset_hz=args.rf_offset_hz
    )
    result = score_fixture(audio)
    result["iq_samples"] = int(iq.size)
    result["rf_offset_hz"] = args.rf_offset_hz
    result["baseband_sample_rate"] = SAMPLE_RATE
    result["symbol_hz"] = TONES_HZ
    result["hardware_state"] = "UNKNOWN; capture analysis only"
    print(json.dumps(result, indent=2))
    if result["verdict"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
