#!/usr/bin/env python3
"""TX-27 FD6818B REG_51 8-bit signed DC frequency DAC offline analyzer.

Renders Cortex-M0 Unicorn REG_51 register write traces (with optional 1st-order
subaudio LPF + AWGN) into an FM-discriminator stream, and independently scores:
  1. 9-step static signed DAC staircase (+60,+40,+20,0,-20,-40,-60,+60,-60)
     for linearity, monotonicity, sign symmetry, and zero-hold stability;
  2. Pilot-only 4-FSK timing & threshold calibration on the 24-symbol pilot
     (+3,-3,+1,-1 x6), followed by held-out 64-symbol (128-bit) decode and
     CRC-16/CCITT-FALSE verification;
  3. 20-step 2.5 ms alternating +60/-60 edge-rate amplitude retention.

No payload truth is used to locate timing or calibrate 4-FSK thresholds.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, List, Sequence, Tuple

import numpy as np

STAIRCASE = (60, 40, 20, 0, -20, -40, -60, 60, -60)
DAC_OUTER = 60
DAC_INNER = 20
EXPECTED_MSG = b"FLUX TX27 DAC!"


def crc16_ccitt(data: bytes) -> int:
    c = 0xFFFF
    for byte in data:
        c ^= byte << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if (c & 0x8000) else (c << 1) & 0xFFFF
    return c


def expected_frame_bytes() -> bytes:
    crc = crc16_ccitt(EXPECTED_MSG)
    return EXPECTED_MSG + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def decode_reg51_signed(val: int) -> int:
    """Decode REG_51 in DCS mode (bit15=enable, bit13=neg, bit12=0, [6:0]=mag)."""
    if (val & 0x8000) == 0 or (val & 0x1000) != 0:
        return 0
    mag = val & 0x7F
    return -mag if (val & 0x2000) else mag


def render_reg51_trace_to_fm(
    reg51_events: Sequence[Tuple[float, int]],
    *,
    fs: float = 20_000.0,
    hz_per_code: float = 12.0,
    lpf_cutoff_hz: float = 350.0,
    ignore_gain_in_dcs: bool = False,
    noise_hz: float = 15.0,
    seed: int = 27,
) -> Tuple[np.ndarray, float]:
    """Render timestamped (us, reg51) events into an instantaneous FM Hz stream."""
    assert reg51_events, "no REG_51 events"
    events = sorted(reg51_events, key=lambda kv: kv[0])
    t_end_s = events[-1][0] * 1e-6 + 0.10
    n = int(np.ceil(t_end_s * fs))
    raw = np.zeros(n, dtype=np.float64)

    for idx, (t_us, val) in enumerate(events):
        i0 = max(0, int(round(t_us * 1e-6 * fs)))
        i1 = n if idx + 1 == len(events) else min(n, int(round(events[idx + 1][0] * 1e-6 * fs)))
        if i1 <= i0:
            continue
        code = decode_reg51_signed(val)
        if ignore_gain_in_dcs and code != 0:
            code = DAC_OUTER if code > 0 else -DAC_OUTER
        raw[i0:i1] = float(code) * hz_per_code

    # Simple 1st-order IIR LPF modeling subaudio path
    if lpf_cutoff_hz and lpf_cutoff_hz > 0.0:
        dt = 1.0 / fs
        rc = 1.0 / (2.0 * np.pi * lpf_cutoff_hz)
        alpha = dt / (rc + dt)
        fm = np.empty_like(raw)
        y = 0.0
        for k, x in enumerate(raw):
            y += alpha * (x - y)
            fm[k] = y
    else:
        fm = raw.copy()

    if noise_hz > 0.0:
        rng = np.random.default_rng(seed)
        fm += rng.normal(0.0, noise_hz, size=len(fm))
    return fm, fs


def analyze_tx27_fm(fm: np.ndarray, fs: float, sym_us: float = 5000.0) -> Dict[str, object]:
    """Independently locate the waveform from the leading +60 step, score the
    9-step staircase, calibrate 4-FSK ONLY on the 24-symbol pilot, and decode
    the 64 held-out 4-FSK symbols + CRC-16 + 2.5 ms edge burst."""
    # Remove lead baseline DC offset using initial quiet prefix
    n_quiet = max(10, int(round(0.03 * fs)))
    dc0 = float(np.median(fm[:n_quiet]))
    sig = fm - dc0

    # Locate the first strong positive excursion (staircase step 0 = +60)
    peak_est = float(np.percentile(np.abs(sig), 98))
    if peak_est < 50.0:
        return {"pass": False, "reason": "no modulation detected"}
    above = np.where(sig > 0.5 * peak_est)[0]
    if len(above) == 0:
        return {"pass": False, "reason": "missing leading +60 staircase edge"}
    t0_s = float(above[0]) / fs

    # 1. Score 9-step staircase (45 ms/step, sample inner 55%)
    step_s = 0.045
    stair_hz: List[float] = []
    for k in range(9):
        a = int(round((t0_s + (k + 0.25) * step_s) * fs))
        b = max(a + 1, int(round((t0_s + (k + 0.80) * step_s) * fs)))
        stair_hz.append(float(np.median(sig[a:b])))

    # Strictly decreasing across +60, +40, +20, 0, -20, -40, -60
    monotonic = all(stair_hz[k] > stair_hz[k + 1] + 40.0 for k in range(6))
    zero_resid = abs(stair_hz[3]) / max(abs(stair_hz[0]), 1e-6)
    sym_err = abs(abs(stair_hz[0]) - abs(stair_hz[6])) / max(abs(stair_hz[0]), 1e-6)
    repeat_pos_err = abs(stair_hz[7] - stair_hz[0]) / max(abs(stair_hz[0]), 1e-6)
    repeat_neg_err = abs(stair_hz[8] - stair_hz[6]) / max(abs(stair_hz[6]), 1e-6)

    # 2. Locate 24-symbol 4-FSK pilot after 9*45ms + 40ms separator (= 445 ms)
    sym_s = sym_us * 1e-6
    nom_pilot_s = t0_s + 9.0 * step_s + 0.040
    # Fine-align pilot start over +-3 ms using ONLY the known 24-symbol pilot pattern
    pilot_tmpl = np.array([3.0, -3.0, 1.0, -1.0] * 6, dtype=np.float64)
    pilot_tmpl /= np.linalg.norm(pilot_tmpl)
    best_corr = -1e9
    best_t_pilot = nom_pilot_s
    for off_ms in np.linspace(-3.0, 3.0, 61):
        cand = nom_pilot_s + off_ms * 1e-3
        vals = np.empty(24, dtype=np.float64)
        for k in range(24):
            a = int(round((cand + (k + 0.30) * sym_s) * fs))
            b = max(a + 1, int(round((cand + (k + 0.85) * sym_s) * fs)))
            vals[k] = float(np.mean(sig[a:b]))
        nrm = float(np.linalg.norm(vals)) + 1e-9
        c = float(np.dot(vals, pilot_tmpl) / nrm)
        if c > best_corr:
            best_corr = c
            best_t_pilot = cand

    # Sample all 90 symbols (24 pilot + 64 held-out + 2 guard)
    sym_vals = np.empty(90, dtype=np.float64)
    for k in range(90):
        a = int(round((best_t_pilot + (k + 0.30) * sym_s) * fs))
        b = max(a + 1, int(round((best_t_pilot + (k + 0.85) * sym_s) * fs)))
        sym_vals[k] = float(np.mean(sig[a:b]))

    pilot = sym_vals[:24]
    outer_pos = float(np.median(pilot[0::4]))
    outer_neg = float(np.median(pilot[1::4]))
    inner_pos = float(np.median(pilot[2::4]))
    inner_neg = float(np.median(pilot[3::4]))
    pos_thresh = 0.5 * (outer_pos + inner_pos)
    neg_thresh = 0.5 * (outer_neg + inner_neg)
    inner_outer_sep = (
        0.5 * ((outer_pos - inner_pos) + (inner_neg - outer_neg))
        / max(0.5 * (outer_pos - outer_neg), 1e-6)
    )

    # Slice 64 held-out symbols into 16 bytes (14 ASCII + 2-byte CRC-16)
    held = sym_vals[24:88]
    bits = np.empty(128, dtype=np.uint8)
    for k, v in enumerate(held):
        sign_bit = 1 if v >= 0.0 else 0
        mag_bit = 1 if (v >= pos_thresh if sign_bit else v <= neg_thresh) else 0
        bits[2 * k] = sign_bit
        bits[2 * k + 1] = mag_bit
    rx_bytes = bytes(np.packbits(bits))
    crc_calc = crc16_ccitt(rx_bytes[:14])
    crc_got = (rx_bytes[14] << 8) | rx_bytes[15]
    crc_ok = crc_calc == crc_got

    exp = expected_frame_bytes()
    exp_bits = np.unpackbits(np.frombuffer(exp, dtype=np.uint8))
    held_bit_errs = int(np.sum(bits != exp_bits))
    held_sym_errs = int(
        np.sum((bits[0::2] != exp_bits[0::2]) | (bits[1::2] != exp_bits[1::2]))
    )

    # 3. 20-step 2.5 ms alternating +60/-60 edge-rate probe
    t_edge = best_t_pilot + 90.0 * sym_s
    edge_vals = []
    for k in range(20):
        a = int(round((t_edge + (k + 0.35) * 0.0025) * fs))
        b = max(a + 1, int(round((t_edge + (k + 0.90) * 0.0025) * fs)))
        edge_vals.append(float(np.mean(sig[a:b])))
    edge_swing = float(np.median([abs(v) for v in edge_vals]))
    edge_retention = edge_swing / max(0.5 * (abs(stair_hz[0]) + abs(stair_hz[6])), 1e-6)

    passed = (
        monotonic
        and zero_resid < 0.08
        and sym_err < 0.10
        and inner_outer_sep > 0.35
        and best_corr > 0.95
        and crc_ok
        and held_sym_errs == 0
    )

    return {
        "pass": passed,
        "staircase": {
            "hz": [round(v, 1) for v in stair_hz],
            "monotonic": monotonic,
            "zero_residual_frac": round(zero_resid, 4),
            "symmetry_err": round(sym_err, 4),
            "repeat_pos_err": round(repeat_pos_err, 4),
            "repeat_neg_err": round(repeat_neg_err, 4),
        },
        "payload_4fsk": {
            "pilot_corr": round(best_corr, 4),
            "outer_pos_hz": round(outer_pos, 1),
            "inner_pos_hz": round(inner_pos, 1),
            "inner_neg_hz": round(inner_neg, 1),
            "outer_neg_hz": round(outer_neg, 1),
            "inner_outer_sep": round(inner_outer_sep, 3),
            "heldout_bit_errors": held_bit_errs,
            "heldout_symbol_errors": held_sym_errs,
            "crc_ok": crc_ok,
            "decoded_ascii": rx_bytes[:14].decode("ascii", errors="replace"),
            "payload_exact": rx_bytes == exp,
        },
        "edge_2p5ms": {
            "median_abs_hz": round(edge_swing, 1),
            "retention_vs_static": round(edge_retention, 3),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iq", required=True, help="Complex IQ .npy (or FM Hz .npy with --fm)")
    ap.add_argument("--fs", type=float, default=1_024_000.0)
    ap.add_argument("--fm", action="store_true")
    ap.add_argument("--sym-us", type=float, default=5000.0)
    ap.add_argument("--out", help="Optional JSON output path")
    args = ap.parse_args()

    arr = np.load(args.iq)
    if args.fm:
        fm = arr.astype(np.float64)
        fs = args.fs
    else:
        iq = arr.astype(np.complex128)
        dphi = np.angle(iq[1:] * np.conj(iq[:-1]))
        fm = dphi * (args.fs / (2.0 * np.pi))
        fs = args.fs

    res = analyze_tx27_fm(fm, fs, sym_us=args.sym_us)
    text = json.dumps(res, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
