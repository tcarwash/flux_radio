"""Offline amplitude-modulation waveform reference (simulation only, no RF activity).

K6 context (see ``docs/k6-afsweep.md`` and ``docs/k6-rf-expansion-plan.md``):

* K6 "AM" as documented is a *receive* path: REG_47 AF select 7 routes the
  chip's AM demodulator audio to the speaker (with REG_3D = 0x2AAB IF,
  AFC off, AM AGC settings). The companion "USB" AF select 5 is also
  receive-only and is really dual-sideband at zero IF, not true SSB.
* True RF AM transmit from the K6 is *unproven*: the transmit chain is a
  constant-envelope FM/FSK phase synthesizer, and a constant-envelope
  signal ``A*exp(j*phi(t))`` has ``|s| == A`` by construction, so FM phase
  control alone cannot produce a real AM envelope. Envelope modulation
  needs a separate controlled-amplitude (linear/polar) chain, and the
  APC-to-RF-voltage mapping must be measured per board -- do not assume
  it is linear or safely live-updatable.
* ``dsb_sc_waveform`` below is a *simulation* of an ideal suppressed-carrier
  DSB analytic signal for receiver/decoder testing. It is not a transmit
  path and proves nothing about K6 TX hardware.

Model: complex baseband analytic signal ``s(t) = (1 + m*x(t)) * exp(j*(2*pi*f_offset*t + phase))``,
with audio ``x(t)`` normalized to ``|x| <= 1`` and modulation index ``0 <= m <= 1``.
Overmodulation (``m > 1`` or ``|x| > 1``) is rejected, never clipped.
Pure NumPy; no hardware, serial, or RF access.
"""

import numpy as np

__all__ = [
    "am_baseband",
    "dsb_sc_waveform",
    "envelope",
    "mean_power",
]

_TOL = 1e-12


def _check_audio(x):
    """Return ``x`` as a float array, requiring finite samples with ``|x| <= 1``."""
    a = np.asarray(x, dtype=float)
    if a.ndim != 1:
        raise ValueError("audio must be a 1-D sequence")
    if a.size == 0:
        raise ValueError("audio must be non-empty")
    if not np.all(np.isfinite(a)):
        raise ValueError("audio must be finite (no NaN/inf)")
    peak = float(np.max(np.abs(a)))
    if peak > 1.0 + _TOL:
        raise ValueError("audio peak %.6g exceeds 1: normalize first" % peak)
    return a


def _check_carrier(fs, f_offset, phase):
    """Validate sample rate, offset frequency, and phase (all finite)."""
    for name, value in (("fs", fs), ("f_offset", f_offset), ("phase", phase)):
        if not np.isfinite(value):
            raise ValueError("%s must be finite" % name)
    fs = float(fs)
    if fs <= 0:
        raise ValueError("fs must be positive")
    if abs(float(f_offset)) >= fs / 2:
        raise ValueError("f_offset must lie within (-fs/2, fs/2)")
    return fs, float(f_offset), float(phase)


def _carrier(n, fs, f_offset, phase):
    """Unit-magnitude complex carrier ``exp(j*(2*pi*f_offset*t + phase))``."""
    t = np.arange(n, dtype=float) / fs
    return np.exp(1j * (2.0 * np.pi * f_offset * t + phase))


def am_baseband(x, m, fs, f_offset=0.0, phase=0.0):
    """Conventional AM analytic signal ``(1 + m*x) * carrier`` (complex128).

    ``x`` must already be normalized to ``|x| <= 1``; ``m`` in [0, 1].
    Raises ``ValueError`` on overmodulation (``m`` outside [0, 1] or
    ``|x| > 1``), non-finite inputs, or an aliased ``f_offset``.
    """
    a = _check_audio(x)
    if not np.isfinite(m):
        raise ValueError("m must be finite")
    m = float(m)
    if not 0.0 <= m <= 1.0:
        raise ValueError("modulation index m must be in [0, 1], got %r" % m)
    fs, f_offset, phase = _check_carrier(fs, f_offset, phase)
    return ((1.0 + m * a) * _carrier(a.size, fs, f_offset, phase)).astype(np.complex128)


def dsb_sc_waveform(x, fs, f_offset=0.0, phase=0.0):
    """Suppressed-carrier DSB *simulation* ``x * carrier`` (complex128).

    Ideal double-sideband with no carrier term: with ``x = 0`` the output
    is all zeros. Simulation reference only -- not true TX (see module
    docstring). Same audio/carrier guards as :func:`am_baseband`.
    """
    a = _check_audio(x)
    fs, f_offset, phase = _check_carrier(fs, f_offset, phase)
    return (a * _carrier(a.size, fs, f_offset, phase)).astype(np.complex128)


def envelope(s):
    """AM envelope ``|s|`` as a real array (for AM: ``1 + m*x``)."""
    s = np.asarray(s)
    if s.size == 0:
        raise ValueError("signal must be non-empty")
    if not np.all(np.isfinite(s)):
        raise ValueError("signal must be finite (no NaN/inf)")
    return np.abs(s).astype(float)


def mean_power(s):
    """Mean ``|s|^2`` (for a tone ``x``: ``1 + m^2/2`` at full scale)."""
    s = np.asarray(s, dtype=np.complex128)
    if s.size == 0:
        raise ValueError("signal must be non-empty")
    if not np.all(np.isfinite(s)):
        raise ValueError("signal must be finite (no NaN/inf)")
    return float(np.mean(np.abs(s) ** 2))
