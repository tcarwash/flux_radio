#!/usr/bin/env python3
"""Receive-only offline RF waveform fidelity study for Flux native binary FSK.

Nothing here opens a serial device, imports radio-control code, or keys TX.
The RFIC model is an IDEAL phase-continuous +/-deviation oscillator; actual
silicon, frequency settling, frame preambles, phase discontinuities, and PA
spectral behavior must be characterized independently.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal import butter, sosfiltfilt

from k6_native_fsk_pdm import (
    ideal_rf_model, pdm_encode_frequency_samples,
)

SAMPLE_RATE = 96000
SUPPORTED_RATES = (1200, 2400, 4800)
KINDS = ("4fsk", "8fsk", "gaussian-8fsk", "chirp", "msk600", "phase-pulse")
MAX_FRAME_BITS = 64 * 16  # existing bounded experimental FSK FIFO record


def make_frequency_trajectory(kind: str, *, native_baud: int,
                              symbol_baud: int, deviation_hz: float,
                              symbols: int, seed: int) -> tuple[np.ndarray, int]:
    """Desired frequency in Hz, at *every native FSK bit clock*."""
    if kind not in KINDS:
        raise ValueError(f"unknown waveform {kind}")
    if native_baud not in SUPPORTED_RATES or not 8 <= symbols <= 256:
        raise ValueError("unsupported native baud or symbol count")
    baud = 600 if kind == "msk600" else symbol_baud
    if baud <= 0 or native_baud % baud:
        raise ValueError("symbol baud must divide native bit baud")
    chips = native_baud // baud
    rng = np.random.default_rng(seed)
    if kind in ("8fsk", "gaussian-8fsk"):
        ids = rng.integers(0, 8, symbols)
        desired = np.repeat((ids - 3.5) * 300.0, chips)
        if kind == "gaussian-8fsk":
            # One possible smoothed frequency trajectory; no claim of a
            # standard BT or interoperable waveform.
            desired = gaussian_filter1d(desired, sigma=max(1.0, 0.35 * chips),
                                        mode="nearest")
    elif kind == "4fsk":
        levels = np.array([-900., -300., 300., 900.])
        desired = np.repeat(levels[rng.integers(0, 4, symbols)], chips)
    elif kind == "msk600":
        # MSK has h = 2*deviation/symbol_baud = 0.5.
        # Ideal source = +/-150 Hz at 600 symbols/s, here represented by
        # much faster +/-deviation native FSK decisions.
        data = rng.integers(0, 2, symbols)
        desired = np.repeat((2.0 * data - 1.0) * (baud / 4.0), chips)
    elif kind == "chirp":
        span = min(0.8 * deviation_hz, 1050.0)
        desired = np.linspace(-span, span, symbols * chips)
    else:
        # Exploratory differential-phase pulses: a frequency pulse can
        # accumulate an approximately pi-radian rotation. This does NOT
        # create amplitude-shaped conventional BPSK.
        duration = native_baud / (2.0 * deviation_hz)
        nchips = max(1, int(round(duration)))
        if abs(nchips - duration) > 0.3 or nchips >= chips:
            raise ValueError("phase-pulse half-cycle is not chip-resolvable")
        desired = np.zeros(symbols * chips)
        for i in range(4, symbols, 6):
            desired[i * chips:i * chips + nchips] = deviation_hz
    if np.max(np.abs(desired)) > deviation_hz + 1e-9:
        raise ValueError(f"{kind} offsets exceed +/-{deviation_hz:g} Hz")
    return desired.astype(np.float64), baud


def analyze(kind: str, *, native_baud: int, symbol_baud: int,
            deviation_hz: float, symbols: int = 32, seed: int = 17,
            lowpass_hz: float = 1600.0) -> tuple[dict, dict]:
    desired, actual_symbol_baud = make_frequency_trajectory(
        kind, native_baud=native_baud, symbol_baud=symbol_baud,
        deviation_hz=deviation_hz, symbols=symbols, seed=seed)
    chips = pdm_encode_frequency_samples(desired, deviation_hz=deviation_hz)
    model = ideal_rf_model(chips, native_baud=native_baud,
                           deviation_hz=deviation_hz,
                           sample_rate=SAMPLE_RATE,
                           detector_cutoff_hz=max(150.0, 2 * actual_symbol_baud))
    samples_per_chip = SAMPLE_RATE // native_baud
    ideal_hz = np.repeat(desired, samples_per_chip)
    ideal_iq = np.exp(2j * np.pi * np.cumsum(ideal_hz) / SAMPLE_RATE)
    approx_iq = model["iq"]
    # Match the very same complex filter to the reference and approximation,
    # then fit one global complex gain/phase. Frequency averages alone
    # cannot establish the intended complex baseband waveform.
    sos = butter(5, lowpass_hz, fs=SAMPLE_RATE, output="sos")
    ref = sosfiltfilt(sos, ideal_iq)
    got = sosfiltfilt(sos, approx_iq)
    trim = SAMPLE_RATE // actual_symbol_baud
    if 2 * trim >= len(got):
        raise ValueError("waveform too short for filtered comparison")
    ref, got = ref[trim:-trim], got[trim:-trim]
    ref_energy = float(np.vdot(ref, ref).real)
    got_energy = float(np.vdot(got, got).real)
    dot = np.vdot(ref, got)
    coherence = (float(abs(dot) / np.sqrt(ref_energy * got_energy))
                 if ref_energy > 0 and got_energy > 0 else 0.0)
    gain = dot / ref_energy
    fit = gain * ref
    fit_energy = float(np.vdot(fit, fit).real)
    evm = float(np.sqrt(np.mean(abs(got - fit) ** 2) /
                        max(np.mean(abs(fit) ** 2), 1e-12)))
    # Phase accumulation error before any arbitrary constant-phase correction:
    # useful diagnostic, not an RF EVM or demodulation success probability.
    phase_error = np.angle(approx_iq * np.conj(ideal_iq))
    row = {
        "kind": kind,
        "native_baud": native_baud,
        "waveform_symbol_baud": actual_symbol_baud,
        "binary_deviation_hz_assumed": deviation_hz,
        "seed": seed,
        "symbols": symbols,
        "native_fsk_bits": int(len(chips)),
        "native_fsk_fifo_words_if_framed": int((len(chips) + 15) // 16),
        "exceeds_existing_64_word_lab_frame": bool(len(chips) > MAX_FRAME_BITS),
        "complex_coherence": round(coherence, 6),
        "normalized_evm": round(evm, 6),
        "coherent_signal_power_rel": round(fit_energy / len(ref), 6),
        "filtered_rf_power_rel": round(got_energy / len(got), 6),
        "phase_error_rms_rad_wrapped": round(float(np.sqrt(np.mean(phase_error**2))), 6),
        "ideal_rf_obw_99_hz": round(float(model["occupied_bw_99_hz"]), 3),
        "ideal_rf_power_outside_pm_3khz_fraction": round(
            float(model["power_outside_plus_minus_3khz"]), 6),
        "model": "ideal-instantaneous-phase-continuous-binary-fsk",
        "verified_on_hardware": False,
    }
    vectors = {
        "native_bits": chips,
        "desired_hz_per_native_chip": desired,
    }
    return row, vectors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--modes", default="4fsk,8fsk,gaussian-8fsk,chirp,msk600")
    ap.add_argument("--native-bauds", default="1200,2400,4800")
    ap.add_argument("--deviations-hz", default="1100,1200,1400,4000")
    ap.add_argument("--symbol-baud", type=int, default=75)
    ap.add_argument("--symbols", type=int, default=32)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--lowpass-hz", type=float, default=1600.0)
    args = ap.parse_args(argv)
    try:
        modes = [m.strip() for m in args.modes.split(",")]
        rates = [int(x.strip()) for x in args.native_bauds.split(",")]
        deviations = [float(x.strip()) for x in args.deviations_hz.split(",")]
        if (not modes or not rates or not deviations or
            len(set(modes)) != len(modes) or len(set(rates)) != len(rates) or
            len(set(deviations)) != len(deviations) or
            not all(m in KINDS for m in modes) or
            not all(r in SUPPORTED_RATES for r in rates) or
            not all(np.isfinite(d) and d > 0 for d in deviations) or
            not 100 <= args.lowpass_hz < SAMPLE_RATE / 2 or
            args.symbol_baud <= 0):
            raise ValueError("invalid modes, rates, deviation sweep, or filter")
        rows = []
        args.out_dir.mkdir(parents=True, exist_ok=True)
        for kind in modes:
            for rate in rates:
                for deviation in deviations:
                    row, vectors = analyze(
                        kind, native_baud=rate, symbol_baud=args.symbol_baud,
                        deviation_hz=deviation, symbols=args.symbols,
                        seed=args.seed, lowpass_hz=args.lowpass_hz)
                    tag = f"{kind}-b{rate}-d{deviation:g}"
                    np.savez_compressed(args.out_dir / f"{tag}.npz", **vectors)
                    row["vectors"] = f"{tag}.npz"
                    rows.append(row)
    except (ValueError, OverflowError) as exc:
        ap.error(str(exc))
    report = {
        "type": "Flux offline native 2-FSK waveform fidelity sweep",
        "receive_only": True,
        "rf_transmission": False,
        "rf_hardware_calibration": False,
        "reference": "ideal continuous-phase RF for the desired frequency trajectory",
        "comparison_filter_hz": args.lowpass_hz,
        "sample_rate_hz": SAMPLE_RATE,
        "limitations": [
            "Assumes ideal instantaneous frequency steps and phase continuity.",
            "Not a measured hardware spectrum, RF mask, calibrated deviation or BER.",
            "Complex coherence reflects the selected comparison filter and fixture.",
            "Frames above 64 words require an unimplemented multi-frame stream.",
            "Native FSK overhead/preamble and startup/stop transients omitted.",
            "A phase-pulse is not standard BPSK or an amplitude-shaped waveform.",
        ],
        "cases": rows,
    }
    report_path = args.out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Offline only: {len(rows)} cases; results at {report_path}")
    for row in rows:
        print(f"{row['kind']:17} {row['native_baud']:4} b/s "
              f"dev={row['binary_deviation_hz_assumed']:6g} Hz  "
              f"coh={row['complex_coherence']:.3f}  "
              f"evm={row['normalized_evm']:.2f}  "
              f"OBW99={row['ideal_rf_obw_99_hz']:.0f} Hz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
