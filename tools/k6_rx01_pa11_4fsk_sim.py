#!/usr/bin/env python3
"""Offline physics & DSP simulator for RX-01 (#24): PA11 flat-discriminator 4-FSK RX.

Models the exact published K6 audio feedback network from FD6818B AF_OUT to PA11:
  AF_OUT --[2.2k]-- VOICE_IN --[1uF]--[22k]--+--[4.7k]--+--[4.7k]-- PA11 (delta-sigma)
                                             |          |
                                           22 nF      3.9 nF
                                             |          |
                                            GND        GND

Evaluates 4800-baud 4-FSK (YSF/DMR-style +-1944 Hz / +-648 Hz, RRC alpha=0.5)
sampled at PA11's measured 17.8 kS/s (w=90, hold=3) with CIC sinc^2 droop and
measured converter floor (0.00017 FS rms vs 0.051 FS RX hiss), comparing:
  1. `raw_pa11`: flat REG_2B | 0x0700, PA11 RC ladder, NO digital EQ
  2. `stock_reg2b_eq1`: stock REG_2B (300 Hz HPF + de-emphasis + 3 kHz LPF) + PA11 + 1-zero EQ
  3. `eq1_fixed_pa11`: flat REG_2B | 0x0700, PA11 RC ladder + 1-zero Cortex-M0 fixed-point EQ
     (`y[n] = x[n] - (alpha_q8 / 256) * x[n-1]`)
  4. `ideal_disc`: ideal flat FM discriminator (no PA11 RC ladder)
  vs Flux's measured GP0 1-bit IF hard-limited floor (-79 dBm).
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, List, Tuple

import numpy as np

BAUD = 4800.0
OUTER_HZ = 1944.0
FS_PA11 = 17800.0        # Measured pa11adc.h rate at w=90, hold=3
IF_BW_HZ = 12500.0       # Narrow FM IF bandwidth
RX_NF_DB = 6.0           # Nominal receiver noise figure (offline model assumption)
GP0_FLOOR_DBM = -79.0    # Flux measured GP0 1-bit IF noise floor
PA11_ADC_FLOOR_RMS = 0.00017  # Measured muted PA11 noise (FS rms, 300-3000 Hz)
PA11_AF_FS_HZ = 3500.0   # FM deviation mapped to ~0.35 FS on PA11 (headroom below clip)


def rrc_taps(sps: int, alpha: float = 0.5, span_syms: int = 8) -> np.ndarray:
    n = span_syms * sps
    t = (np.arange(n + 1, dtype=np.float64) - n / 2.0) / float(sps)
    h = np.zeros_like(t)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-12:
            h[i] = 1.0 - alpha + 4.0 * alpha / np.pi
        elif abs(abs(ti) - 1.0 / (4.0 * alpha)) < 1e-9:
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


def pa11_rc_transfer(freqs_hz: np.ndarray) -> np.ndarray:
    """Exact complex transfer function from AF_OUT voltage to PA11 current node.

    Circuit (pa11adc.h):
      AF_OUT --[Z0 = 2.2k + 22k + 1/(jw*1uF)]-- V1 --[R2 = 4.7k]-- V2 --[R3 = 4.7k]-- PA11(virtual AC gnd)
                                                 |                  |
                                               C1=22nF            C2=3.9nF
                                                 |                  |
                                                GND                GND
    Normalized to passband DC (ignoring 1 uF blocking cap at 100 Hz) = 1.0 at midband
    so relative attenuation vs frequency is directly readable.
    """
    w = 2.0 * np.pi * freqs_hz
    jw = 1j * w
    r0 = 2200.0 + 22000.0
    c_blk = 1.0e-6
    c1 = 22.0e-9
    r2 = 4700.0
    c2 = 3.9e-9
    r3 = 4700.0

    # Handle DC safely without divide-by-zero warning (1 uF blocks exact DC)
    nz = np.abs(w) >= 1e-9
    jw_safe = np.where(nz, jw, 1j)
    z0 = np.where(nz, r0 + 1.0 / (jw_safe * c_blk), 1e12 + 0j)
    y0 = 1.0 / z0
    y2 = 1.0 / r2
    y3 = 1.0 / r3

    # Nodal equations at V1 and V2 (PA11 delta-sigma holds V_pa11 near V_th -> AC ground):
    # (y0 + jw*c1 + y2) * V1 - y2 * V2 = y0 * Vin
    # -y2 * V1 + (y2 + jw*c2 + y3) * V2 = 0
    a11 = y0 + jw * c1 + y2
    a22 = y2 + jw * c2 + y3
    det = a11 * a22 - y2 * y2
    v2_over_vin = (y0 * y2) / det

    # Normalize to the flat passband (ignoring C_blk) so low-freq gain = 1.0
    y0_dc = 1.0 / r0
    dc_gain = (y0_dc * y2) / ((y0_dc + y2) * (y2 + y3) - y2 * y2)
    return v2_over_vin / dc_gain


def stock_reg2b_transfer(freqs_hz: np.ndarray) -> np.ndarray:
    """Approximate stock BK4829 FM voice audio chain (REG_2B=0):
    300 Hz 2nd-order HPF + 750 us de-emphasis (~212 Hz) + 3 kHz 2nd-order LPF."""
    jw = 1j * 2.0 * np.pi * freqs_hz
    w_hp = 2.0 * np.pi * 300.0
    w_de = 2.0 * np.pi * 300.0
    w_lp = 2.0 * np.pi * 3000.0
    h_hp = (jw * jw) / (jw * jw + np.sqrt(2.0) * w_hp * jw + w_hp * w_hp)
    h_de = 1.0 / (1.0 + jw / w_de)
    h_lp = (w_lp * w_lp) / (jw * jw + np.sqrt(2.0) * w_lp * jw + w_lp * w_lp)
    # Normalize around 300 Hz reference
    h = h_hp * h_de * h_lp
    ref_idx = int(np.argmin(np.abs(np.abs(freqs_hz) - 300.0)))
    scale = abs(h[ref_idx]) + 1e-12
    return h / scale


def cic2_droop(freqs_hz: np.ndarray, fs: float) -> np.ndarray:
    """2nd-order CIC triangle decimator magnitude response sinc^2(f / fs)."""
    x = freqs_hz / fs
    return np.sinc(x) ** 2


def measure_pa11_rc_profile() -> Dict[str, float]:
    """Return exact PA11 RC ladder magnitude/phase at key 4-FSK frequencies."""
    probe_f = np.array([50.0, 100.0, 300.0, 600.0, 1200.0, 2400.0, 3600.0])
    h = pa11_rc_transfer(probe_f)
    db = 20.0 * np.log10(np.maximum(np.abs(h), 1e-12))
    # Dominant pole frequency (-3 dB relative to 50 Hz plateau)
    f_grid = np.linspace(50.0, 5000.0, 4951)
    h_grid = np.abs(pa11_rc_transfer(f_grid))
    peak = float(np.max(h_grid))
    i_3db = int(np.argmax((f_grid > 100.0) & (h_grid <= peak / np.sqrt(2.0))))
    fp_hz = float(f_grid[i_3db])
    alpha_opt = float(np.exp(-2.0 * np.pi * fp_hz / FS_PA11))
    alpha_q8 = int(round(alpha_opt * 256.0))
    return {
        "dominant_pole_hz": round(fp_hz, 1),
        "recommended_alpha_q8": alpha_q8,
        "attenuation_300hz_db": round(float(db[2]), 2),
        "attenuation_1200hz_db": round(float(db[4]), 2),
        "attenuation_2400hz_db": round(float(db[5]), 2),
        "attenuation_3600hz_db": round(float(db[6]), 2),
        "drop_300_to_2400hz_db": round(float(db[2] - db[5]), 2),
    }


def rf_power_to_if_snr_db(pin_dbm: float) -> float:
    """RF input power (dBm) -> 12.5 kHz IF SNR (dB), offline thermal+NF model."""
    noise_floor_dbm = -174.0 + 10.0 * np.log10(IF_BW_HZ) + RX_NF_DB
    return pin_dbm - noise_floor_dbm


def simulate_4fsk_ser(
    pin_dbm_list: List[float],
    *,
    n_syms: int = 2000,
    seed: int = 24,
    alpha_q8: int | None = None,
) -> Dict[str, object]:
    """Simulate 4800-baud 4-FSK through FM discriminator + PA11 + optional 1-zero EQ."""
    rng = np.random.default_rng(seed)
    alphabet = np.array([-3, -1, 1, 3], dtype=np.int8)
    symbols = rng.choice(alphabet, size=n_syms)

    # High-rate continuous RF baseband at 16 * 4800 = 76.8 kHz
    osr_rf = 16
    fs_rf = BAUD * osr_rf
    ups = np.zeros(n_syms * osr_rf, dtype=np.float64)
    ups[osr_rf // 2 :: osr_rf] = symbols.astype(np.float64) * (OUTER_HZ / 3.0)
    h_tx = rrc_taps(osr_rf, alpha=0.5, span_syms=8)
    fm_tx = np.convolve(ups, h_tx, mode="same") * np.sqrt(osr_rf)
    h_dc = float(np.sum(h_tx)) * np.sqrt(osr_rf) / osr_rf
    fm_tx /= h_dc
    phase_tx = 2.0 * np.pi * np.cumsum(fm_tx) / fs_rf
    iq_clean = np.exp(1j * phase_tx)

    # IF channel filter (+/- 6.25 kHz brick-wall raised cosine)
    freqs_rf = np.fft.fftfreq(len(iq_clean), d=1.0 / fs_rf)
    if_mask = np.where(np.abs(freqs_rf) <= IF_BW_HZ / 2.0, 1.0, 0.0)
    # Smooth skirt at IF edge to avoid time-domain ringing
    trans = (np.abs(freqs_rf) > 5500.0) & (np.abs(freqs_rf) < 7500.0)
    if_mask[trans] = 0.5 * (1.0 + np.cos(np.pi * (np.abs(freqs_rf[trans]) - 5500.0) / 2000.0))

    rc_prof = measure_pa11_rc_profile()
    if alpha_q8 is None:
        alpha_q8 = int(rc_prof["recommended_alpha_q8"])

    # Frequency-domain analog responses on the PA11 sampled stream (17.8 kS/s)
    t_rf = np.arange(len(iq_clean), dtype=np.float64) / fs_rf
    n_pa11 = int(np.floor(t_rf[-1] * FS_PA11))
    t_pa11 = np.arange(n_pa11, dtype=np.float64) / FS_PA11
    freqs_p = np.fft.rfftfreq(n_pa11, d=1.0 / FS_PA11)

    h_pa11_rc = pa11_rc_transfer(freqs_p) * cic2_droop(freqs_p, FS_PA11)
    h_stock = stock_reg2b_transfer(freqs_p) * h_pa11_rc
    # 1-zero Cortex-M0 fixed-point EQ: y[n] = x[n] - (alpha_q8 / 256) * x[n-1]
    a_coef = alpha_q8 / 256.0
    h_eq1 = 1.0 - a_coef * np.exp(-1j * 2.0 * np.pi * freqs_p / FS_PA11)

    # Resample RX matched filter grid back to symbol centers (4800 Hz, 4 sps = 19.2 kHz)
    osr_rx = 4
    fs_rx = BAUD * osr_rx
    t_rx = np.arange(n_syms * osr_rx, dtype=np.float64) / fs_rx
    h_rx = rrc_taps(osr_rx, alpha=0.5, span_syms=8)
    margin = 20
    ref_syms = symbols[margin:-margin].astype(np.float64)

    def demod_ser(y_pa11: np.ndarray) -> float:
        # Interpolate 17.8 kS/s -> 19.2 kS/s, RRC match-filter, search fine timing lag (+/- 20 samples)
        y_rx = np.interp(t_rx, t_pa11, y_pa11)
        mf = np.convolve(y_rx, h_rx, mode="same")
        best_ser = 1.0
        for lag in range(-12, 13):
            idx = margin * osr_rx + osr_rx // 2 + lag + np.arange(len(ref_syms)) * osr_rx
            if idx[0] < 0 or idx[-1] >= len(mf):
                continue
            s_hat = mf[idx]
            # Affine gain/offset fit (carrier freq offset + amplitude AGC)
            s_hat = s_hat - np.mean(s_hat)
            gain = float(np.dot(s_hat, ref_syms) / (np.dot(s_hat, s_hat) + 1e-12))
            scaled = s_hat * gain
            # 4-FSK hard slicing at thresholds -2, 0, +2
            dec = np.where(scaled < -2.0, -3, np.where(scaled < 0.0, -1, np.where(scaled < 2.0, 1, 3)))
            ser = float(np.mean(dec != ref_syms))
            if ser < best_ser:
                best_ser = ser
        return round(best_ser, 5)

    curves: Dict[str, List[Dict[str, float]]] = {
        "raw_pa11": [],
        "stock_reg2b_eq1": [],
        "eq1_fixed_pa11": [],
        "ideal_disc": [],
    }

    for pin_dbm in pin_dbm_list:
        if_snr_db = rf_power_to_if_snr_db(pin_dbm)
        # Complex AWGN scaled to IF bandwidth (noise power inside IF_BW_HZ = 10^(-snr/10))
        noise_var_total = (10.0 ** (-if_snr_db / 10.0)) * (fs_rf / IF_BW_HZ)
        sigma = np.sqrt(noise_var_total / 2.0)
        noise = sigma * (rng.standard_normal(len(iq_clean)) + 1j * rng.standard_normal(len(iq_clean)))
        iq_rx = np.fft.ifft(np.fft.fft(iq_clean + noise) * if_mask)

        # Ideal FM quadrature discriminator -> instantaneous frequency (Hz)
        dphi = np.angle(iq_rx[1:] * np.conj(iq_rx[:-1]))
        disc_hz = np.concatenate(([0.0], dphi * (fs_rf / (2.0 * np.pi))))

        # Anti-alias low-pass before sampling at 17.8 kS/s (audio bandwidth ~8 kHz)
        aa_mask = np.where(np.abs(freqs_rf) <= 8000.0, 1.0, 0.0)
        disc_aa = np.real(np.fft.ifft(np.fft.fft(disc_hz) * aa_mask))
        disc_p = np.interp(t_pa11, t_rf, disc_aa)
        spec_p = np.fft.rfft(disc_p)

        # PA11 converter self-noise (0.00017 FS rms, flat white across Nyquist 8.9 kHz)
        adc_noise_hz = rng.standard_normal(n_pa11) * (PA11_ADC_FLOOR_RMS * PA11_AF_FS_HZ / 0.35)

        y_ideal = disc_p
        y_raw = np.fft.irfft(spec_p * h_pa11_rc, n=n_pa11) + adc_noise_hz
        y_eq1 = np.fft.irfft(np.fft.rfft(y_raw) * h_eq1, n=n_pa11)
        y_stock_raw = np.fft.irfft(spec_p * h_stock, n=n_pa11) + adc_noise_hz
        y_stock_eq1 = np.fft.irfft(np.fft.rfft(y_stock_raw) * h_eq1, n=n_pa11)

        for name, sig in (
            ("raw_pa11", y_raw),
            ("stock_reg2b_eq1", y_stock_eq1),
            ("eq1_fixed_pa11", y_eq1),
            ("ideal_disc", y_ideal),
        ):
            curves[name].append({
                "pin_dbm": float(pin_dbm),
                "if_snr_db": round(if_snr_db, 2),
                "ser": demod_ser(sig),
            })

    def sensitivity_1pct(rows: List[Dict[str, float]]) -> float | None:
        for r in rows:
            if r["ser"] <= 0.01:
                return float(r["pin_dbm"])
        return None

    summary = {
        name: {
            "sens_1pct_dbm": sensitivity_1pct(rows),
            "ser_at_high_snr": rows[-1]["ser"],
            "gain_vs_gp0_db": (
                round(GP0_FLOOR_DBM - sensitivity_1pct(rows), 1)
                if sensitivity_1pct(rows) is not None
                else None
            ),
        }
        for name, rows in curves.items()
    }

    return {
        "rc_profile": rc_prof,
        "alpha_q8": alpha_q8,
        "gp0_measured_floor_dbm": GP0_FLOOR_DBM,
        "summary": summary,
        "curves": curves,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-syms", type=int, default=2000)
    ap.add_argument("--out", help="Optional JSON output path")
    args = ap.parse_args()
    pins = [-122.0, -120.0, -118.0, -116.0, -114.0, -112.0, -110.0, -106.0, -100.0, -90.0]
    res = simulate_4fsk_ser(pins, n_syms=args.n_syms)
    text = json.dumps(res, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
