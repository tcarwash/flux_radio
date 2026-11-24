"""Deterministic offline SSB (USB/LSB) IQ reference, pure NumPy.

Receive-side analytic-signal model only: real audio -> complex IQ whose
spectrum keeps one sideband. Built with an FFT Hilbert construction
(positive frequencies doubled, negative zeroed, DC/Nyquist kept); LSB is
the conjugate mirror. Intended for offline modem studies in the style of
``docs/k6-if-modem.md``, ``sim/k6if/psk.py`` and ``sim/k6digi/gp0.py``.
Not a device model: no PA, no on-air claims, no firmware.

Scaling: ``real(out) == audio`` (up to FFT roundoff), so a tone of
amplitude ``A`` has constant envelope ``|IQ| == A``. With ``carrier_hz``
the spectrum moves to ``carrier + audio`` for USB and ``carrier - audio``
for LSB; the exact ``lsb == conj(usb)`` relation holds at ``carrier 0``.
DC (0 Hz) has no sideband: a constant passes through unchanged on both.
SPDX-License-Identifier: MIT"""

import numpy as np

__all__ = ["analytic_signal", "ssb_modulate", "crest_factor", "papr_db"]


def _as_real_vector(x, name="audio"):
    if np.iscomplexobj(x):
        raise ValueError("%s must be real, got complex" % name)
    try:
        v = np.asarray(x, dtype=float)
    except (TypeError, ValueError):
        raise ValueError("%s must be a real 1-D sequence" % name)
    if v.ndim != 1 or v.size == 0:
        raise ValueError("%s must be a non-empty 1-D sequence" % name)
    if not np.all(np.isfinite(v)):
        raise ValueError("%s must be finite (no NaN/inf)" % name)
    return v


def _as_fs(fs):
    try:
        f = float(fs)
    except (TypeError, ValueError):
        raise ValueError("fs must be a positive finite sample rate")
    if not np.isfinite(f) or f <= 0:
        raise ValueError("fs must be a positive finite sample rate")
    return f


def analytic_signal(x):
    """Complex analytic signal of real vector ``x`` (FFT Hilbert).

    DC and Nyquist bins are kept (not doubled), so constants pass through
    unchanged. Pure NumPy; deterministic.
    """
    v = _as_real_vector(x)
    n = v.shape[0]
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = 1.0
        h[n // 2] = 1.0
        h[1:n // 2] = 2.0
    else:
        h[0] = 1.0
        h[1:(n + 1) // 2] = 2.0
    return np.fft.ifft(np.fft.fft(v) * h)


def ssb_modulate(audio, fs, sideband="usb", carrier_hz=0.0):
    """Real audio -> SSB complex IQ at sample rate ``fs``.

    ``sideband`` is ``"usb"`` (keeps positive frequencies) or ``"lsb"``
    (conjugate mirror, keeps negative frequencies). ``carrier_hz`` shifts
    the result by ``exp(2j*pi*carrier*t)`` and must lie below Nyquist.
    """
    v = _as_real_vector(audio)
    f = _as_fs(fs)
    if not isinstance(sideband, str) or sideband.lower() not in ("usb", "lsb"):
        raise ValueError("sideband must be 'usb' or 'lsb'")
    try:
        fc = float(carrier_hz)
    except (TypeError, ValueError):
        raise ValueError("carrier_hz must be a finite frequency")
    if not np.isfinite(fc):
        raise ValueError("carrier_hz must be a finite frequency")
    if abs(fc) >= f / 2:
        raise ValueError("carrier_hz must lie below Nyquist (fs/2)")
    z = analytic_signal(v)
    if sideband.lower() == "lsb":
        z = np.conj(z)
    if fc != 0.0:
        z = z * np.exp(2j * np.pi * fc * np.arange(v.shape[0]) / f)
    return z


def _as_iq(z, name="z"):
    try:
        v = np.asarray(z)
    except (TypeError, ValueError):
        raise ValueError("%s must be a 1-D sequence" % name)
    if v.ndim != 1 or v.size == 0:
        raise ValueError("%s must be a non-empty 1-D sequence" % name)
    if np.iscomplexobj(v):
        v = v.astype(complex)
    else:
        v = np.asarray(v, dtype=float)
    if not np.all(np.isfinite(v)):
        raise ValueError("%s must be finite (no NaN/inf)" % name)
    return v


def crest_factor(z):
    """Peak / rms magnitude of IQ vector ``z``; 0.0 for all-zero input."""
    v = _as_iq(z)
    rms = np.sqrt(np.mean(np.abs(v) ** 2))
    if rms == 0:
        return 0.0
    return float(np.max(np.abs(v)) / rms)


def papr_db(z):
    """Peak-to-average power ratio of IQ vector ``z`` in dB.

    ``-inf`` for all-zero input.
    """
    c = crest_factor(z)
    if c == 0:
        return float("-inf")
    return float(20.0 * np.log10(c))
