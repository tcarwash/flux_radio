#!/usr/bin/env python3
"""Offline narrowband-FM demodulation for retained K6 RTL-SDR CF32 captures.

This is a receive-only analysis helper: it reads an existing IQ file and emits
8 kHz mono audio plus basic level/CFO diagnostics. It never opens SDR hardware,
serial ports, or radio controls.
"""
from __future__ import annotations

import argparse
import json
import math
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, resample_poly, sosfilt


def demod(path: Path, *, fs: int, offset: float, out: Path,
          audio_rate: int, low: float, high: float) -> dict:
    if path.stat().st_size % 8:
        raise ValueError("CF32 file length is not complex-sample aligned")
    count = path.stat().st_size // 8
    iq = np.memmap(path, dtype="<c8", mode="r", shape=(count,))

    # Estimate the carrier offset with a coherent adjacent-sample correlation.
    # The median of instantaneous phase steps is unsuitable for FM: speech
    # deviation can occupy most of the sample distribution. Accumulate in
    # chunks to keep memory bounded.
    corr = 0j
    chunk = 1_000_000
    previous = None
    for start in range(0, count, chunk):
        end = min(count, start + chunk)
        block = np.asarray(iq[start:end], dtype=np.complex64)
        if previous is not None:
            corr += complex(block[0] * np.conj(previous))
        if len(block) > 1:
            corr += complex(np.sum(block[1:] * np.conj(block[:-1]), dtype=np.complex128))
        previous = block[-1]
    carrier_offset_hz = float(np.angle(corr) * fs / (2 * math.pi))
    residual_hz = carrier_offset_hz - offset

    # Mix and decimate in bounded chunks. Preserve phase continuity by using
    # absolute sample indices for the NCO.
    decim = 8
    tuned = np.empty(count, dtype=np.complex64)
    chunk = 262144
    omega = -2 * math.pi * carrier_offset_hz / fs
    for start in range(0, count, chunk):
        end = min(count, start + chunk)
        phase = omega * np.arange(start, end, dtype=np.float64)
        tuned[start:end] = iq[start:end] * np.exp(1j * phase).astype(np.complex64)
    del iq

    z = resample_poly(tuned, 1, decim, window=("kaiser", 8.0)).astype(np.complex64)
    del tuned
    demod_fs = fs // decim
    phase = np.angle(z[1:] * np.conj(z[:-1])).astype(np.float32)
    del z
    discriminator_hz = phase * np.float32(demod_fs / (2 * math.pi))
    del phase

    sos = butter(5, (low, high), btype="bandpass", fs=demod_fs, output="sos")
    audio = sosfilt(sos, discriminator_hz).astype(np.float32)
    del discriminator_hz
    audio = resample_poly(audio, audio_rate, demod_fs).astype(np.float32)
    audio -= np.mean(audio, dtype=np.float64)

    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    # Write with one common, transparent scale: normalize each capture peak to
    # -6 dBFS for listening; the JSON retains the pre-normalization levels.
    scale = 0.501187 / peak if peak > 0 else 0.0
    pcm = np.clip(np.rint(audio * scale * 32767), -32768, 32767).astype("<i2")
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(audio_rate)
        wf.writeframes(pcm.tobytes())
    return {
        "input": str(path), "input_bytes": path.stat().st_size,
        "sample_rate_hz": fs, "nominal_tune_offset_hz": offset,
        "estimated_rf_offset_hz": carrier_offset_hz,
        "estimated_residual_cfo_hz": residual_hz,
        "discriminator_rate_hz": demod_fs, "audio_rate_hz": audio_rate,
        "audio_band_hz": [low, high], "audio_samples": int(len(audio)),
        "duration_s": len(audio) / audio_rate,
        "pre_normalization_peak_hz_deviation": peak,
        "pre_normalization_rms_hz_deviation": rms,
        "listening_peak_dbfs": -6.0 if peak > 0 else None,
        "output": str(out), "output_bytes": out.stat().st_size,
        "limitations": [
            "Per-capture peak normalization is for listening only; it is not a calibrated deviation or SNR comparison.",
            "CFO is estimated from a robust median; speech tone fidelity and intelligibility need separate evaluation.",
            "No SDR, radio, serial, PTT, or RF control is used."
        ]
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--sample-rate", type=int, default=1_024_000)
    p.add_argument("--offset-hz", type=float, default=125_000)
    p.add_argument("--audio-rate", type=int, default=8_000)
    p.add_argument("--low-hz", type=float, default=250)
    p.add_argument("--high-hz", type=float, default=3_400)
    a = p.parse_args()
    result = demod(a.input, fs=a.sample_rate, offset=a.offset_hz, out=a.output,
                   audio_rate=a.audio_rate, low=a.low_hz, high=a.high_hz)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
