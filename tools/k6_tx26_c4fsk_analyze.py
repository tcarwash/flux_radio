#!/usr/bin/env python3
"""TX-26 FD6818B 4800-baud 4-FSK (REG_5F sign + REG_70 magnitude) offline analyzer.

Supports both:
  1. Synthetic FM-discriminator rendering from Cortex-M0 Unicorn register logs
     (for offline verification of c/k6/mod/tx26_c4fsk_probe.c), and
  2. Independent analysis of future conducted RTL-SDR complex IQ (.npy) captures.

No payload truth is used to acquire packet starts or calibrate 4-FSK thresholds:
packet starts are located solely from the public preamble+sync (16x0x55 + 076b3e36),
and P3's 4-FSK inner/outer threshold and write lag are fitted solely from its
64-symbol pilot before slicing the 416 held-out payload symbols and checking CRC-16.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np

BAUD = 4800.0
NSYM = 480
STEP_SYMS = 96
P1_STEPS = (72, 54, 36, 24, 12)
GAIN_OUTER = 72
GAIN_INNER = 24
SYNC_BYTES = bytes([0x07, 0x6B, 0x3E, 0x36])
PREAMBLE_BYTES = bytes([0x55]) * 16
LEAD_BYTES = PREAMBLE_BYTES + SYNC_BYTES
LEAD_BITS = np.unpackbits(np.frombuffer(LEAD_BYTES, dtype=np.uint8))
assert len(LEAD_BITS) == 160


def gen_pn9_bytes() -> bytes:
    """Bit-identical to gen_pn9() in c/k6/mod/tx26_c4fsk_probe.c."""
    s = 0x1FF
    out = bytearray(60)
    for i in range(60):
        v = 0
        for _ in range(8):
            bit = s & 1
            v = (v << 1) | bit
            s = (s >> 1) | (((s ^ (s >> 5)) & 1) << 8)
        out[i] = v
    return bytes(out)


def crc16_ccitt(data: bytes) -> int:
    c = 0xFFFF
    for byte in data:
        c ^= byte << 8
        for _ in range(8):
            c = ((c << 1) ^ 0x1021) & 0xFFFF if (c & 0x8000) else (c << 1) & 0xFFFF
    return c


def gen_p3_vectors() -> Tuple[bytes, bytes, bytes]:
    """Return (sign_bytes[60], mag_bytes[60], expected_payload_104B) matching C."""
    hdr = b"FLUX TX26 4FSK!\n"
    payload = bytearray(104)
    payload[:16] = hdr
    lfsr = 0x1A7
    for i in range(16, 102):
        v = 0
        for _ in range(8):
            v = (v << 1) | (lfsr & 1)
            lfsr = (lfsr >> 1) | (((lfsr ^ (lfsr >> 5)) & 1) << 8)
        payload[i] = v
    crc = crc16_ccitt(bytes(payload[:102]))
    payload[102] = (crc >> 8) & 0xFF
    payload[103] = crc & 0xFF

    sign_b = bytearray(60)
    mag_b = bytearray(60)
    for i in range(8):
        sign_b[i] = 0xAA
        mag_b[i] = 0xCC
    for i in range(52):
        b0 = payload[2 * i]
        b1 = payload[2 * i + 1]
        sb = 0
        mb = 0
        for k in range(4):
            sb = (sb << 1) | ((b0 >> (7 - 2 * k)) & 1)
            mb = (mb << 1) | ((b0 >> (6 - 2 * k)) & 1)
        for k in range(4):
            sb = (sb << 1) | ((b1 >> (7 - 2 * k)) & 1)
            mb = (mb << 1) | ((b1 >> (6 - 2 * k)) & 1)
        sign_b[8 + i] = sb
        mag_b[8 + i] = mb
    return bytes(sign_b), bytes(mag_b), bytes(payload)


def unpack_fifo_words(words: Sequence[int]) -> np.ndarray:
    """BK4819/FD6818B FIFO order: low byte MSB-first, then high byte MSB-first."""
    raw = bytearray()
    for w in words:
        raw.append(w & 0xFF)
        raw.append((w >> 8) & 0xFF)
    return np.unpackbits(np.frombuffer(bytes(raw), dtype=np.uint8))


def render_fsk_trace_to_fm(
    packets: Sequence[dict],
    reg70_events: Sequence[Tuple[float, int]],
    *,
    fs: float = 48_000.0,
    hz_per_gain: float = 37.5,
    chip_lag_syms: float = 1.75,
    latched: bool = False,
    noise_hz: float = 40.0,
    seed: int = 26,
) -> Tuple[np.ndarray, float]:
    """Render an emulated Cortex-M0 FSK+REG_70 register log into an FM discriminator
    stream (instantaneous Hz at sample rate `fs`)."""
    assert packets, "no packets to render"
    t_end_s = max(p["t_en_us"] for p in packets) * 1e-6 + 0.25
    n_samples = int(np.ceil(t_end_s * fs)) + int(0.05 * fs)
    fm = np.zeros(n_samples, dtype=np.float64)

    events = sorted(reg70_events, key=lambda kv: kv[0])

    def gain_at(t_us: float) -> float:
        g = float(GAIN_OUTER)
        for ev_us, reg_val in events:
            if ev_us <= t_us:
                g = float(reg_val & 0x7F) if (reg_val & 0x80) else 0.0
            else:
                break
        return g

    sym_us = 1e6 / BAUD
    lag_us = chip_lag_syms * sym_us
    for pkt in packets:
        t0_us = pkt["t_en_us"]
        pay_bits = unpack_fifo_words(pkt["words"])
        bits = np.concatenate([LEAD_BITS, pay_bits])
        signs = np.where(bits > 0, 1.0, -1.0)
        latched_g = gain_at(t0_us)
        i0 = max(0, int(np.floor((t0_us * 1e-6) * fs)))
        i1 = min(n_samples, int(np.ceil(((t0_us + len(bits) * sym_us) * 1e-6) * fs)))
        sample_us = (np.arange(i0, i1) / fs) * 1e6
        sym_idx = np.clip(((sample_us - t0_us) / sym_us).astype(np.int64), 0, len(bits) - 1)
        if latched:
            gains = np.full(i1 - i0, latched_g, dtype=np.float64)
        else:
            # Chip's FSK path lags register writes by chip_lag_syms, so a write
            # issued at (t_sym + lag_us) lands right on the symbol edge t_sym.
            eff_us = sample_us + lag_us
            gains = np.empty(i1 - i0, dtype=np.float64)
            ev_idx = 0
            cur_g = float(GAIN_OUTER)
            for k, tu in enumerate(eff_us):
                while ev_idx < len(events) and events[ev_idx][0] <= tu:
                    rv = events[ev_idx][1]
                    cur_g = float(rv & 0x7F) if (rv & 0x80) else 0.0
                    ev_idx += 1
                gains[k] = cur_g
        fm[i0:i1] = signs[sym_idx] * gains * hz_per_gain

    if noise_hz > 0.0:
        rng = np.random.default_rng(seed)
        fm += rng.normal(0.0, noise_hz, size=len(fm))
    return fm, fs


def find_public_sync_starts(fm: np.ndarray, fs: float, expected: int = 4) -> List[float]:
    """Locate payload start sample index for each packet using ONLY public 160-bit
    preamble+sync correlation (no payload truth)."""
    sps = fs / BAUD
    lead_n = int(round(len(LEAD_BITS) * sps))
    idx = np.clip((np.arange(lead_n) / sps).astype(np.int64), 0, len(LEAD_BITS) - 1)
    tmpl = np.where(LEAD_BITS[idx] > 0, 1.0, -1.0)
    tmpl -= tmpl.mean()
    tmpl_norm = float(np.sqrt(np.sum(tmpl * tmpl)))

    # Correlate signed/clipped FM against public preamble+sync
    x = np.clip(fm, -4000.0, 4000.0)
    corr = np.correlate(x, tmpl, mode="valid")
    win_e = np.sqrt(np.convolve(x * x, np.ones(lead_n), mode="valid") + 1e-9)
    rho = corr / (win_e * tmpl_norm)

    min_sep = int(round((len(LEAD_BITS) + NSYM) * 0.8 * sps))
    found: List[Tuple[float, int]] = []
    work = rho.copy()
    for _ in range(expected * 2):
        pk = int(np.argmax(work))
        val = float(work[pk])
        if val < 0.65:
            break
        found.append((val, pk))
        lo = max(0, pk - min_sep)
        hi = min(len(work), pk + min_sep)
        work[lo:hi] = -1.0
    found.sort(key=lambda kv: kv[1])
    return [float(pk + lead_n) for _, pk in found[:expected]]


def sample_symbols(fm: np.ndarray, start_s: float, fs: float, nsym: int = NSYM) -> np.ndarray:
    """Boxcar average over the inner 60% of each symbol (avoids edge transitions)."""
    sps = fs / BAUD
    out = np.empty(nsym, dtype=np.float64)
    for k in range(nsym):
        a = int(round(start_s + (k + 0.2) * sps))
        b = max(a + 1, int(round(start_s + (k + 0.8) * sps)))
        out[k] = float(np.mean(fm[a:b]))
    return out


def analyze_tx26_fm(fm: np.ndarray, fs: float) -> Dict[str, object]:
    """Score P0 (outer baseline), P1 (live REG_70 ladder), P2 (droop), and P3
    (pilot-calibrated held-out 4-FSK + CRC-16)."""
    starts = find_public_sync_starts(fm, fs, expected=4)
    if len(starts) != 4:
        return {"pass": False, "reason": f"expected 4 syncs, found {len(starts)}", "starts": starts}

    pn9_bits = np.unpackbits(np.frombuffer(gen_pn9_bytes(), dtype=np.uint8))
    pn9_signs = np.where(pn9_bits > 0, 1.0, -1.0)

    # P0: binary outer baseline
    s0 = sample_symbols(fm, starts[0], fs)
    p0_outer_hz = float(np.median(np.abs(s0)))
    p0_sign_errs = int(np.sum((s0 > 0) != (pn9_signs > 0)))

    # P1: 5-step REG_70[6:0] ladder (96 symbols per step)
    s1 = sample_symbols(fm, starts[1], fs)
    p1_mags = np.abs(s1)
    # Use interior 70 symbols of each 96-symbol plateau to measure steady level
    step_hz = [
        float(np.median(p1_mags[k * STEP_SYMS + 12 : (k + 1) * STEP_SYMS - 12]))
        for k in range(5)
    ]
    step_ratios = [v / max(step_hz[0], 1e-6) for v in step_hz]
    expected_ratios = [g / P1_STEPS[0] for g in P1_STEPS]
    monotonic = all(step_hz[k] > step_hz[k + 1] * 1.08 for k in range(4))
    latched = (step_hz[0] - step_hz[-1]) / max(step_hz[0], 1e-6) < 0.15

    # Estimate step-landing offset (in symbols relative to nominal 96*k)
    land_offsets: List[float] = []
    if not latched:
        for k in range(1, 5):
            mid = 0.5 * (step_hz[k - 1] + step_hz[k])
            lo = max(0, k * STEP_SYMS - 12)
            hi = min(NSYM, k * STEP_SYMS + 12)
            seg = p1_mags[lo:hi]
            below = np.where(seg < mid)[0]
            if len(below):
                land_offsets.append(float((lo + int(below[0])) - k * STEP_SYMS))

    # P2: 240 zeros then 240 ones — droop check
    s2 = sample_symbols(fm, starts[2], fs)
    z_first = float(np.median(s2[10:110]))
    z_last = float(np.median(s2[130:230]))
    o_first = float(np.median(s2[250:350]))
    o_last = float(np.median(s2[370:470]))
    droop_ratio_zero = abs(z_last) / max(abs(z_first), 1e-6)
    droop_ratio_one = abs(o_last) / max(abs(o_first), 1e-6)

    # P3: 4-FSK — calibrate threshold ONLY from first 64 pilot symbols (+3,-3,+1,-1 x16)
    s3 = sample_symbols(fm, starts[3], fs)
    pilot = s3[:64]
    pilot_outer = np.concatenate([pilot[0::4], pilot[1::4]])
    pilot_inner = np.concatenate([pilot[2::4], pilot[3::4]])
    outer_lvl = float(np.median(np.abs(pilot_outer)))
    inner_lvl = float(np.median(np.abs(pilot_inner)))
    thresh = 0.5 * (outer_lvl + inner_lvl)
    pilot_sep = (outer_lvl - inner_lvl) / max(outer_lvl, 1e-6)

    # Slice held-out 416 symbols (indices 64..479) into 104 bytes
    held = s3[64:]
    rx_sign = (held > 0).astype(np.uint8)
    rx_mag = (np.abs(held) >= thresh).astype(np.uint8)
    dibit_bits = np.empty(832, dtype=np.uint8)
    dibit_bits[0::2] = rx_sign
    dibit_bits[1::2] = rx_mag
    rx_payload = bytes(np.packbits(dibit_bits))
    rx_crc_calc = crc16_ccitt(rx_payload[:102])
    rx_crc_got = (rx_payload[102] << 8) | rx_payload[103]
    crc_ok = rx_crc_calc == rx_crc_got

    exp_sign_b, exp_mag_b, exp_payload = gen_p3_vectors()
    exp_sign = np.unpackbits(np.frombuffer(exp_sign_b, dtype=np.uint8))[64:]
    exp_mag = np.unpackbits(np.frombuffer(exp_mag_b, dtype=np.uint8))[64:]
    held_sign_errs = int(np.sum(rx_sign != exp_sign))
    held_mag_errs = int(np.sum(rx_mag != exp_mag))
    held_sym_errs = int(np.sum((rx_sign != exp_sign) | (rx_mag != exp_mag)))

    passed = (
        p0_sign_errs == 0
        and not latched
        and monotonic
        and pilot_sep > 0.35
        and crc_ok
        and held_sym_errs == 0
        and min(droop_ratio_zero, droop_ratio_one) > 0.85
    )

    return {
        "pass": passed,
        "latched": latched,
        "p0": {"outer_hz": round(p0_outer_hz, 1), "sign_errors": p0_sign_errs},
        "p1": {
            "step_hz": [round(v, 1) for v in step_hz],
            "step_ratios": [round(v, 3) for v in step_ratios],
            "expected_ratios": [round(v, 3) for v in expected_ratios],
            "monotonic": monotonic,
            "land_offsets_sym": land_offsets,
        },
        "p2": {
            "droop_ratio_zero": round(droop_ratio_zero, 4),
            "droop_ratio_one": round(droop_ratio_one, 4),
        },
        "p3": {
            "pilot_outer_hz": round(outer_lvl, 1),
            "pilot_inner_hz": round(inner_lvl, 1),
            "pilot_threshold_hz": round(thresh, 1),
            "pilot_separation": round(pilot_sep, 3),
            "heldout_sign_errors": held_sign_errs,
            "heldout_mag_errors": held_mag_errs,
            "heldout_symbol_errors": held_sym_errs,
            "crc_ok": crc_ok,
            "payload_exact": rx_payload == exp_payload,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iq", required=True, help="Complex IQ .npy capture (or FM float .npy with --fm)")
    ap.add_argument("--fs", type=float, default=1_024_000.0, help="Sample rate in Hz")
    ap.add_argument("--fm", action="store_true", help="Input .npy is already instantaneous FM Hz")
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
        fm -= np.median(fm)
        fs = args.fs

    res = analyze_tx26_fm(fm, fs)
    text = json.dumps(res, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0 if res["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
