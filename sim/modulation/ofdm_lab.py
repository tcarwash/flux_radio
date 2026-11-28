"""Offline OFDM/QPSK waveform + receiver reference (host/simulator ONLY).

This is a laboratory model of an OFDM/QPSK baseband chain: Gray-coded QPSK
symbols on a small IFFT (default 64-point, 16 active carriers), DC and
Nyquist/edge nulls, a configurable cyclic prefix, plus optional AWGN and
carrier-frequency-offset impairments, PAPR/EVM/out-of-band metrics, and a
hard-clipping model.

Scope and limits -- read before using:

* Host/simulator viability study, NOT radio transmit capability. Nothing here
  drives any transmitter, and nothing here demonstrates that any handheld can
  emit OFDM.
* The FD6818B (and the original FD6818) signal path documented in
  ``docs/k6-rf-expansion-plan.md`` is a constant-envelope FM/FSK chain: it
  controls frequency/phase, not arbitrary amplitude. OFDM is a varying-envelope
  (linear) modulation with PAPR several dB above a constant envelope, so it
  needs a linear PA / linear signal chain. Do NOT claim the FD6818B supports
  arbitrary IQ or OFDM/linear-PA transmission on the basis of this file.
* Pure NumPy (no SciPy, no firmware, no hardware access). Complex-baseband,
  ideal sample timing, no multipath, no phase noise, no PA model beyond memoryless
  hard clipping. CFO is a pure per-sample phase rotation.
* Average time-domain power of the unclipped waveform is normalized to 1 so
  PAPR/SNR numbers are comparable across configurations.

Minimal API::

    bits = deterministic_bits(n_symbols, seed=0)   # reproducible bit stream
    syms = qpsk_modulate(bits)                     # Gray QPSK, unit power
    tx = ofdm_modulate(syms)                       # add IFFT + cyclic prefix
    rx = ... impairment: add_awgn / add_cfo / clip_signal ...
    est = ofdm_demodulate(rx)                      # strip CP + FFT
    out = qpsk_demodulate(est)                     # hard bits
    papr_db(tx), evm_db(syms, est), oob_ratio_db(rx, ...)

The Gray QPSK mapping matches ``sim/k6if/psk.py``: bit pair ``(b0, b1)`` maps
to ``((1 - 2*b0) + 1j*(1 - 2*b1)) / sqrt(2)``.
"""

import numpy as np

__all__ = [
    "DEFAULT_N_FFT",
    "DEFAULT_N_ACTIVE",
    "DEFAULT_CP_LEN",
    "active_indices",
    "deterministic_bits",
    "qpsk_modulate",
    "qpsk_demodulate",
    "ofdm_modulate",
    "ofdm_demodulate",
    "modulate_bits",
    "demodulate_bits",
    "add_awgn",
    "add_cfo",
    "papr_db",
    "evm_rms",
    "evm_db",
    "clip_signal",
    "oob_ratio_db",
]

DEFAULT_N_FFT = 64
DEFAULT_N_ACTIVE = 16
DEFAULT_CP_LEN = 16


def _check_n_fft(n_fft):
    if isinstance(n_fft, bool) or not isinstance(n_fft, (int, np.integer)):
        raise ValueError("n_fft must be an integer")
    n_fft = int(n_fft)
    if n_fft < 16 or (n_fft & (n_fft - 1)) != 0:
        raise ValueError("n_fft must be a power of two >= 16")
    return n_fft


def _check_n_active(n_fft, n_active):
    if isinstance(n_active, bool) or not isinstance(n_active, (int, np.integer)):
        raise ValueError("n_active must be an integer")
    n_active = int(n_active)
    if n_active < 2 or n_active > n_fft - 2:
        raise ValueError("n_active must satisfy 2 <= n_active <= n_fft - 2")
    if n_active % 2:
        raise ValueError("n_active must be even (symmetric sidebands)")
    return n_active


def _check_cp_len(n_fft, cp_len):
    if isinstance(cp_len, bool) or not isinstance(cp_len, (int, np.integer)):
        raise ValueError("cp_len must be an integer")
    cp_len = int(cp_len)
    if cp_len < 0 or cp_len >= n_fft:
        raise ValueError("cp_len must satisfy 0 <= cp_len < n_fft")
    return cp_len


def active_indices(n_fft=DEFAULT_N_FFT, n_active=DEFAULT_N_ACTIVE):
    """Subcarrier indices carrying data.

    Carriers ``1 .. n_active/2`` (positive sideband) and
    ``n_fft - n_active/2 .. n_fft - 1`` (negative sideband). Bin 0 (DC) and
    bin ``n_fft/2`` (Nyquist) are always nulled; every other unused bin is an
    edge/guard null. Returns an integer array of length ``n_active``.
    """
    n_fft = _check_n_fft(n_fft)
    n_active = _check_n_active(n_fft, n_active)
    half = n_active // 2
    pos = np.arange(1, half + 1)
    neg = np.arange(n_fft - half, n_fft)
    return np.concatenate([pos, neg])


def deterministic_bits(n_symbols, seed=0):
    """Reproducible pseudo-random bit stream: ``2 * n_symbols`` bits (0/1)."""
    if isinstance(n_symbols, bool) or not isinstance(n_symbols, (int, np.integer)):
        raise ValueError("n_symbols must be an integer")
    n_symbols = int(n_symbols)
    if n_symbols < 1:
        raise ValueError("n_symbols must be >= 1")
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, size=2 * n_symbols).astype(int)


def qpsk_modulate(bits):
    """Gray-coded QPSK symbols (unit average power) for an even-length bit vector.

    Bit pair ``(b0, b1)`` at positions ``(2*i, 2*i+1)`` maps to
    ``((1 - 2*b0) + 1j*(1 - 2*b1)) / sqrt(2)``, the same convention as
    ``sim/k6if/psk.py``.
    """
    b = np.asarray(bits)
    if b.size == 0:
        raise ValueError("bits must be non-empty")
    if b.ndim != 1 or b.size % 2:
        raise ValueError("bits must be a 1-D vector with even length")
    if not np.all((b == 0) | (b == 1)):
        raise ValueError("bits must contain only 0/1")
    pairs = b.reshape(-1, 2).astype(float)
    return ((1.0 - 2.0 * pairs[:, 0]) + 1j * (1.0 - 2.0 * pairs[:, 1])) / np.sqrt(2.0)


def qpsk_demodulate(symbols):
    """Hard-decision inverse of :func:`qpsk_modulate`; returns even-length 0/1 vector."""
    s = np.asarray(symbols, dtype=complex).ravel()
    if s.size == 0:
        raise ValueError("symbols must be non-empty")
    bits = np.empty(2 * s.size, dtype=int)
    bits[0::2] = (s.real < 0).astype(int)
    bits[1::2] = (s.imag < 0).astype(int)
    return bits


def ofdm_modulate(symbols, n_fft=DEFAULT_N_FFT, cp_len=DEFAULT_CP_LEN,
                  n_active=DEFAULT_N_ACTIVE):
    """Map QPSK symbols to a time-domain OFDM waveform (with cyclic prefix).

    ``len(symbols)`` must be a positive multiple of ``n_active``; each block of
    ``n_active`` symbols occupies one OFDM symbol. The IFFT is scaled so the
    average time-domain power is 1. The cyclic prefix is a copy of each
    symbol's last ``cp_len`` samples. Returns a 1-D complex array of length
    ``n_syms * (n_fft + cp_len)``.
    """
    n_fft = _check_n_fft(n_fft)
    n_active = _check_n_active(n_fft, n_active)
    cp_len = _check_cp_len(n_fft, cp_len)
    s = np.asarray(symbols, dtype=complex).ravel()
    if s.size == 0:
        raise ValueError("symbols must be non-empty")
    if s.size % n_active:
        raise ValueError("len(symbols) must be a multiple of n_active")
    idx = active_indices(n_fft, n_active)
    grids = np.zeros((s.size // n_active, n_fft), dtype=complex)
    grids[:, idx] = s.reshape(-1, n_active)
    time = np.fft.ifft(grids, axis=1) * (n_fft / np.sqrt(n_active))
    if cp_len == 0:
        return time.ravel()
    return np.concatenate([time[:, -cp_len:], time], axis=1).ravel()


def ofdm_demodulate(waveform, n_fft=DEFAULT_N_FFT, cp_len=DEFAULT_CP_LEN,
                    n_active=DEFAULT_N_ACTIVE):
    """Strip the cyclic prefix and FFT back to per-carrier QPSK symbol estimates."""
    n_fft = _check_n_fft(n_fft)
    n_active = _check_n_active(n_fft, n_active)
    cp_len = _check_cp_len(n_fft, cp_len)
    x = np.asarray(waveform, dtype=complex).ravel()
    stride = n_fft + cp_len
    if x.size == 0:
        raise ValueError("waveform must be non-empty")
    if x.size % stride:
        raise ValueError("len(waveform) must be a multiple of n_fft + cp_len")
    win = x.reshape(-1, stride)[:, cp_len:]
    grids = np.fft.fft(win, axis=1) * (np.sqrt(n_active) / n_fft)
    return grids[:, active_indices(n_fft, n_active)].ravel()


def modulate_bits(bits, n_fft=DEFAULT_N_FFT, cp_len=DEFAULT_CP_LEN,
                  n_active=DEFAULT_N_ACTIVE):
    """Convenience: bits -> Gray QPSK -> OFDM waveform with cyclic prefix."""
    return ofdm_modulate(qpsk_modulate(bits), n_fft=n_fft, cp_len=cp_len,
                         n_active=n_active)


def demodulate_bits(waveform, n_fft=DEFAULT_N_FFT, cp_len=DEFAULT_CP_LEN,
                    n_active=DEFAULT_N_ACTIVE):
    """Convenience: OFDM waveform -> FFT -> hard Gray QPSK bits."""
    return qpsk_demodulate(ofdm_demodulate(waveform, n_fft=n_fft, cp_len=cp_len,
                                           n_active=n_active))


def add_awgn(waveform, snr_db, seed=1):
    """Add complex white Gaussian noise for the given SNR (dB, signal power / noise power)."""
    x = np.asarray(waveform, dtype=complex).ravel()
    if x.size == 0:
        raise ValueError("waveform must be non-empty")
    snr_db = float(snr_db)
    if not np.isfinite(snr_db):
        raise ValueError("snr_db must be finite")
    power = float(np.mean(np.abs(x) ** 2))
    if power <= 0:
        raise ValueError("waveform must have nonzero power")
    var = power / (10.0 ** (snr_db / 10.0))
    rng = np.random.default_rng(seed)
    noise = (rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
    return x + noise * np.sqrt(var / 2.0)


def add_cfo(waveform, freq_offset_hz, sample_rate_hz):
    """Apply a carrier-frequency offset: ``x[n] * exp(2j*pi*f*n/fs)``."""
    x = np.asarray(waveform, dtype=complex).ravel()
    if x.size == 0:
        raise ValueError("waveform must be non-empty")
    if not np.isfinite(float(freq_offset_hz)):
        raise ValueError("freq_offset_hz must be finite")
    if not np.isfinite(float(sample_rate_hz)) or float(sample_rate_hz) <= 0:
        raise ValueError("sample_rate_hz must be positive and finite")
    n = np.arange(x.size)
    return x * np.exp(2j * np.pi * float(freq_offset_hz) * n / float(sample_rate_hz))


def papr_db(waveform):
    """Peak-to-average power ratio in dB (constant envelope -> 0 dB)."""
    x = np.asarray(waveform, dtype=complex).ravel()
    if x.size == 0:
        raise ValueError("waveform must be non-empty")
    power = np.abs(x) ** 2
    avg = float(np.mean(power))
    if avg <= 0:
        raise ValueError("waveform must have nonzero power")
    return float(10.0 * np.log10(float(np.max(power)) / avg))


def evm_rms(reference, received):
    """RMS error-vector magnitude (linear): ``sqrt(mean(|rx-ref|^2) / mean(|ref|^2))``."""
    ref = np.asarray(reference, dtype=complex).ravel()
    rx = np.asarray(received, dtype=complex).ravel()
    if ref.size == 0:
        raise ValueError("reference must be non-empty")
    if ref.shape != rx.shape:
        raise ValueError("reference and received must have the same shape")
    denom = float(np.mean(np.abs(ref) ** 2))
    if denom <= 0:
        raise ValueError("reference must have nonzero power")
    return float(np.sqrt(float(np.mean(np.abs(rx - ref) ** 2)) / denom))


def evm_db(reference, received):
    """RMS EVM in dB (``20*log10(evm_rms)``; perfect match -> ``-inf``)."""
    rms = evm_rms(reference, received)
    if rms <= 0:
        return float("-inf")
    return float(20.0 * np.log10(rms))


def clip_signal(waveform, clip_amplitude):
    """Memoryless hard clip: limit ``|x|`` to ``clip_amplitude``, preserving phase."""
    x = np.asarray(waveform, dtype=complex).ravel()
    if x.size == 0:
        raise ValueError("waveform must be non-empty")
    clip_amplitude = float(clip_amplitude)
    if not np.isfinite(clip_amplitude) or clip_amplitude <= 0:
        raise ValueError("clip_amplitude must be positive and finite")
    mag = np.abs(x)
    scale = np.where(mag > clip_amplitude, clip_amplitude / np.maximum(mag, 1e-300), 1.0)
    return x * scale


def oob_ratio_db(waveform, n_fft=DEFAULT_N_FFT, cp_len=DEFAULT_CP_LEN,
                 n_active=DEFAULT_N_ACTIVE):
    """Out-of-band leakage in dB: inactive-bin energy / active-bin energy.

    Each OFDM symbol (CP stripped) is FFT'd and the energies are summed over
    all symbols. An ideal unclipped waveform has (up to float rounding) zero
    inactive-bin energy; clipping/smearing raises this ratio.
    """
    n_fft = _check_n_fft(n_fft)
    n_active = _check_n_active(n_fft, n_active)
    cp_len = _check_cp_len(n_fft, cp_len)
    x = np.asarray(waveform, dtype=complex).ravel()
    stride = n_fft + cp_len
    if x.size == 0:
        raise ValueError("waveform must be non-empty")
    if x.size % stride:
        raise ValueError("len(waveform) must be a multiple of n_fft + cp_len")
    grids = np.fft.fft(x.reshape(-1, stride)[:, cp_len:], axis=1)
    mask = np.zeros(n_fft, dtype=bool)
    mask[active_indices(n_fft, n_active)] = True
    e_active = float(np.sum(np.abs(grids[:, mask]) ** 2))
    e_inactive = float(np.sum(np.abs(grids[:, ~mask]) ** 2))
    if e_active <= 0:
        raise ValueError("waveform has no active-carrier energy")
    if e_inactive <= 0:
        return float("-inf")
    return float(10.0 * np.log10(e_inactive / e_active))
