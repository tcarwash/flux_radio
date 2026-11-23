#!/usr/bin/env python3
"""Pilot-trained analysis of fast REG70 gain modulation in saved complex RF IQ.

Scores the FM-discriminator audio tone amplitude at known alternating gain
symbols. This reports the measured receiver response, not RF power or direct
carrier-I/Q amplitude. No hardware, serial, transmission or flash access.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import butter, hilbert, sosfiltfilt

from k6_tx17c_direct_frequency_analyze import extract_iq_and_rx_frequency
from k6_tx15s_rf_analyze import FS

TONE_WORD = 11264
NOMINAL_HZ = TONE_WORD / 10.32444
RATES = ((50.0, 200, 0.020), (100.0, 100, 0.010), (10000/67, 67, 0.0067))
SYMBOLS = 24
PILOT_S = 0.100
FIRST_BLOCK_S = PILOT_S
GAIN_LOW = 16
GAIN_HIGH = 64
EDGE_SKIP_S = 0.001


def _fit_tone(y: np.ndarray, t: np.ndarray, *, bounds=None):
    if len(y) != len(t) or len(y) < 64:
        raise ValueError("tone fit needs matching, sufficiently long samples")
    if bounds is None:
        bounds = (NOMINAL_HZ - 8.0, NOMINAL_HZ + 8.0)
    def fit(hz):
        phase = 2 * np.pi * hz * t
        m = np.stack((np.sin(phase), np.cos(phase), np.ones(len(t))), axis=1)
        c = np.linalg.lstsq(m, y, rcond=None)[0]
        e = y - m @ c
        return float(np.mean(e*e)), c
    opt = minimize_scalar(lambda h: fit(h)[0], bounds=bounds, method="bounded",
                          options={"xatol": 0.0001})
    if not opt.success:
        raise ValueError("pilot-only tone frequency fit failed")
    loss, c = fit(float(opt.x))
    return float(opt.x), float(np.hypot(c[0], c[1])), float(np.arctan2(c[1], c[0])), float(np.sqrt(loss))


def score_audio(f: np.ndarray, t: np.ndarray, onset_s: float) -> dict:
    f = np.asarray(f, dtype=np.float64)
    t = np.asarray(t, dtype=np.float64)
    if f.ndim != 1 or t.ndim != 1 or len(f) != len(t):
        raise ValueError("frequency discriminator and timestamps must be 1-D and equal length")
    pilot = (t >= onset_s + .012) & (t <= onset_s + .088)
    if int(pilot.sum()) < 2000:
        raise ValueError("initial gain-24 pilot is too short")
    tone_hz, pilot_amp, pilot_phase, pilot_rmse = _fit_tone(f[pilot], t[pilot]-onset_s)
    if pilot_amp < 200:
        raise ValueError("initial gain-24 pilot has insufficient FM deviation")

    # TX/RX envelope-onset timing is not precise enough to place short gain
    # symbols from the raw RF amplitude threshold alone. Estimate one common
    # offset from block A only, then apply it unchanged to the two held-out
    # faster-rate blocks. Zero-phase filtering keeps the offline estimator
    # from adding a causal filter delay of its own.
    band = butter(4, (max(100.0, tone_hz-300.0), min(FS/2-100.0, tone_hz+300.0)),
                  btype="bandpass", fs=FS, output="sos")
    env = np.abs(hilbert(sosfiltfilt(band, f)))
    period_a = RATES[0][2]
    offsets = np.arange(-.012, .012001, .00025)
    candidates = []
    nominal_start = onset_s + FIRST_BLOCK_S
    for shift in offsets:
        lv, hv = [], []
        for index in range(SYMBOLS):
            start = nominal_start + shift + index*period_a
            valid = (t >= start+EDGE_SKIP_S) & (t < start+period_a-EDGE_SKIP_S)
            if not valid.any():
                continue
            (lv if index%2==0 else hv).append(float(np.median(env[valid])))
        if len(lv)==SYMBOLS//2 and len(hv)==SYMBOLS//2:
            candidates.append((float(np.median(hv)-np.median(lv)),float(shift)))
    if not candidates:
        raise ValueError("unable to locate first alternating-gain block")
    alignment_contrast, timing_offset = max(candidates)

    blocks = []
    cursor = onset_s + FIRST_BLOCK_S + timing_offset
    for nominal_rate, ticks, period_s in RATES:
        symbols = []
        for index in range(SYMBOLS):
            start = cursor + index * period_s
            a, b = start + EDGE_SKIP_S, start + period_s - EDGE_SKIP_S
            valid = (t >= a) & (t < b)
            if valid.sum() < 160:
                raise ValueError(f"insufficient samples for {nominal_rate:g} symbol {index}")
            phase = 2*np.pi*tone_hz*(t[valid]-onset_s)
            m = np.stack((np.sin(phase), np.cos(phase), np.ones(valid.sum())), axis=1)
            c = np.linalg.lstsq(m, f[valid], rcond=None)[0]
            amp = float(np.hypot(c[0], c[1]))
            ph = float(np.arctan2(c[1], c[0]))
            phase_error = float(np.rad2deg(np.angle(np.exp(1j*(ph-pilot_phase)))))
            gain = GAIN_LOW if index % 2 == 0 else GAIN_HIGH
            symbols.append({"index": index, "gain_code": gain,
                            "deviation_amplitude_hz": amp,
                            "normalized_to_pilot24": amp/pilot_amp,
                            "audio_phase_error_deg": phase_error,
                            "samples": int(valid.sum())})
        low = np.array([x["deviation_amplitude_hz"] for x in symbols if x["gain_code"] == GAIN_LOW])
        high = np.array([x["deviation_amplitude_hz"] for x in symbols if x["gain_code"] == GAIN_HIGH])
        contrast = float((np.median(high)-np.median(low))/pilot_amp)
        phase = np.array([x["audio_phase_error_deg"] for x in symbols])
        blocks.append({"nominal_rate_symbols_s": nominal_rate,
                       "tick_period_100us": ticks,
                       "actual_schedule_rate_symbols_s": 1/period_s,
                       "period_ms": period_s*1000,
                       "low_gain16_median_deviation_hz": float(np.median(low)),
                       "high_gain64_median_deviation_hz": float(np.median(high)),
                       "high_minus_low_over_pilot24": contrast,
                       "symbol_audio_phase_rms_deg": float(np.sqrt(np.mean(phase**2))),
                       "symbols": symbols})
        cursor += SYMBOLS*period_s

    return {
        "pilot_only_fitted_tone_hz": tone_hz,
        "pilot24_deviation_amplitude_hz": pilot_amp,
        "pilot_only_phase_reference_deg": float(np.rad2deg(pilot_phase)),
        "pilot_audio_fit_rmse_hz": pilot_rmse,
        "fit_uses_dynamic_gain_symbols": False,
        "timing_offset_from_RF_onset_s": timing_offset,
        "timing_offset_fit_block": "50 symbols/s alternating gain (timing calibration only)",
        "timing_offset_fit_heldout_contrast_hz": alignment_contrast,
        "50_symbols_s_block_is_timing_training_not_independent_qualification": True,
        "timing_offset_applied_unchanged_to_faster_blocks": True,
        "fast_gain_blocks": blocks,
        "initial_pilot_s": PILOT_S,
        "dynamic_start_after_rf_on_s": FIRST_BLOCK_S,
        "amplitude_measurement": "FM discriminator instantaneous-frequency tone amplitude",
        "direct_rf_iq_amplitude_or_phase_measured": False,
        "interpretation": "Compare high/low separation at each rate; do not infer RF power gain or arbitrary IQ.",
    }


def analyze_file(path: Path) -> dict:
    z, f, t, amp, discovery = extract_iq_and_rx_frequency(path)
    result = score_audio(f, t[:-1], discovery["onset_s"])
    with path.open("rb") as fh:
        digest = hashlib.file_digest(fh, "sha256").hexdigest()
    return {"study": "TX-15X fast REG70 gain update response",
            "analysis_version": "v2-blockA-timing-calibration",
            "source_iq": str(path), "source_iq_sha256": digest,
            "rf_acquisition": discovery, "score": result,
            "offline_read_only": True, "transmission_initiated": False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a=p.parse_args(argv)
    if a.out.exists(): p.error("refusing to overwrite")
    r=analyze_file(a.iq)
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(r,indent=2)+"\n")
    print("pilot tone",round(r["score"]["pilot_only_fitted_tone_hz"],3),"Hz")
    for b in r["score"]["fast_gain_blocks"]:
        print(round(b["actual_schedule_rate_symbols_s"],2),"symbols/s contrast",round(b["high_minus_low_over_pilot24"],3),"pilot units; audio-phase RMS",round(b["symbol_audio_phase_rms_deg"],2),"deg")
    print("saved",a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
