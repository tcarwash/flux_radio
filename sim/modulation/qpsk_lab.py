"""Bounded offline Gray-mapped QPSK laboratory prototype (pure NumPy).

Scope and limits (read before use):

- Simulated linear baseband IQ only: bits -> Gray QPSK symbols -> RRC
  pulse shaping at an integral samples/symbol -> deterministic seeded AWGN
  -> matched RRC filter -> sample with *known* timing -> optional static
  phase-offset correction -> hard decisions, error rates, RMS EVM.
- OUT OF SCOPE (explicitly unknown / not claimed here): carrier-frequency
  offset (CFO) estimation, PLL / carrier tracking, timing recovery, fading,
  and any FM-discriminator path. Demodulation assumes perfect symbol timing
  and (optionally) a known static phase offset.
- NOT proven on K6 hardware: true linear RRC-shaped QPSK through the K6's
  FM-centric RF chain is unproven. This module models an ideal linear IQ
  channel for offline study only. It registers no production mode, no
  feature pack, and no KISS handler.
- No hardware serial/RF, no network, no git interaction in this module.

Convention notes:

- Gray mapping matches ``sim/k6if/psk.py`` (``PSK.symbols`` with bps=2):
  bit pair (b0, b1) -> I = (1 - 2*b0) / sqrt(2), Q = (1 - 2*b1) / sqrt(2),
  so 00 -> (+, +), 01 -> (+, -), 10 -> (-, +), 11 -> (-, -).
- RRC taps follow the same closed form as ``sim/k6if/psk.py`` ``rrc()``
  (unit-energy normalisation) but are implemented here with NumPy only.
- Differential symbol encoding, when enabled, mirrors ``psk.py``:
  encoded[0] = plain[0], encoded[k] = encoded[k-1] * plain[k]
  (equivalently ``cumprod`` seeded with 1). Decoding uses
  d[k] = r[k] * conj(r[k-1]) with r[-1] := 1.
"""

from __future__ import annotations

import numpy as np

__all__ = [
    "bits_to_symbols",
    "symbols_to_bits",
    "rrc_taps",
    "modulate",
    "add_awgn",
    "demodulate_symbols",
    "demodulate_bits",
    "bit_error_rate",
    "symbol_error_rate",
    "evm_rms",
]

_SQRT2 = float(np.sqrt(2.0))


def _check_bits(bits) -> np.ndarray:
    b = np.asarray(bits)
    if b.ndim != 1:
        raise ValueError("bits must be a one-dimensional sequence")
    if b.size == 0:
        raise ValueError("bits must not be empty")
    if b.size % 2 != 0:
        raise ValueError("QPSK needs an even number of bits, got %d" % b.size)
    if not np.all((b == 0) | (b == 1)):
        raise ValueError("bits must contain only 0 and 1")
    return b.astype(int)


def _check_sps(sps: int) -> int:
    if isinstance(sps, bool) or not isinstance(sps, (int, np.integer)):
        raise ValueError("sps must be an integer number of samples per symbol")
    sps = int(sps)
    if sps < 1:
        raise ValueError("sps must be >= 1, got %d" % sps)
    return sps


def _check_alpha(alpha: float) -> float:
    if not isinstance(alpha, (int, float, np.floating, np.integer)):
        raise ValueError("alpha must be a number")
    alpha = float(alpha)
    if not np.isfinite(alpha) or alpha <= 0.0 or alpha > 1.0:
        raise ValueError("alpha must be in (0, 1], got %r" % alpha)
    return alpha


def _check_span(span_symbols: int) -> int:
    if isinstance(span_symbols, bool) or not isinstance(span_symbols, (int, np.integer)):
        raise ValueError("span_symbols must be an integer")
    span_symbols = int(span_symbols)
    if span_symbols < 1:
        raise ValueError("span_symbols must be >= 1, got %d" % span_symbols)
    return span_symbols


def bits_to_symbols(bits, differential: bool = False) -> np.ndarray:
    """Map an even-length bit vector to unit-magnitude Gray QPSK symbols.

    differential=False: symbol[k] carries bits (b[2k], b[2k+1]).
    differential=True: the plain Gray symbols are then differentially
      encoded (each output symbol rotates the previous one).
    """
    b = _check_bits(bits)
    pairs = b.reshape(-1, 2)
    syms = ((1.0 - 2.0 * pairs[:, 0]) + 1j * (1.0 - 2.0 * pairs[:, 1])) / _SQRT2
    if differential:
        syms = np.cumprod(np.concatenate(([complex(1.0)], syms)))[1:]
    return syms


def symbols_to_bits(symbols, differential: bool = False) -> np.ndarray:
    """Hard-decide complex symbols back to bits (inverse of bits_to_symbols).

    differential=True first differentially decodes
    (d[k] = r[k] * conj(r[k-1]), r[-1] := 1) and then slices.
    """
    r = np.asarray(symbols, dtype=complex).ravel()
    if r.size == 0:
        raise ValueError("symbols must not be empty")
    if not np.all(np.isfinite(r.real)) or not np.all(np.isfinite(r.imag)):
        raise ValueError("symbols must be finite")
    if differential:
        prev = np.concatenate(([complex(1.0)], r[:-1]))
        r = r * np.conj(prev)
    bits = np.empty(2 * r.size, dtype=int)
    bits[0::2] = (r.real < 0).astype(int)
    bits[1::2] = (r.imag < 0).astype(int)
    return bits


def rrc_taps(sps: int, alpha: float = 0.35, span_symbols: int = 8) -> np.ndarray:
    """Root-raised-cosine taps, ``2*span*sps + 1`` long, unit energy.

    Same closed form as ``sim/k6if/psk.py`` ``rrc()`` (which normalises by
    ``sqrt(sum(h**2))``), reimplemented with NumPy only. ``sps`` must be an
    integer (integral samples/symbol by design); ``span_symbols`` counts the
    one-sided filter span in symbols.
    """
    sps = _check_sps(sps)
    alpha = _check_alpha(alpha)
    span = _check_span(span_symbols)
    t = np.arange(-span * sps, span * sps + 1, dtype=float) / float(sps)
    h = np.empty_like(t)
    singular = 1.0 / (4.0 * alpha)
    for i, x in enumerate(t):
        if abs(x) < 1e-12:
            h[i] = 1.0 - alpha + 4.0 * alpha / np.pi
        elif abs(abs(x) - singular) < 1e-12:
            h[i] = (alpha / np.sqrt(2.0)
                    * ((1.0 + 2.0 / np.pi) * np.sin(np.pi / (4.0 * alpha))
                       + (1.0 - 2.0 / np.pi) * np.cos(np.pi / (4.0 * alpha))))
        else:
            h[i] = ((np.sin(np.pi * x * (1.0 - alpha))
                     + 4.0 * alpha * x * np.cos(np.pi * x * (1.0 + alpha)))
                    / (np.pi * x * (1.0 - (4.0 * alpha * x) ** 2)))
    energy = float(np.sum(h ** 2))
    if energy <= 0.0 or not np.isfinite(energy):
        raise ValueError("RRC prototype has non-positive energy")
    return h / np.sqrt(energy)


def modulate(bits, sps: int = 4, alpha: float = 0.35,
             span_symbols: int = 8, differential: bool = False) -> np.ndarray:
    """Map bits to a simulated linear-IQ baseband waveform at ``sps``/symbol.

    Upsamples the Gray (optionally differentially encoded) symbols and
    applies the RRC pulse shape with full convolution, so the waveform
    includes the filter transient on both ends.
    """
    syms = bits_to_symbols(bits, differential=differential)
    sps = _check_sps(sps)
    alpha = _check_alpha(alpha)
    span = _check_span(span_symbols)
    h = rrc_taps(sps, alpha, span)
    up = np.zeros(syms.size * sps, dtype=complex)
    up[::sps] = syms
    return np.convolve(up, h, mode="full")


def add_awgn(samples, snr_db: float | None = None,
             noise_std: float | None = None, seed: int = 0) -> np.ndarray:
    """Add deterministic complex AWGN from a seeded RNG (no hidden randomness).

    Exactly one of ``snr_db`` / ``noise_std`` must be given:

    - ``snr_db``: set the total complex noise power from the mean signal
      power, ``Pn = Ps / 10**(snr_db/10)``.
    - ``noise_std``: total complex noise std with ``E|n|^2 = noise_std**2``,
      drawn as ``(randn + j*randn) / sqrt(2) * noise_std``.

    ``seed`` selects ``numpy.random.default_rng(seed)``, so identical inputs
    give identical outputs.
    """
    x = np.asarray(samples, dtype=complex).ravel()
    if x.size == 0:
        raise ValueError("samples must not be empty")
    if not np.all(np.isfinite(x.real)) or not np.all(np.isfinite(x.imag)):
        raise ValueError("samples must be finite")
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
        raise ValueError("seed must be an integer")
    if int(seed) < 0:
        raise ValueError("seed must be >= 0")
    if (snr_db is None) == (noise_std is None):
        raise ValueError("give exactly one of snr_db or noise_std")
    if snr_db is not None:
        snr_db = float(snr_db)
        if not np.isfinite(snr_db):
            raise ValueError("snr_db must be finite")
        signal_power = float(np.mean(np.abs(x) ** 2))
        std = float(np.sqrt(signal_power / (10.0 ** (snr_db / 10.0))))
    else:
        std = float(noise_std)
        if not np.isfinite(std) or std < 0.0:
            raise ValueError("noise_std must be finite and >= 0")
    rng = np.random.default_rng(int(seed))
    noise = ((rng.standard_normal(x.size) + 1j * rng.standard_normal(x.size))
             / _SQRT2 * std)
    return x + noise


def demodulate_symbols(samples, num_symbols: int, sps: int = 4,
                       alpha: float = 0.35, span_symbols: int = 8,
                       phase_offset_rad: float = 0.0,
                       differential: bool = False) -> np.ndarray:
    """Matched-RRC demodulation with known timing and optional phase offset.

    Applies the matched RRC filter (full convolution with the same taps the
    transmitter used), samples at the known total group delay
    (``len(taps) - 1``), derotates by ``phase_offset_rad``
    (``y * exp(-1j * phase_offset_rad)``), and returns ``num_symbols``
    soft symbols. With ``differential=True`` the soft symbols are
    differentially decoded (same convention as the encoder) before return.

    No CFO estimation, PLL, or timing recovery is performed -- callers must
    supply symbol timing (implicitly exact here) and any static phase.
    """
    x = np.asarray(samples, dtype=complex).ravel()
    if x.size == 0:
        raise ValueError("samples must not be empty")
    if not np.all(np.isfinite(x.real)) or not np.all(np.isfinite(x.imag)):
        raise ValueError("samples must be finite")
    if isinstance(num_symbols, bool) or not isinstance(num_symbols, (int, np.integer)):
        raise ValueError("num_symbols must be an integer")
    num_symbols = int(num_symbols)
    if num_symbols < 1:
        raise ValueError("num_symbols must be >= 1")
    sps = _check_sps(sps)
    alpha = _check_alpha(alpha)
    span = _check_span(span_symbols)
    phase = float(phase_offset_rad)
    if not np.isfinite(phase):
        raise ValueError("phase_offset_rad must be finite")
    h = rrc_taps(sps, alpha, span)
    y = np.convolve(x, h, mode="full")
    start = len(h) - 1
    idx = start + np.arange(num_symbols) * sps
    if idx[-1] >= y.size:
        raise ValueError(
            "waveform too short for %d symbols at sps=%d (need index %d, have %d samples)"
            % (num_symbols, sps, int(idx[-1]), y.size))
    syms = y[idx] * np.exp(-1j * phase)
    if differential:
        prev = np.concatenate(([complex(1.0)], syms[:-1]))
        syms = syms * np.conj(prev)
    return syms


def demodulate_bits(samples, num_symbols: int, sps: int = 4,
                    alpha: float = 0.35, span_symbols: int = 8,
                    phase_offset_rad: float = 0.0,
                    differential: bool = False) -> np.ndarray:
    """Convenience wrapper: matched-filter demodulation plus hard decisions."""
    syms = demodulate_symbols(samples, num_symbols, sps=sps, alpha=alpha,
                              span_symbols=span_symbols,
                              phase_offset_rad=phase_offset_rad,
                              differential=False)
    return symbols_to_bits(syms, differential=differential)


def bit_error_rate(bits_a, bits_b) -> float:
    """Fraction of disagreeing bits between two equal-length bit vectors."""
    a = np.asarray(bits_a).ravel()
    c = np.asarray(bits_b).ravel()
    if a.size == 0 or c.size == 0:
        raise ValueError("bit vectors must not be empty")
    if a.shape != c.shape:
        raise ValueError("bit vectors must have equal length")
    if not np.all((a == 0) | (a == 1)) or not np.all((c == 0) | (c == 1)):
        raise ValueError("bit vectors must contain only 0 and 1")
    return float(np.mean(a != c))


def symbol_error_rate(syms_a, syms_b) -> float:
    """Fraction of unequal entries between two equal-length symbol vectors."""
    a = np.asarray(syms_a).ravel()
    c = np.asarray(syms_b).ravel()
    if a.size == 0 or c.size == 0:
        raise ValueError("symbol vectors must not be empty")
    if a.shape != c.shape:
        raise ValueError("symbol vectors must have equal length")
    return float(np.mean(a != c))


def evm_rms(received, reference) -> float:
    """RMS error-vector magnitude normalised by the reference RMS level.

    ``sqrt(mean(|rx - ref|^2)) / sqrt(mean(|ref|^2))``. Returns 0.0 for
    identical inputs; raises if the reference has zero power.
    """
    rx = np.asarray(received, dtype=complex).ravel()
    ref = np.asarray(reference, dtype=complex).ravel()
    if rx.size == 0 or ref.size == 0:
        raise ValueError("vectors must not be empty")
    if rx.shape != ref.shape:
        raise ValueError("received and reference must have equal length")
    if not np.all(np.isfinite(rx.real)) or not np.all(np.isfinite(rx.imag)):
        raise ValueError("received must be finite")
    if not np.all(np.isfinite(ref.real)) or not np.all(np.isfinite(ref.imag)):
        raise ValueError("reference must be finite")
    ref_power = float(np.mean(np.abs(ref) ** 2))
    if ref_power <= 0.0 or not np.isfinite(ref_power):
        raise ValueError("reference has zero power")
    return float(np.sqrt(np.mean(np.abs(rx - ref) ** 2)) / np.sqrt(ref_power))
