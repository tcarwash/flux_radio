#!/usr/bin/env python3
"""Offline native-2FSK-as-one-bit-RF-frequency-DAC experiment.

No device access or radio writes. Model assumes the native RF FSK engine
can change frequency without resetting carrier phase; that must be verified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt, welch


def pdm_encode_frequency_samples(frequency_offsets_hz, *, deviation_hz=4000):
    """First-order phase-error PDM for one desired RF offset per native FSK bit.

    The accumulator is NEVER reset at a symbol/chip boundary. Samples may
    describe 2/4/8-FSK, a Gaussian-shaped trajectory, or a frequency chirp.
    This models an ideal phase-continuous FSK source, not actual silicon.
    """
    targets = np.asarray(frequency_offsets_hz, dtype=float)
    if targets.ndim != 1 or not targets.size or not np.isfinite(targets).all():
        raise ValueError("need finite nonempty 1-D frequency trajectory")
    if not np.isfinite(deviation_hz) or deviation_hz <= 0:
        raise ValueError("deviation must be finite and positive")
    if np.any(np.abs(targets) > deviation_hz):
        raise ValueError("frequency target exceeds native +/- deviation")
    bits = np.empty(len(targets), dtype=np.uint8)
    error = 0.0
    for i, hz in enumerate(targets):
        error += (hz / deviation_hz + 1.0) / 2.0
        bit = int(error >= .5)
        error -= bit
        bits[i] = bit
    return bits


def pdm_encode(symbol_offsets_hz, *, native_baud=2400, symbol_baud=75,
               deviation_hz=4000):
    """Error-feedback conversion from target RF offsets to binary native FSK.

    Cumulative target phase slope becomes a 1-bit sequence of +/- deviation.
    No reset at symbol boundaries: phase-error integrator persists.
    """
    if not np.isfinite([native_baud, symbol_baud, deviation_hz]).all():
        raise ValueError("timing/deviation must be finite")
    if min(native_baud, symbol_baud, deviation_hz) <= 0:
        raise ValueError("timing/deviation must be positive")
    chips = round(native_baud / symbol_baud)
    if chips < 2 or chips > 4096 or abs(native_baud / symbol_baud - chips) > 1e-9:
        raise ValueError("native baud must be integer multiple >=2 of symbol baud")
    values = np.asarray(symbol_offsets_hz, dtype=float)
    if values.ndim != 1 or not values.size or not np.isfinite(values).all():
        raise ValueError("need finite nonempty 1-D target frequency sequence")
    if np.any(np.abs(values) > deviation_hz):
        raise ValueError("frequency target exceeds native +/- deviation")
    return pdm_encode_frequency_samples(np.repeat(values, chips),
                                        deviation_hz=deviation_hz)


def average_offsets(bits, *, native_baud=2400, symbol_baud=75,
                    deviation_hz=4000):
    chips = round(native_baud / symbol_baud)
    values = np.asarray(bits, dtype=np.uint8)
    if not len(values) or len(values) % chips or np.any(values > 1):
        raise ValueError("invalid binary native-FSK vector")
    return deviation_hz * (2. * values.reshape(-1, chips).mean(axis=1) - 1.)


def ideal_rf_model(bits, *, native_baud=2400, deviation_hz=4000,
                   sample_rate=96000, detector_cutoff_hz=300):
    """Ideal instantaneous/phase-continuous RF model; hardware differs."""
    samples_per_bit = round(sample_rate / native_baud)
    if samples_per_bit < 2 or abs(samples_per_bit * native_baud - sample_rate) > 1e-8:
        raise ValueError("sample rate must be an integer multiple of native baud")
    values = np.asarray(bits, dtype=np.uint8)
    if not len(values) or np.any(values > 1):
        raise ValueError("bits must contain 0/1")
    instant_hz = np.repeat(deviation_hz * (2. * values.astype(float) - 1.),
                           samples_per_bit)
    phase = np.cumsum(instant_hz) * (2. * np.pi / sample_rate)
    iq = np.exp(1j * phase)
    sos = butter(4, detector_cutoff_hz, btype="lowpass", fs=sample_rate,
                 output="sos")
    recovered_hz = sosfiltfilt(sos, instant_hz)
    freq, psd = welch(iq, fs=sample_rate, nperseg=min(8192, len(iq)),
                      return_onesided=False, scaling="spectrum")
    order = np.argsort(freq)
    freq, psd = freq[order], np.maximum(psd[order], 0.)
    total = psd.sum()
    # Approximate adjacent-channel pollution of a synthetic ideal phase modulator.
    outside_3khz = float(psd[np.abs(freq) > 3000.].sum() / total)
    powers = np.cumsum(psd) / total
    low = float(np.interp(.005, powers, freq))
    high = float(np.interp(.995, powers, freq))
    return dict(iq=iq, instantaneous_hz=instant_hz,
                recovered_hz=recovered_hz, occupied_bw_99_hz=high-low,
                power_outside_plus_minus_3khz=outside_3khz)


def probe_vector(*, n_symbols=32, spacing_hz=300, seed=17):
    if n_symbols < 1 or spacing_hz <= 0:
        raise ValueError("n_symbols/spacing must be positive")
    symbol_ids = np.random.default_rng(seed).integers(0, 8, n_symbols,
                                                      dtype=np.uint8)
    return symbol_ids, (symbol_ids.astype(float) - 3.5) * spacing_hz


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--native-baud", type=int, default=2400)
    ap.add_argument("--symbol-baud", type=int, default=75)
    ap.add_argument("--deviation-hz", type=float, default=4000)
    ap.add_argument("--spacing-hz", type=float, default=300)
    ap.add_argument("--n-symbols", type=int, default=32)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--max-words", type=int, default=64)
    args = ap.parse_args(argv)
    ids, targets = probe_vector(n_symbols=args.n_symbols,
                                spacing_hz=args.spacing_hz, seed=args.seed)
    bits = pdm_encode(targets, native_baud=args.native_baud,
                      symbol_baud=args.symbol_baud,
                      deviation_hz=args.deviation_hz)
    if len(bits) % 16 or len(bits) > 16 * args.max_words:
        ap.error("requires whole 16-bit FIFO words, within --max-words")
    actual = average_offsets(bits, native_baud=args.native_baud,
                             symbol_baud=args.symbol_baud,
                             deviation_hz=args.deviation_hz)
    rf = ideal_rf_model(bits, native_baud=args.native_baud,
                        deviation_hz=args.deviation_hz,
                        detector_cutoff_hz=max(150., 2 * args.symbol_baud))
    # Compare recovered frequency with ideal symbol target, omitting transitions.
    samples_per_symbol = round(96000 / args.symbol_baud)
    center_error = []
    for i, target in enumerate(targets):
        center = round((i+.5) * samples_per_symbol)
        span = max(2, samples_per_symbol // 8)
        center_error.append(float(np.mean(rf["recovered_hz"][center-span:center+span])-target))
    payload = np.packbits(bits).tobytes()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "native-fsk-pdm.bin").write_bytes(payload)
    result = {
        "simulation_only": True, "transmitted": False,
        "requires_custom_fifo_writer": True,
        "native_baud": args.native_baud, "symbol_baud": args.symbol_baud,
        "deviation_hz_hypothesis": args.deviation_hz,
        "symbol_spacing_hz": args.spacing_hz,
        "symbol_ids": ids.tolist(),
        "target_offsets_hz": targets.tolist(),
        "symbol_avg_offsets_hz": actual.tolist(),
        "average_symbol_error_rms_hz": float(np.sqrt(np.mean((targets-actual)**2))),
        "filtered_center_error_rms_hz": float(np.sqrt(np.mean(np.square(center_error)))),
        "ideal_rf_occupied_bw_99_hz": rf["occupied_bw_99_hz"],
        "ideal_rf_power_outside_plus_minus_3khz_fraction":
            rf["power_outside_plus_minus_3khz"],
        "bits": len(bits), "words": len(payload)//2,
        "payload_hex": payload.hex(),
        "wire_order": "low byte first per 16-bit word; MSB first within each byte",
        "caveat": "ideal instantaneous FSK; no silicon timing, PLL smoothing, phase resets or spectrum mask",
    }
    (args.out_dir / "native-fsk-pdm.json").write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({k:v for k,v in result.items()
                      if k not in {"symbol_ids","target_offsets_hz",
                                   "symbol_avg_offsets_hz","payload_hex"}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
