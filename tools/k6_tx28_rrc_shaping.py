#!/usr/bin/env python3
"""TX-28 offline RRC/Gaussian 4-FSK pulse-shaping benchmark (1x vs 4x vs 8x REG_70).

Models the exact physical constraints of FD6818B 4800-baud 4-FSK (REG_5F sign +
quantized 7-bit REG_70[6:0] magnitude) under:
  - 1x rectangular (1 write/symbol, stock 117 us SPI),
  - 4x oversampled RRC (19.2 kWrites/s, 52 us budget — requires fast SPI),
  - 8x oversampled RRC (38.4 kWrites/s, 26 us budget — requires fast SPI),
  - Ideal continuous RRC 4-FSK reference (e.g. YSF/M17/DMR target).

Quantifies adjacent-channel power ratio (ACPR at +-12.5 kHz, 9 kHz channel BW),
99% occupied bandwidth, and RRC matched-filter normalized symbol EVM.
SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import json
from typing import Dict

import numpy as np

BAUD = 4800.0
OUTER_HZ = 2400.0  # M17-like +-2.4 kHz outer (+-800 Hz inner)
HZ_PER_GAIN = 33.3333333333  # gain 72 -> 2400 Hz, gain 24 -> 800 Hz


def rrc_taps(sps: int, alpha: float = 0.5, span_syms: int = 8) -> np.ndarray:
    """Unit-energy root-raised-cosine FIR impulse response."""
    n = span_syms * sps
    t = np.arange(-n // 2, n // 2 + 1, dtype=np.float64) / float(sps)
    h = np.zeros_like(t)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            h[i] = 1.0 - alpha + 4.0 * alpha / np.pi
        elif alpha > 0 and abs(abs(4.0 * alpha * ti) - 1.0) < 1e-10:
            h[i] = (alpha / np.sqrt(2.0)) * (
                (1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * alpha))
                + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * alpha))
            )
        else:
            num = np.sin(np.pi * ti * (1.0 - alpha)) + 4.0 * alpha * ti * np.cos(
                np.pi * ti * (1.0 + alpha)
            )
            den = np.pi * ti * (1.0 - (4.0 * alpha * ti) ** 2)
            h[i] = num / den
    return h / np.sqrt(np.sum(h * h))


def synthesize_4fsk_fm(
    symbols: np.ndarray,
    *,
    sim_sps: int = 64,
    write_osr: int = 1,
    quantize_reg70: bool = True,
    signed_dac: bool = False,
    alpha: float = 0.5,
) -> np.ndarray:
    """Synthesize instantaneous FM deviation (Hz) at `BAUD * sim_sps` Hz.

    Symbol k occupies interval `[k * sim_sps, (k + 1) * sim_sps)` with its
    impulse / optimal sampling instant centered at `(k + 0.5) * sim_sps`.

    Modes:
      - `write_osr == 0`: ideal continuous RRC 4-FSK reference.
      - `write_osr == 1`: rectangular 1 write/symbol (`REG_5F` + `REG_70`).
      - `write_osr >= 2, signed_dac=False`: `REG_5F` sign held constant across
        each symbol interval; `REG_70[6:0]` updates `write_osr` times/symbol
        with quantized 7-bit nonnegative gain (opposite-sign lobes clamped to 0).
      - `write_osr >= 2, signed_dac=True`: signed 8-bit magnitude+sign updates
        `write_osr` times/symbol (e.g. TX-27 `REG_51[13,6:0]` or sub-symbol sign).
    """
    n_sym = len(symbols)
    ups = np.zeros(n_sym * sim_sps, dtype=np.float64)
    ups[sim_sps // 2 :: sim_sps] = symbols.astype(np.float64) * (OUTER_HZ / 3.0)
    h = rrc_taps(sim_sps, alpha=alpha, span_syms=8)
    ideal_fm = np.convolve(ups, h, mode="same") * np.sqrt(sim_sps)
    # Normalize steady outer-level DC gain so +3/+3/+3... -> +OUTER_HZ
    h_dc = float(np.sum(h)) * np.sqrt(sim_sps) / sim_sps
    ideal_fm /= h_dc

    if write_osr == 0:
        return ideal_fm

    if write_osr == 1:
        # Rectangular 1 write/symbol: exact sign from REG_5F, outer/inner gain from REG_70
        g_code = np.where(np.abs(symbols) > 2, 72, 24)
        fm_sym = np.sign(symbols) * g_code * HZ_PER_GAIN
        return np.repeat(fm_sym, sim_sps)

    assert sim_sps % write_osr == 0
    hold = sim_sps // write_osr
    fm = np.empty(n_sym * sim_sps, dtype=np.float64)
    for k in range(n_sym):
        sym_sign = 1.0 if symbols[k] > 0 else -1.0
        for sub in range(write_osr):
            a = k * sim_sps + sub * hold
            b = a + hold
            target = float(np.mean(ideal_fm[a:b]))
            if signed_dac:
                sgn = 1.0 if target >= 0.0 else -1.0
                mag_hz = abs(target)
                if quantize_reg70:
                    code = int(np.clip(round(mag_hz / HZ_PER_GAIN), 0, 127))
                    mag_hz = code * HZ_PER_GAIN
                fm[a:b] = sgn * mag_hz
            else:
                mag_hz = max(0.0, sym_sign * target)
                if quantize_reg70:
                    code = int(np.clip(round(mag_hz / HZ_PER_GAIN), 0, 127))
                    mag_hz = code * HZ_PER_GAIN
                fm[a:b] = sym_sign * mag_hz
    return fm


def rf_spectrum_metrics(fm_hz: np.ndarray, fs: float) -> Dict[str, float]:
    """FM-modulate carrier and measure ACPR (+-12.5 kHz, 9 kHz BW) & 99% OBW."""
    phase = 2.0 * np.pi * np.cumsum(fm_hz) / fs
    iq = np.exp(1j * phase)
    win = np.hanning(len(iq))
    spec = np.fft.fftshift(np.fft.fft(iq * win))
    psd = np.abs(spec) ** 2
    freqs = np.fft.fftshift(np.fft.fftfreq(len(iq), d=1.0 / fs))

    main_mask = np.abs(freqs) <= 4500.0
    adj_mask = ((freqs >= 8000.0) & (freqs <= 17000.0)) | ((freqs <= -8000.0) & (freqs >= -17000.0))
    p_main = float(np.sum(psd[main_mask]))
    p_adj = float(np.sum(psd[adj_mask]))
    acpr_db = float(10.0 * np.log10((p_adj / 2.0 + 1e-30) / (p_main + 1e-30)))

    # 99% occupied bandwidth
    order = np.argsort(np.abs(freqs))
    cum = np.cumsum(psd[order]) / float(np.sum(psd))
    idx99 = int(np.searchsorted(cum, 0.99))
    obw99_hz = float(2.0 * abs(freqs[order[min(idx99, len(freqs) - 1)]]))
    return {"acpr_12p5khz_db": round(acpr_db, 2), "obw99_hz": round(obw99_hz, 1)}


def rrc_rx_evm(fm_hz: np.ndarray, symbols: np.ndarray, sim_sps: int = 64, alpha: float = 0.5) -> float:
    """Pass FM discriminator stream through standard RRC matched filter & measure symbol EVM."""
    h = rrc_taps(sim_sps, alpha=alpha, span_syms=8)
    mf = np.convolve(fm_hz, h, mode="same")
    # Sample centered interior symbols (avoid edge transients)
    margin = 12
    off = sim_sps // 2
    rx = mf[margin * sim_sps + off : (len(symbols) - margin) * sim_sps + off : sim_sps]
    ref = symbols[margin:-margin].astype(np.float64)
    gain = float(np.dot(rx, ref) / (np.dot(ref, ref) + 1e-12))
    err = rx - gain * ref
    evm = float(np.sqrt(np.mean(err * err)) / np.sqrt(np.mean((gain * ref) ** 2)))
    return round(evm * 100.0, 2)


def run_benchmark(n_syms: int = 512, seed: int = 28) -> Dict[str, Dict[str, float]]:
    rng = np.random.default_rng(seed)
    alphabet = np.array([-3, -1, 1, 3], dtype=np.int8)
    symbols = rng.choice(alphabet, size=n_syms)
    sim_sps = 64
    fs = BAUD * sim_sps
    out: Dict[str, Dict[str, float]] = {}
    modes = (
        ("rect_1x", 1, False),
        ("rrc_4x_reg70", 4, False),
        ("rrc_8x_reg70", 8, False),
        ("rrc_4x_signed", 4, True),
        ("rrc_8x_signed", 8, True),
        ("ideal_rrc", 0, True),
    )
    for label, osr, signed_dac in modes:
        fm = synthesize_4fsk_fm(symbols, sim_sps=sim_sps, write_osr=osr, signed_dac=signed_dac)
        m = rf_spectrum_metrics(fm, fs)
        m["rrc_evm_pct"] = rrc_rx_evm(fm, symbols, sim_sps=sim_sps)
        m["writes_per_sec"] = int(BAUD * osr) if osr > 0 else 0
        m["write_budget_us"] = round(1e6 / (BAUD * osr), 1) if osr > 0 else 0.0
        out[label] = m
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-syms", type=int, default=512)
    ap.add_argument("--out", help="Optional JSON output path")
    args = ap.parse_args()
    res = run_benchmark(n_syms=args.n_syms)
    text = json.dumps(res, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
