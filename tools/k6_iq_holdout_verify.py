#!/usr/bin/env python3
"""Offline, pilot-only alignment and held-out complex-IQ verification for Flux.

Inputs are a separately specified complex-baseband TARGET and one complex
OBSERVATION at the same sample rate. This tool cannot determine whether
the observation came from an actual radio, a simulation, or a recording.
It never asserts that joint FSK+APC TX or hardware arbitrary-IQ exists.

One constant sample offset, carrier-frequency offset, and complex gain are
estimated from the PILOT ONLY. The held-out remainder is not used for
alignment, phase equalization, frequency fit, or amplitude normalization.
No radio, serial, SDR control, PTT, firmware, or network dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

MAX_SAMPLES = 2_000_000


def _validate_vector(name: str, vector: np.ndarray) -> np.ndarray:
    result = np.asarray(vector)
    if (result.ndim != 1 or len(result) < 256 or
            len(result) > MAX_SAMPLES or not np.iscomplexobj(result) or
            not np.isfinite(result).all()):
        raise ValueError(f"{name}: need 256..2M finite 1-D complex samples")
    if float(np.vdot(result, result).real) < 1e-12:
        raise ValueError(f"{name}: no usable signal energy")
    return result.astype(np.complex128, copy=False)


def _fitted_metrics(target: np.ndarray, estimate: np.ndarray) -> dict:
    energy = float(np.vdot(target, target).real)
    if energy <= 1e-12:
        raise ValueError("held-out target has no signal energy")
    residual = estimate - target
    nmse = float(np.vdot(residual, residual).real / energy)
    amp_nmse = float(np.dot(np.abs(estimate) - np.abs(target),
                            np.abs(estimate) - np.abs(target)) / energy)
    mask = np.abs(target) > 0.03 * np.max(np.abs(target))
    phases = np.angle(estimate[mask] * np.conj(target[mask]))
    weighted_phase_rms = float(np.sqrt(np.mean(phases**2)))
    corr = float(abs(np.vdot(estimate, target)) /
                 np.sqrt(float(np.vdot(estimate, estimate).real) * energy))
    return {
        "normalized_evm": float(np.sqrt(max(nmse, 0))),
        "normalized_error_power": nmse,
        "amplitude_only_normalized_rms_error": float(np.sqrt(max(amp_nmse, 0))),
        "phase_error_rms_rad": weighted_phase_rms,
        "coherent_correlation": corr,
    }


def _pilot_fit(target: np.ndarray, observed: np.ndarray, fs: float,
               maximum_cfo_hz: float) -> dict | None:
    """Estimate CFO and gain from aligned pilot samples, with no holdout."""
    limit = 0.03
    mask = ((np.abs(target) > limit * max(float(np.max(np.abs(target))), 1e-12)) &
            (np.abs(observed) > limit * max(float(np.max(np.abs(observed))), 1e-12)))
    if int(np.count_nonzero(mask)) < 0.7 * len(target):
        return None
    # The phase of observation * conjugate(target) is constant + CFO*t.
    phase_error = np.unwrap(np.angle(observed[mask] * np.conj(target[mask])))
    sample_index = np.flatnonzero(mask).astype(float)
    slope, _ = np.polyfit(sample_index / fs, phase_error, 1)
    cfo = float(slope / (2 * np.pi))
    if not np.isfinite(cfo) or abs(cfo) > maximum_cfo_hz:
        return None
    correction = np.exp(-2j * np.pi * cfo * np.arange(len(target)) / fs)
    aligned = observed * correction
    power = float(np.vdot(aligned, aligned).real)
    if power < 1e-12:
        return None
    gain = np.vdot(aligned, target) / power
    estimate = aligned * gain
    pilot_evm = _fitted_metrics(target, estimate)["normalized_evm"]
    return {"estimated_cfo_hz": cfo, "complex_gain": gain,
            "pilot_evm": pilot_evm}


def _apc_level_diagnostics(target: np.ndarray, estimate: np.ndarray) -> dict:
    """Optional 0.8/1.0 amplitude-bin diagnostics; NOT causal RF evidence."""
    peak = float(np.max(np.abs(target)))
    if peak <= 1e-12:
        return {"available": False}
    ratio = np.abs(target) / peak
    mask_low = np.abs(ratio - 0.80) <= 0.035
    mask_high = ratio >= 0.965
    if min(int(mask_low.sum()), int(mask_high.sum())) < 32:
        return {"available": False, "reason": "insufficient 0.80/1.00 target levels"}
    result = {"available": True, "normalized_amplitude_targets": [0.8, 1.0]}
    for name, mask in (("low", mask_low), ("high", mask_high)):
        phase = np.angle(estimate[mask] * np.conj(target[mask]))
        result[f"{name}_samples"] = int(mask.sum())
        result[f"{name}_median_phase_residual_rad"] = float(np.median(phase))
        result[f"{name}_median_measured_to_target_amplitude"] = float(
            np.median(np.abs(estimate[mask]) / np.maximum(np.abs(target[mask]), 1e-12))
        )
    result["high_minus_low_median_phase_rad"] = (
        result["high_median_phase_residual_rad"] -
        result["low_median_phase_residual_rad"])
    return result


def verify(target: np.ndarray, observation: np.ndarray, *, fs: float,
           pilot_samples: int, max_lag_samples: int,
           max_cfo_hz: float = 300., maximum_pilot_evm: float = 0.5) -> tuple[dict, dict]:
    target = _validate_vector("target", target)
    observation = _validate_vector("observation", observation)
    if not np.isfinite(fs) or fs < 1000 or fs > 2_000_000:
        raise ValueError("sample rate outside 1 kHz..2 MHz")
    if not 128 <= pilot_samples <= len(target) - 128:
        raise ValueError("pilot needs >=128 samples and >=128 held-out samples")
    if (len(observation) < len(target) or max_lag_samples < 0 or
            max_lag_samples > len(observation) - len(target)):
        raise ValueError("observation must contain full target and allowed pre-roll")
    if max_lag_samples > int(fs * 0.5):
        raise ValueError("offset search limited to 500 ms")
    if not 0 < max_cfo_hz < fs / 2:
        raise ValueError("CFO limit must be positive and below Nyquist")
    if not 0 < maximum_pilot_evm < 1:
        raise ValueError("pilot EVM threshold must be in (0, 1)")

    best = None
    for lag in range(max_lag_samples + 1):
        candidate = _pilot_fit(target[:pilot_samples],
                               observation[lag:lag+pilot_samples], fs, max_cfo_hz)
        if candidate is not None and (best is None or
                                      candidate["pilot_evm"] < best["pilot_evm"]):
            best = {**candidate, "lag_samples": lag}
    if best is None or best["pilot_evm"] >= maximum_pilot_evm:
        raise ValueError("pilot-only acquisition failed; no held-out IQ score")

    lag = best["lag_samples"]
    cfo = best["estimated_cfo_hz"]
    raw = observation[lag:lag+len(target)]
    correction = np.exp(-2j * np.pi * cfo * np.arange(len(target)) / fs)
    aligned = raw * correction * best["complex_gain"]
    pilot_target = target[:pilot_samples]
    heldout_target = target[pilot_samples:]
    heldout = aligned[pilot_samples:]
    report = {
        "analysis": "OFFLINE pilot-trained / held-out complex-IQ comparison",
        "provenance": "input source is not independently authenticated by this tool",
        "combined_polar_tx_demonstrated": False,
        "physical_hardware_iq_evm_verified": False,
        "does_not_equalize_heldout": True,
        "sample_rate_hz": float(fs),
        "target_samples": int(len(target)),
        "pilot_samples": int(pilot_samples),
        "heldout_samples": int(len(heldout_target)),
        "pilot_fraction": float(pilot_samples / len(target)),
        "pilot_only_estimated_capture_offset_samples": int(lag),
        "pilot_only_estimated_carrier_offset_hz": float(cfo),
        "pilot_only_estimated_complex_gain_magnitude": float(abs(best["complex_gain"])),
        "pilot_only_estimated_complex_gain_phase_rad": float(np.angle(best["complex_gain"])),
        "pilot_metrics": _fitted_metrics(pilot_target, aligned[:pilot_samples]),
        "heldout_metrics": _fitted_metrics(heldout_target, heldout),
        "heldout_apc_level_diagnostics": _apc_level_diagnostics(heldout_target, heldout),
        "warnings": [
            "Inputs may be synthetic: this analysis does not establish any real hardware transmitter capability",
            "Single pilot-only timing/CFO/gain fit: no fractional timing, clock drift, channel response or per-sample equalization",
            "Target must be independently defined; using measured RF phase as target invalidates full-complex EVM",
            "Real FD6818B adds unknown FIFO framing, filtered FM settling and APC-induced phase disturbance",
            "Complex IQ EVM alone is not spectral compliance, calibrated power or signal interoperability",
        ]
    }
    arrays = {
        "target_iq": target.astype(np.complex64),
        "aligned_observation_iq": aligned.astype(np.complex64),
        "heldout_target_iq": heldout_target.astype(np.complex64),
        "heldout_observation_iq": heldout.astype(np.complex64),
        "heldout_residual_iq": (heldout - heldout_target).astype(np.complex64),
    }
    return report, arrays


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True,
                        help="independently defined complex64/128 1-D .npy target")
    parser.add_argument("--observed", type=Path, required=True,
                        help="preprocessed and downconverted complex64/128 1-D .npy capture")
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument("--pilot-ms", type=float, default=40.)
    parser.add_argument("--max-lag-ms", type=float, default=10.)
    parser.add_argument("--max-cfo-hz", type=float, default=300.)
    parser.add_argument("--max-pilot-evm", type=float, default=.5)
    parser.add_argument("--out-prefix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        target = np.load(args.target, allow_pickle=False)
        observed = np.load(args.observed, allow_pickle=False)
        ntrain = int(round(args.sample_rate * args.pilot_ms / 1000))
        maxlag = int(round(args.sample_rate * args.max_lag_ms / 1000))
        report, arrays = verify(target, observed, fs=args.sample_rate,
                                pilot_samples=ntrain, max_lag_samples=maxlag,
                                max_cfo_hz=args.max_cfo_hz,
                                maximum_pilot_evm=args.max_pilot_evm)
        report["target_path"] = str(args.target)
        report["observation_path"] = str(args.observed)
        report["target_sha256"] = hashlib.sha256(args.target.read_bytes()).hexdigest()
        report["observation_sha256"] = hashlib.sha256(args.observed.read_bytes()).hexdigest()
        dest = args.out_prefix
        json_path, npz_path = dest.with_suffix(".json"), dest.with_suffix(".npz")
        if json_path.exists() or npz_path.exists():
            raise ValueError("refusing to overwrite prior result")
        dest.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(npz_path, **arrays)
        report["npz_path"] = str(npz_path)
        report["npz_sha256"] = hashlib.sha256(npz_path.read_bytes()).hexdigest()
        json_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, TypeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
