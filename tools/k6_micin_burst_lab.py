#!/usr/bin/env python3
"""Flux MICIN short-period, loopable 4FSK voltage-reference and AC-coupling lab.

OFFLINE ONLY. Produces a deterministic 48-symbol shaped voltage-control
reference, one WAV containing repeated 44-ms cycles, and a SIMULATION of
hypothetical analog high-pass paths. Never plays audio, opens a radio,
keys a transmitter, accesses an SDR or changes firmware/registers.

An arbitrary 100-ms gate contains >=1 complete 44-ms cycle in an
otherwise continuous, glitch-free audio source, but only if RF startup/
shutdown and source interruptions consume <12 ms in aggregate. This is
a mathematical timing condition, NOT verified physical synchronization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

from k6_micin_4fsk import LEVELS, SYNC, make_shaped_4fsk, write_wav

FS = 48000
BAUD = 1200
GUARD_MS = 2
PILOT_SYMBOLS = 16
TOTAL_SYMBOLS = 48
TX_BURST_MS = 100
CUTOFFS_HZ = (0, 20, 100, 300)


def reference_symbols(pattern="stress") -> np.ndarray:
    """Two equal-duration cases: long DC plateaus and DC-balanced data."""
    if pattern == "stress":
        tail = np.repeat(np.arange(4, dtype=np.uint8), 8)
    elif pattern == "balanced":
        tail = np.tile(np.array([0,3,1,2,3,0,2,1],dtype=np.uint8),4)
    else:
        raise ValueError("pattern must be stress or balanced")
    return np.r_[SYNC, tail].astype(np.uint8)


def make_cycle(*, amplitude=0.03, bt=0.5, pattern="stress") -> tuple[np.ndarray, np.ndarray]:
    if not (0.001 <= amplitude <= 0.1 and 0.15 <= bt <= 1.5):
        raise ValueError("outside previously used MICIN reference limits")
    symbols = reference_symbols(pattern)
    wave = make_shaped_4fsk(symbols, sample_rate=FS, symbol_baud=BAUD,
                            bt=bt, amplitude=amplitude, guard_ms=GUARD_MS)
    if len(wave) != 2112:
        raise AssertionError("48 symbols at 1200 and 2+2-ms guards must yield 2112 samples")
    if np.max(abs(wave)) > amplitude + 1e-10:
        raise ValueError("waveform above intended reference amplitude")
    if abs(wave[0]) > 1e-4 or abs(wave[-1]) > 1e-4:
        raise ValueError("loop boundary unexpectedly far from zero")
    return wave, symbols


def score_coupling(cycle: np.ndarray, symbols: np.ndarray, *,
                   cutoff_hz: float, pattern="stress") -> dict:
    if (not np.isfinite(cutoff_hz) or cutoff_hz < 0 or
            cutoff_hz >= FS/2):
        raise ValueError("unsupported hypothetical HPF cutoff")
    cycle = np.asarray(cycle, dtype=float)
    symbols = np.asarray(symbols)
    if (cycle.shape != (2112,) or symbols.shape != (48,) or
            not np.isfinite(cycle).all() or
            not np.array_equal(symbols, reference_symbols(pattern))):
        raise ValueError("invalid deterministic MICIN fixture vector")
    # Analyze a middle period so 1-pole HPF startup transients are settled.
    train_cycles = np.tile(cycle, 24)
    if cutoff_hz:
        sos = butter(1, cutoff_hz, btype="highpass", fs=FS, output="sos")
        observed = sosfilt(sos, train_cycles)
    else:
        observed = train_cycles.copy()
    steady = observed[12*len(cycle):13*len(cycle)]
    sps = FS//BAUD
    samples = round(GUARD_MS*FS/1000) + (np.arange(len(symbols))+.5)*sps
    indices = np.round(samples).astype(int)
    v = steady[indices]
    truth = LEVELS[symbols]
    # One scale/offset calibration on the fixed 16-symbol pilot only.
    # No holdout samples, labels, timing or gain are used in the fit.
    design = np.column_stack([truth[:PILOT_SYMBOLS],
                              np.ones(PILOT_SYMBOLS)])
    slope, offset = np.linalg.lstsq(design, v[:PILOT_SYMBOLS], rcond=None)[0]
    if abs(slope) < 1e-6:
        raise ValueError("hypothetical path makes even pilot undecodable")
    inferred = (v - offset)/slope
    decisions = np.argmin(abs(inferred[:, None]-LEVELS[None, :]), axis=1)
    hold = slice(PILOT_SYMBOLS, len(symbols))
    residual = inferred[hold]-truth[hold]
    plateau = {}
    for symbol in range(4):
        indices_at_level = np.flatnonzero(symbols == symbol)
        heldout_level = indices_at_level[indices_at_level >= PILOT_SYMBOLS]
        plateau[str(symbol)] = {
            "true_level": float(LEVELS[symbol]),
            "first_symbol_estimate": float(inferred[heldout_level[0]]),
            "last_symbol_estimate": float(inferred[heldout_level[-1]]),
            "median_estimate": float(np.median(inferred[heldout_level])),
            "errors": int(np.count_nonzero(decisions[heldout_level] != symbol)),
        }
    return {
        "hypothetical_first_order_highpass_cutoff_hz": float(cutoff_hz),
        "physical_micin_hpf_measured": False,
        "pilot_only_calibration_samples": PILOT_SYMBOLS,
        "heldout_samples": TOTAL_SYMBOLS-PILOT_SYMBOLS,
        "pilot_fit_slope_wav_units_per_level": float(slope),
        "pilot_fit_offset_wav_units": float(offset),
        "pilot_symbol_errors": int(np.count_nonzero(decisions[:PILOT_SYMBOLS] != symbols[:PILOT_SYMBOLS])),
        "heldout_symbol_errors": int(np.count_nonzero(decisions[hold] != symbols[hold])),
        "heldout_normalized_level_rmse": float(np.sqrt(np.mean(residual**2))),
        "heldout_level_plateaus": plateau,
    }


def analyze(*, amplitude=.03, bt=.5, cutoffs=CUTOFFS_HZ,
            pattern="stress") -> tuple[dict,np.ndarray]:
    cycle, symbols = make_cycle(amplitude=amplitude, bt=bt, pattern=pattern)
    if not cutoffs or len(cutoffs)>8 or len(set(cutoffs))!=len(cutoffs):
        raise ValueError("need 1..8 distinct hypothetical filter cases")
    cases = [score_coupling(cycle, symbols, cutoff_hz=fc, pattern=pattern) for fc in cutoffs]
    period_ms = len(cycle)/FS*1000
    margin = TX_BURST_MS - 2*period_ms
    return {
        "project": "Flux",
        "experiment": "TX-12 offline loopable MICIN voltage 4FSK + hypothetical coupling",
        "offline_only": True, "radio_tx_performed": False,
        "physical_micin_connection_verified": False,
        "physical_micin_frequency_response_measured": False,
        "physical_audio_to_rf_sync_demonstrated": False,
        "independent_watchdog_and_compiled_firmware_hardware_verified": False,
        "sample_rate_hz": FS, "symbol_rate_baud": BAUD,
        "nominal_fullscale_amplitude": amplitude, "gaussian_bt": bt,
        "heldout_pattern": pattern,
        "pilot_symbols": PILOT_SYMBOLS, "heldout_symbols": 32,
        "all_symbol_indices": symbols.tolist(),
        "lead_tail_guard_ms_each": GUARD_MS,
        "cycle_samples": len(cycle), "cycle_ms": period_ms,
        "simulated_rf_gate_ms": TX_BURST_MS,
        "max_combined_rf_startup_shutdown_and_playback_interruption_ms_if_unsynchronized":
            margin,
        "one_whole_period_in_gate_guaranteed_ideal_only": bool(margin>=0),
        "cycle_float64_samples_sha256": hashlib.sha256(cycle.astype("<f8").tobytes()).hexdigest(),
        "source_representation": "WAV control voltage fraction, NOT calibrated MICIN millivolts",
        "simulated_coupling": cases,
        "caveats": [
            "High-pass corner frequencies are hypothetical. No MICIN electrical transfer has been measured",
            "This is signed analog instantaneous-frequency control, not a hardware native four-level FSK engine",
            "A continuous, glitch-free repeated source and stable RF gate are REQUIRED for timing guarantee",
            "The 12-ms timing margin is before accounting for actual RF attack, startup, shutdown or DAC stalls",
            "WAV amplitude is not MICIN voltage. Do not connect an unmeasured audio output directly to MICIN",
            "Pilot-only affine calibration is a simplified detector, not an RF modulation performance assessment",
            "Experimental MICIN firmware remains a bench-only research image; emulator acceptance passed after separate test harness repair",
            "No audio playback, RF TX, flashing or SPI mutations were made",
        ]
    },cycle


def main(argv=None) -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir",type=Path,required=True)
    ap.add_argument("--amplitude",type=float,default=.03)
    ap.add_argument("--bt",type=float,default=.5)
    ap.add_argument("--repeats",type=int,default=200)
    ap.add_argument("--pattern",choices=("stress","balanced"),default="stress")
    args=ap.parse_args(argv)
    if not 4<=args.repeats<=400:
        ap.error("repeats must be 4..400")
    try:
        report,cycle=analyze(amplitude=args.amplitude,bt=args.bt,
                             pattern=args.pattern)
        dest=args.out_dir
        paths=[dest/"micin-4fsk-loop.wav",dest/"micin-4fsk-cycle.npy",
               dest/"micin-4fsk-loop.json"]
        if any(path.exists() for path in paths):
            raise ValueError("refusing to overwrite existing fixture artifacts")
        dest.mkdir(parents=True,exist_ok=True)
        np.save(paths[1],cycle,allow_pickle=False)
        write_wav(paths[0],np.tile(cycle,args.repeats),FS)
        report["loops_in_wav"]=args.repeats
        report["audio_duration_seconds"]=len(cycle)*args.repeats/FS
        report["waveform_paths"]=[str(path) for path in paths[:2]]
        report["wav_sha256"]=hashlib.sha256(paths[0].read_bytes()).hexdigest()
        report["cycle_npy_sha256"]=hashlib.sha256(paths[1].read_bytes()).hexdigest()
        paths[2].write_text(json.dumps(report,indent=2)+"\n")
        print(json.dumps({k:v for k,v in report.items()
                          if k not in ("all_symbol_indices","caveats")},indent=2))
    except (ValueError,OSError,TypeError) as exc:
        ap.error(str(exc))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
