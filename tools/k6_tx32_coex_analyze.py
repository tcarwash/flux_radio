#!/usr/bin/env python3
"""TX-32 Live-PTT Coexistence Analyzer & Receive-Only RTL-SDR Capture Tool.

Analyzes simultaneous K6 PA11 Goertzel logs (`tx32 s=...`) and air-coupled
RTL-SDR IQ captures across the 6 TX-32 slots:
  S0: PA OFF baseline (PA11 only)
  S1: PA ON, FSK OFF, REG_50 stock          (PA-ON RF check + baseline 1300 Hz FM dev)
  S2: PA ON, FSK OFF, REG_50 |= 0x8000      (pure-carrier REG_50[15] mute check)
  S3: PA ON, FSK ON (2x 33w), REG_50 stock  (primary live-PTT coexistence check)
  S4: PA ON, FSK ON (2x 33w), REG_50|0x8000 (live-PTT + REG_50[15] mute check)
  S5: PA ON, FSK ON (2x 33w), TX filt=0     (fallback TX audio filter zeroing check)

Blind FSK decoding uses ONLY the public 16x0x55 + 0x076b3e36 header and the
Flux L64 whitened packet CRC (offset 0 or 2) -- never known TX payload bytes.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import pathlib
import re
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modem import pager  # noqa: E402

DEFAULT_CENTER_HZ = 433_750_000
DEFAULT_RF_HZ = 433_500_000
DEFAULT_FS_HZ = 1_024_000
BASEBAND_FS = 48_000.0          # 10x oversampling of 4800-bps native FSK
FSK_BAUD = 4800.0
PHYSICAL_BYTES = 66
PACKET_BYTES = 64
GUARD_WORD = 0xA55A

# Public 16x0x55 preamble + 0x076b3e36 sync (160 bits, MSB first per byte)
PUBLIC_HEADER_BYTES = bytes([0x55] * 16 + [0x07, 0x6B, 0x3E, 0x36])
PUBLIC_HEADER_BITS = np.unpackbits(np.frombuffer(PUBLIC_HEADER_BYTES, dtype=np.uint8)).astype(np.int8)
PUBLIC_HEADER_NRZ = (2 * PUBLIC_HEADER_BITS - 1).astype(np.float64)

# Public FSK-13 verified 33-word guarded L64 packets (for offline synthetic IQ generation only)
SYNTH_FSK_WORDS: List[List[int]] = [
    [
        0xa856, 0x9add, 0xdeec, 0xa633, 0xb07f, 0x37dd, 0x8c50, 0x1a2b,
        0xbb79, 0x7b08, 0x8290, 0x969f, 0x437c, 0x94da, 0xc79d, 0x84d1,
        0x4d77, 0xd086, 0xfbb2, 0x54da, 0x6f99, 0x9cda, 0x5af0, 0x840a,
        0x5872, 0xf482, 0x9ce2, 0xcd8a, 0xd1e7, 0x094e, 0x1732, 0xe5e8,
        0xa55a,
    ],
    [
        0xa856, 0x9bdd, 0xdeec, 0x3228, 0x684e, 0xa4f6, 0x8055, 0x9aab,
        0xb170, 0xc41f, 0x552a, 0xda9e, 0x4f55, 0x3263, 0x08ae, 0x21ce,
        0x07e3, 0x1e97, 0xb6e5, 0x5183, 0xb9ec, 0xc6ec, 0xf6ee, 0xe83b,
        0xe861, 0x44f1, 0xe9d7, 0xcd8a, 0xd1e7, 0x094e, 0x1732, 0x4924,
        0xa55a,
    ],
    [
        0xa856, 0x98dd, 0xdeec, 0xfd44, 0xf6ef, 0x9487, 0xd467, 0xeeaa,
        0x8c47, 0xea20, 0x97bb, 0x8799, 0x664c, 0x1adc, 0xe5e8, 0x7ab3,
        0x7a13, 0xcbf0, 0x0beb, 0x806f, 0xc8e9, 0xee42, 0xb6a4, 0x5f7c,
        0x212c, 0xb7a9, 0x3592, 0xcd8a, 0xd1e7, 0x094e, 0x1732, 0xaddd,
        0xa55a,
    ],
    [
        0xa856, 0x99dd, 0xdeec, 0xfd56, 0x0c25, 0x388e, 0x4c14, 0x8830,
        0x5500, 0x854b, 0x9969, 0xd729, 0x0fca, 0xdb2f, 0x991b, 0xcfbb,
        0xc3fe, 0x6137, 0x5021, 0x9486, 0x2dae, 0xacf7, 0xc54e, 0x3c3c,
        0xf740, 0x8fb9, 0x0be4, 0xcd8a, 0xd1e7, 0x094e, 0x1732, 0x563d,
        0xa55a,
    ],
    [
        0xa856, 0x9edd, 0xdeec, 0x103f, 0x3bdf, 0x2ec1, 0x7616, 0xc8ba,
        0xfa3a, 0x918c, 0x3376, 0x46ab, 0x757e, 0x6e5f, 0xa99d, 0x3afc,
        0x90e6, 0x84b4, 0xa3ab, 0x289e, 0x4aff, 0x6e97, 0x1921, 0xab44,
        0x2121, 0x727a, 0x8ea5, 0xcd8a, 0xd1e7, 0x094e, 0x1732, 0x35d8,
        0xa55a,
    ],
    [
        0xa856, 0x9fdd, 0xdeec, 0xf793, 0x5a23, 0xa3e6, 0xb742, 0xd710,
        0x3860, 0xec32, 0xdbb4, 0xfaa8, 0x8b28, 0x567d, 0x70fa, 0xceab,
        0xace9, 0xeec1, 0xbdf3, 0x2d97, 0x3bd6, 0x5795, 0xfb3a, 0x7c43,
        0x9e22, 0x2d6d, 0x53a3, 0xcd8a, 0xd1e7, 0x094e, 0x1732, 0x4637,
        0xa55a,
    ],
]


def load_iq(path: pathlib.Path) -> np.ndarray:
    """Load RTL-SDR IQ from .npy (uint8 or complex64), .cu8, or .cf32."""
    suf = path.suffix.lower()
    if suf == ".npy":
        arr = np.load(path)
        if arr.dtype == np.uint8:
            z = (arr[0::2].astype(np.float32) - 127.5) + 1j * (arr[1::2].astype(np.float32) - 127.5)
            return (z / 127.5).astype(np.complex64)
        return np.asarray(arr, dtype=np.complex64)
    if suf == ".cu8":
        raw = np.fromfile(path, dtype=np.uint8)
        z = (raw[0::2].astype(np.float32) - 127.5) + 1j * (raw[1::2].astype(np.float32) - 127.5)
        return (z / 127.5).astype(np.complex64)
    if suf == ".cf32":
        return np.fromfile(path, dtype=np.complex64)
    raise ValueError(f"Unsupported IQ format: {path}")


def channelize_and_fm_demod(
    iq: np.ndarray,
    *,
    fs_hz: float,
    center_hz: float,
    rf_hz: float,
    out_fs: float = BASEBAND_FS,
) -> Tuple[np.ndarray, np.ndarray]:
    """Mix target RF to DC, decimate to `out_fs`, return (env_power, fm_hz)."""
    iq = np.asarray(iq, dtype=np.complex64)
    shift_hz = rf_hz - center_hz
    if abs(shift_hz) > 1.0:
        n = np.arange(len(iq), dtype=np.float64)
        iq = (iq * np.exp(-2j * np.pi * shift_hz * n / fs_hz)).astype(np.complex64)

    # Resample cleanly to out_fs (48 kHz)
    up = int(round(out_fs))
    down = int(round(fs_hz))
    g = math.gcd(up, down)
    bb = signal.resample_poly(iq, up // g, down // g).astype(np.complex64)

    # Channel low-pass filter (+/- 10 kHz)
    taps = signal.firwin(129, 10_000.0, fs=out_fs)
    bb = signal.lfilter(taps, 1.0, bb).astype(np.complex64)

    env_pwr = (np.abs(bb) ** 2).astype(np.float64)
    dphi = np.angle(bb[1:] * np.conj(bb[:-1]))
    fm_hz = np.concatenate(([0.0], dphi * (out_fs / (2.0 * np.pi))))
    return env_pwr, fm_hz


def segment_rf_bursts(env_pwr: np.ndarray, fs: float = BASEBAND_FS, min_dur_s: float = 0.12) -> List[Tuple[int, int]]:
    """Find contiguous PA-ON RF carrier bursts using a robust envelope threshold."""
    win = max(1, int(round(0.004 * fs)))
    smooth = np.convolve(env_pwr, np.ones(win) / win, mode="same")
    hi = float(np.percentile(smooth, 95))
    lo = float(np.percentile(smooth, 10))
    if hi <= lo * 4.0 + 1e-12:
        return []
    thresh = lo + 0.18 * (hi - lo)
    active = smooth >= thresh
    edges = np.diff(active.astype(np.int8), prepend=0, append=0)
    starts = np.where(edges == 1)[0]
    ends = np.where(edges == -1)[0]
    min_samp = int(round(min_dur_s * fs))
    return [(int(a), int(b)) for a, b in zip(starts, ends) if (b - a) >= min_samp]


def measure_fm_tone_hz(fm_hz: np.ndarray, fs: float, target_hz: float = 1300.0) -> Dict[str, float]:
    """Measure peak FM deviation (Hz) of a narrowband tone and adjacent noise floor."""
    # Trim 15 ms at edges to avoid PA ramp transients
    trim = min(len(fm_hz) // 8, int(round(0.015 * fs)))
    x = fm_hz[trim : len(fm_hz) - trim] if len(fm_hz) > 2 * trim else fm_hz
    x = x - float(np.mean(x))
    n = len(x)
    if n < 64:
        return {"tone_dev_hz": 0.0, "neigh_dev_hz": 0.0, "tone_snr_db": 0.0}
    win = np.hanning(n)
    w_sum = float(np.sum(win))
    t = np.arange(n, dtype=np.float64) / fs

    def amp_at(freq: float) -> float:
        c = np.sum(x * win * np.exp(-2j * np.pi * freq * t))
        return float(2.0 * abs(c) / (w_sum + 1e-12))

    # Fine search +/- 15 Hz around target_hz to tolerate small speaker/clock offset
    peak_dev = max(amp_at(target_hz + df) for df in (-12.0, -6.0, 0.0, 6.0, 12.0))
    neigh = max(amp_at(1150.0), amp_at(1450.0))
    snr_db = float(20.0 * np.log10((peak_dev + 1e-6) / (neigh + 1e-6)))
    return {
        "tone_dev_hz": round(peak_dev, 1),
        "neigh_dev_hz": round(neigh, 1),
        "tone_snr_db": round(snr_db, 2),
    }


def select_l64(payload: bytes) -> Dict[str, object]:
    """Blind CRC-validated Flux L64 check at offset 0 or 2 (handles 0x00F8 prefix)."""
    if len(payload) != PHYSICAL_BYTES:
        return {"valid": False, "ambiguous": False}
    prof = pager.profile("L", "1200", depth=1, parity=False, short_last=True)
    matches = []
    for off in (0, 2):
        cand = payload[off : off + PACKET_BYTES]
        pkt = prof.unpack(cand)
        if pkt is not None:
            matches.append((off, cand, pkt))
    if len(matches) != 1:
        return {"valid": False, "ambiguous": len(matches) > 1, "first_word_le": payload[:2].hex()}
    off, cand, pkt = matches[0]
    return {
        "valid": True,
        "ambiguous": False,
        "offset_bytes": off,
        "displaced_prefix": off == 2,
        "first_word_le": payload[:2].hex(),
        "packet_sha256": hashlib.sha256(cand).hexdigest(),
        "msg_id": pkt["msg_id"],
        "seq": pkt["seq"],
        "codec_frames": len(pkt["frames"]),
    }


def decode_guarded_fsk_burst(fm_hz: np.ndarray, fs: float = BASEBAND_FS) -> List[Dict[str, object]]:
    """Blindly acquire and CRC-decode 4800-bps guarded 33-word L64 frames in one burst."""
    sps = int(round(fs / FSK_BAUD))
    assert abs(sps * FSK_BAUD - fs) < 1e-6
    x = fm_hz - float(np.mean(fm_hz))
    # Matched boxcar filter over 1 symbol period
    mf = np.convolve(x, np.ones(sps, dtype=np.float64) / sps, mode="same")
    # Upsampled public header template (160 bits * sps)
    tmpl = np.repeat(PUBLIC_HEADER_NRZ, sps)
    t_len = len(tmpl)
    payload_bits = PHYSICAL_BYTES * 8
    total_len = t_len + payload_bits * sps
    if len(mf) < total_len:
        return []

    # Normalized correlation across burst
    num = np.correlate(mf, tmpl, mode="valid")
    e_sig = np.correlate(mf * mf, np.ones(t_len), mode="valid")
    e_t = float(np.dot(tmpl, tmpl))
    corr = num / (np.sqrt(np.maximum(e_sig, 1e-12) * e_t))

    found: List[Dict[str, object]] = []
    min_gap = int(0.85 * total_len)
    used_until = -1
    while True:
        abs_c = np.abs(corr)
        if used_until >= 0:
            abs_c[: min(used_until, len(abs_c))] = 0.0
        idx = int(np.argmax(abs_c))
        peak = float(abs_c[idx])
        if peak < 0.72:
            break
        pol = 1.0 if corr[idx] >= 0.0 else -1.0
        # Try small fine phase offsets around peak (+/- 2 samples) and accept only unique CRC
        best_dec = None
        for d in (0, -1, 1, -2, 2):
            p0 = idx + t_len + d + sps // 2
            p_end = p0 + (payload_bits - 1) * sps
            if p0 < 0 or p_end >= len(mf):
                continue
            syms = pol * mf[p0 : p0 + payload_bits * sps : sps]
            bits = (syms > 0.0).astype(np.uint8)
            raw = np.packbits(bits).tobytes()
            dec = select_l64(raw)
            if dec["valid"]:
                dec["sync_corr"] = round(peak, 4)
                dec["polarity"] = int(pol)
                best_dec = dec
                break
        if best_dec is not None:
            found.append(best_dec)
        else:
            found.append({"valid": False, "sync_corr": round(peak, 4), "polarity": int(pol)})
        used_until = idx + min_gap
        if used_until >= len(corr):
            break
    return found


def parse_k6_log(lines: List[str]) -> Dict[int, Dict[str, float]]:
    """Parse `tx32 s=N g13=... gn=... g12=... v=...` lines from K6 serial output."""
    out: Dict[int, Dict[str, float]] = {}
    pat = re.compile(
        r"tx32\s+s=(\d+)\s+g13=([0-9a-fA-F]+)\s+gn=([0-9a-fA-F]+)\s+g12=([0-9a-fA-F]+)\s+v=([0-9a-fA-F]+)"
    )
    for line in lines:
        m = pat.search(line)
        if not m:
            continue
        s = int(m.group(1))
        g13 = int(m.group(2), 16)
        gn = int(m.group(3), 16)
        g12 = int(m.group(4), 16)
        var = int(m.group(5), 16)
        contrast_db = float(10.0 * np.log10((g13 + 1.0) / (gn + 1.0)))
        fsk_bleed_db = float(10.0 * np.log10((g12 + 1.0) / (g13 + 1.0)))
        out[s] = {
            "g13": g13,
            "gn": gn,
            "g12": g12,
            "var": var,
            "contrast_1300_db": round(contrast_db, 2),
            "fsk_1200_rel_db": round(fsk_bleed_db, 2),
        }
    return out


def analyze_coexistence(
    iq: np.ndarray,
    *,
    fs_hz: float = DEFAULT_FS_HZ,
    center_hz: float = DEFAULT_CENTER_HZ,
    rf_hz: float = DEFAULT_RF_HZ,
    k6_lines: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Run complete TX-32 coexistence analysis and emit an unambiguous verdict."""
    env, fm_hz = channelize_and_fm_demod(iq, fs_hz=fs_hz, center_hz=center_hz, rf_hz=rf_hz)
    bursts = segment_rf_bursts(env, fs=BASEBAND_FS)
    rf_slots: Dict[int, Dict[str, object]] = {}

    # Map detected PA-ON bursts in order to S1..S5
    for idx, (a, b) in enumerate(bursts[:5]):
        slot_id = idx + 1
        seg = fm_hz[a:b]
        tone = measure_fm_tone_hz(seg, BASEBAND_FS, 1300.0)
        entry: Dict[str, object] = {
            "start_s": round(a / BASEBAND_FS, 4),
            "dur_s": round((b - a) / BASEBAND_FS, 4),
            **tone,
        }
        if slot_id >= 3:
            frames = decode_guarded_fsk_burst(seg, BASEBAND_FS)
            entry["fsk_frames_found"] = len(frames)
            entry["fsk_crc_valid"] = sum(1 for f in frames if f.get("valid"))
            entry["fsk_frames"] = frames
        rf_slots[slot_id] = entry

    s1_dev = float(rf_slots.get(1, {}).get("tone_dev_hz", 0.0))
    for sid, entry in rf_slots.items():
        dev = float(entry["tone_dev_hz"])
        if s1_dev > 50.0:
            entry["mic_bleed_suppression_db"] = round(
                float(20.0 * np.log10((s1_dev + 1e-6) / (dev + 1e-6))), 2
            )
        else:
            entry["mic_bleed_suppression_db"] = None

    k6_slots = parse_k6_log(k6_lines or [])

    # Decision matrix
    verdict = "INCOMPLETE_DATA"
    reason = "Insufficient RF bursts or K6 PA11 lines"
    if 0 in k6_slots and 1 in rf_slots:
        c0 = k6_slots[0]["contrast_1300_db"]
        c1 = k6_slots.get(1, {}).get("contrast_1300_db", 0.0)
        if c0 < 12.0:
            verdict = "BASELINE_NO_TONE"
            reason = f"S0 PA-OFF baseline contrast too low ({c0:.1f} dB < 12 dB)"
        elif c1 < 10.0:
            verdict = "PA_INTERFERENCE_ON_PA11"
            reason = f"PA-ON swamps PA11 (S0={c0:.1f} dB -> S1={c1:.1f} dB)"
        else:
            def slot_ok(sid: int) -> Tuple[bool, bool, bool]:
                pa_ok = k6_slots.get(sid, {}).get("contrast_1300_db", 0.0) >= 12.0
                r = rf_slots.get(sid, {})
                crc_ok = int(r.get("fsk_crc_valid", 0)) >= 2
                sup = r.get("mic_bleed_suppression_db")
                bleed_ok = (sup is not None and float(sup) >= 18.0) or float(r.get("tone_dev_hz", 9999.0)) < 120.0
                return pa_ok, crc_ok, bleed_ok

            pa3, crc3, b3 = slot_ok(3)
            pa4, crc4, b4 = slot_ok(4)
            pa5, crc5, b5 = slot_ok(5)
            if pa3 and crc3 and b3:
                verdict = "LIVE_PTT_DIRECT"
                reason = "S3 passed PA11 1300 Hz + 2/2 guarded L64 CRC + clean RF (no extra mute needed)"
            elif pa4 and crc4 and b4:
                verdict = "LIVE_PTT_REG50_MUTE"
                reason = "S4 passed PA11 1300 Hz + 2/2 guarded L64 CRC with REG_50[15]=1 mute"
            elif pa5 and crc5 and b5:
                verdict = "LIVE_PTT_TXFILT_MUTE"
                reason = "S5 passed PA11 1300 Hz + 2/2 guarded L64 CRC with TX audio filter zeroing"
            elif not (pa3 or pa4 or pa5):
                verdict = "FSK_HIJACKS_AFOUT"
                reason = "PA11 1300 Hz mic signal disappeared during native FSK TX (S3/S4/S5)"
            else:
                verdict = "MIC_UNMUTABLE_ON_FM"
                reason = "PA11 survived FSK TX, but analog mic bleed or FSK CRC failed across S3/S4/S5"

    return {
        "verdict": verdict,
        "reason": reason,
        "bursts_detected": len(bursts),
        "rf_slots": rf_slots,
        "k6_pa11_slots": k6_slots,
    }


def _words_to_fsk_fm(words: List[int], *, fs: float = BASEBAND_FS, dev_hz: float = 2100.0,
                     displace_00f8: bool = False) -> np.ndarray:
    """Build realistic 4800-bps 2-FSK FM deviation stream for one 33-word frame."""
    if displace_00f8:
        tx_words = [0x00F8] + words[:32]
    else:
        tx_words = list(words)
    payload_bytes = b"".join(int(w).to_bytes(2, "little") for w in tx_words)
    frame_bytes = PUBLIC_HEADER_BYTES + payload_bytes
    bits = np.unpackbits(np.frombuffer(frame_bytes, dtype=np.uint8))
    nrz = (2 * bits.astype(np.float64) - 1.0) * dev_hz
    sps = int(round(fs / FSK_BAUD))
    return np.repeat(nrz, sps)


def synthesize_scenario(
    scenario: str,
    *,
    fs_iq: float = 240_000.0,
    center_hz: float = DEFAULT_CENTER_HZ,
    rf_hz: float = DEFAULT_RF_HZ,
    seed: int = 32,
) -> Tuple[np.ndarray, List[str]]:
    """Generate realistic synthetic RTL-SDR IQ + K6 serial log for any coexistence scenario."""
    rng = np.random.default_rng(seed)
    gap = np.zeros(int(round(0.080 * BASEBAND_FS)), dtype=np.complex64)
    chunks: List[np.ndarray] = [gap]

    def make_carrier(fm_hz: np.ndarray) -> np.ndarray:
        phase = 2.0 * np.pi * np.cumsum(fm_hz) / BASEBAND_FS
        return np.exp(1j * phase).astype(np.complex64)

    def tone_fm(dur_s: float, dev_hz: float) -> np.ndarray:
        t = np.arange(int(round(dur_s * BASEBAND_FS)), dtype=np.float64) / BASEBAND_FS
        return dev_hz * np.sin(2.0 * np.pi * 1300.0 * t)

    # S1: PA ON pure FM with strong 1300 Hz mic tone (1800 Hz dev)
    chunks.append(make_carrier(tone_fm(0.26, 1800.0)))
    chunks.append(gap)

    # S2: REG_50[15]=1 mute control
    s2_dev = 30.0 if scenario in ("direct", "reg50_mute") else 1800.0
    chunks.append(make_carrier(tone_fm(0.26, s2_dev)))
    chunks.append(gap)

    # S3, S4, S5: each sends 2 guarded 33-word L64 frames (odd frame displaced by 0x00F8!)
    bleed_map = {
        "direct": (25.0, 25.0, 25.0),
        "reg50_mute": (1600.0, 25.0, 1600.0),
        "txfilt_mute": (1600.0, 1600.0, 25.0),
        "pa_swamp": (25.0, 25.0, 25.0),
        "fsk_hijack": (25.0, 25.0, 25.0),
        "unmutable": (1600.0, 1600.0, 1600.0),
    }
    b3, b4, b5 = bleed_map[scenario]
    for slot_idx, bleed in ((3, b3), (4, b4), (5, b5)):
        f0 = (slot_idx - 3) * 2
        fm0 = _words_to_fsk_fm(SYNTH_FSK_WORDS[f0], displace_00f8=False)
        fm1 = _words_to_fsk_fm(SYNTH_FSK_WORDS[f0 + 1], displace_00f8=True)
        pad = np.zeros(int(round(0.015 * BASEBAND_FS)), dtype=np.float64)
        fm = np.concatenate([pad, fm0, pad, fm1, pad])
        t = np.arange(len(fm), dtype=np.float64) / BASEBAND_FS
        fm = fm + bleed * np.sin(2.0 * np.pi * 1300.0 * t)
        chunks.append(make_carrier(fm))
        chunks.append(gap)

    bb = np.concatenate(chunks)
    # Add realistic AWGN + upsample to fs_iq + shift to rf_hz - center_hz
    noise = (0.04 * (rng.standard_normal(len(bb)) + 1j * rng.standard_normal(len(bb)))).astype(np.complex64)
    bb = bb + noise
    up = int(round(fs_iq))
    down = int(round(BASEBAND_FS))
    g = math.gcd(up, down)
    iq = signal.resample_poly(bb, up // g, down // g).astype(np.complex64)
    shift_hz = rf_hz - center_hz
    if abs(shift_hz) > 1.0:
        n = np.arange(len(iq), dtype=np.float64)
        iq = (iq * np.exp(2j * np.pi * shift_hz * n / fs_iq)).astype(np.complex64)

    # Synthetic K6 PA11 Goertzel lines
    def k6_line(sid: int, strong: bool) -> str:
        g13 = 0x00080000 if strong else 0x00000400
        gn = 0x00000380
        g12 = 0x00000500
        v = 0x00012000
        return f"tx32 s={sid} g13={g13:08x} gn={gn:08x} g12={g12:08x} v={v:08x}"

    if scenario == "pa_swamp":
        flags = (True, False, False, False, False, False)
    elif scenario == "fsk_hijack":
        flags = (True, True, True, False, False, False)
    else:
        flags = (True, True, True, True, True, True)
    k6_lines = [k6_line(i, flags[i]) for i in range(6)]
    return iq, k6_lines


def capture_rtlsdr_iq(
    *,
    seconds: float,
    center_hz: int = DEFAULT_CENTER_HZ,
    fs_hz: int = DEFAULT_FS_HZ,
    gain_tenth_db: int = 0,
    device_index: int = 0,
) -> np.ndarray:
    """Receive-only RTL-SDR IQ capture via librtlsdr.so.0 (never touches serial/radio)."""
    lib = ctypes.CDLL("librtlsdr.so.0")
    lib.rtlsdr_get_device_count.restype = ctypes.c_uint
    for name, types, restype in (
        ("open", [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint], ctypes.c_int),
        ("close", [ctypes.c_void_p], ctypes.c_int),
        ("set_center_freq", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
        ("set_sample_rate", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_int),
        ("set_tuner_gain_mode", [ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
        ("set_tuner_gain", [ctypes.c_void_p, ctypes.c_int], ctypes.c_int),
        ("reset_buffer", [ctypes.c_void_p], ctypes.c_int),
        ("read_sync", [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)], ctypes.c_int),
    ):
        fn = getattr(lib, "rtlsdr_" + name)
        fn.argtypes = types
        fn.restype = restype

    count = int(lib.rtlsdr_get_device_count())
    if device_index < 0 or device_index >= count:
        raise RuntimeError(f"RTL-SDR device {device_index} not found ({count} available)")
    handle = ctypes.c_void_p()
    if lib.rtlsdr_open(ctypes.byref(handle), device_index):
        raise RuntimeError("RTL-SDR open failed (device busy or unavailable)")
    try:
        for fn_name, arg in (
            ("set_center_freq", center_hz),
            ("set_sample_rate", fs_hz),
            ("set_tuner_gain_mode", 1),
            ("set_tuner_gain", gain_tenth_db),
        ):
            if getattr(lib, "rtlsdr_" + fn_name)(handle, arg):
                raise RuntimeError(f"rtlsdr_{fn_name} failed")
        if lib.rtlsdr_reset_buffer(handle):
            raise RuntimeError("rtlsdr_reset_buffer failed")
        buf_bytes = 262_144
        nblocks = math.ceil(seconds * fs_hz * 2 / buf_bytes)
        tmp = ctypes.create_string_buffer(buf_bytes)
        got = ctypes.c_int()
        parts = []
        for _ in range(nblocks):
            if lib.rtlsdr_read_sync(handle, tmp, buf_bytes, ctypes.byref(got)) or got.value != buf_bytes:
                raise RuntimeError("RTL-SDR read_sync failed")
            parts.append(np.frombuffer(tmp.raw, dtype=np.uint8).copy())
        return np.concatenate(parts)
    finally:
        if handle.value:
            lib.rtlsdr_close(handle)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_an = sub.add_parser("analyze", help="Analyze recorded RTL-SDR IQ + optional K6 serial log")
    p_an.add_argument("--iq", required=True, type=pathlib.Path)
    p_an.add_argument("--k6-log", type=pathlib.Path)
    p_an.add_argument("--fs", type=float, default=DEFAULT_FS_HZ)
    p_an.add_argument("--center-hz", type=float, default=DEFAULT_CENTER_HZ)
    p_an.add_argument("--rf-hz", type=float, default=DEFAULT_RF_HZ)

    p_sim = sub.add_parser("sim", help="Run synthetic offline coexistence scenario")
    p_sim.add_argument(
        "--scenario",
        default="direct",
        choices=("direct", "reg50_mute", "txfilt_mute", "pa_swamp", "fsk_hijack", "unmutable"),
    )

    p_cap = sub.add_parser("capture", help="Receive-only RTL-SDR IQ capture + immediate TX-32 decode")
    p_cap.add_argument("--out", required=True, type=pathlib.Path)
    p_cap.add_argument("--seconds", type=float, default=10.0)
    p_cap.add_argument("--center-hz", type=int, default=DEFAULT_CENTER_HZ)
    p_cap.add_argument("--rf-hz", type=int, default=DEFAULT_RF_HZ)
    p_cap.add_argument("--fs", type=int, default=DEFAULT_FS_HZ)
    p_cap.add_argument("--gain-tenth-db", type=int, default=0)
    p_cap.add_argument("--k6-log", type=pathlib.Path)

    args = ap.parse_args()
    if args.cmd == "sim":
        iq, k6_lines = synthesize_scenario(args.scenario)
        res = analyze_coexistence(iq, fs_hz=240_000.0, center_hz=DEFAULT_CENTER_HZ, rf_hz=DEFAULT_RF_HZ, k6_lines=k6_lines)
        print(json.dumps(res, indent=2))
        return 0

    if args.cmd == "capture":
        raw = capture_rtlsdr_iq(
            seconds=args.seconds,
            center_hz=args.center_hz,
            fs_hz=args.fs,
            gain_tenth_db=args.gain_tenth_db,
        )
        npy_path = args.out.with_suffix(".npy")
        np.save(npy_path, raw)
        iq = ((raw[0::2].astype(np.float32) - 127.5) + 1j * (raw[1::2].astype(np.float32) - 127.5)) / 127.5
        k6_lines = args.k6_log.read_text(encoding="utf-8").splitlines() if args.k6_log else None
        res = analyze_coexistence(iq, fs_hz=args.fs, center_hz=args.center_hz, rf_hz=args.rf_hz, k6_lines=k6_lines)
        res["iq_file"] = str(npy_path)
        print(json.dumps(res, indent=2))
        return 0

    iq = load_iq(args.iq)
    k6_lines = args.k6_log.read_text(encoding="utf-8").splitlines() if args.k6_log else None
    res = analyze_coexistence(iq, fs_hz=args.fs, center_hz=args.center_hz, rf_hz=args.rf_hz, k6_lines=k6_lines)
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
