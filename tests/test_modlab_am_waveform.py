"""Deterministic tests for the offline AM waveform reference.

Pure NumPy, no hardware/RF. All tones sit on exact FFT bins (1 s at
48 kHz -> 1 Hz resolution), so the spectra are reproducible bit-for-bit.
"""

import numpy as np
import pytest

from sim.modulation.am_waveform import (
    am_baseband,
    dsb_sc_waveform,
    envelope,
    mean_power,
)

FS = 48000
N = FS  # 1 s window -> 1 Hz bins
FO = 5000.0
FM = 1000.0
T = np.arange(N, dtype=float) / FS


def tone(fm=FM, peak=1.0):
    return peak * np.cos(2.0 * np.pi * fm * T)


def spectrum(s):
    """Full FFT magnitudes keyed by exact-bin frequency."""
    return np.abs(np.fft.fft(s))


def test_modulation_index_range():
    x = tone()
    for bad in (-1e-9, -0.5, 1.0 + 1e-9, 1.5, np.nan, np.inf):
        with pytest.raises(ValueError):
            am_baseband(x, bad, FS, FO)
    for ok in (0.0, 0.3, 1.0):
        assert am_baseband(x, ok, FS, FO).shape == (N,)


def test_audio_must_be_normalized():
    with pytest.raises(ValueError):
        am_baseband(1.2 * tone(), 0.5, FS, FO)  # overmodulation, never clipped
    with pytest.raises(ValueError):
        am_baseband(tone() + np.nan, 0.5, FS, FO)
    with pytest.raises(ValueError):
        am_baseband(np.full(N, np.inf), 0.5, FS, FO)
    with pytest.raises(ValueError):
        am_baseband(np.zeros((N, 2)), 0.5, FS, FO)
    with pytest.raises(ValueError):
        am_baseband(np.array([]), 0.5, FS, FO)


def test_carrier_params_rejected():
    x = tone()
    with pytest.raises(ValueError):
        am_baseband(x, 0.5, 0, FO)
    with pytest.raises(ValueError):
        am_baseband(x, 0.5, FS, FS / 2)  # aliased offset
    with pytest.raises(ValueError):
        am_baseband(x, 0.5, FS, np.nan)
    with pytest.raises(ValueError):
        am_baseband(x, 0.5, FS, FO, phase=np.inf)


def test_carrier_at_zero_audio():
    s = am_baseband(np.zeros(N), 0.7, FS, FO, phase=0.3)
    assert s.dtype == np.complex128
    assert np.allclose(np.abs(s), 1.0, atol=0)
    want = np.exp(1j * (2.0 * np.pi * FO * T + 0.3))
    assert np.allclose(s, want, atol=1e-12)
    mag = spectrum(s)
    assert mag[int(FO)] == pytest.approx(N, rel=1e-9)
    others = np.delete(mag, int(FO))
    assert others.max() < 1e-6 * N  # pure tone, nothing else


def test_phase_and_freq_match_formula():
    x = 0.5 * tone()
    s = am_baseband(x, 0.4, FS, FO, phase=1.1)
    want = (1.0 + 0.4 * x) * np.exp(1j * (2.0 * np.pi * FO * T + 1.1))
    assert np.allclose(s, want, atol=1e-12)


def test_envelope_matches_modulation():
    m = 0.6
    s = am_baseband(tone(), m, FS, FO)
    assert np.allclose(envelope(s), 1.0 + m * tone(), atol=1e-12)
    assert np.allclose(envelope(s).min(), 1.0 - m, atol=1e-9)
    assert np.allclose(envelope(s).max(), 1.0 + m, atol=1e-9)


def test_symmetric_tone_sidebands():
    m = 0.5
    mag = spectrum(am_baseband(tone(), m, FS, FO))
    lo, hi, c = int(FO - FM), int(FO + FM), int(FO)
    assert mag[c] == pytest.approx(N, rel=1e-9)
    assert mag[lo] == pytest.approx(N * m / 2, rel=1e-9)
    assert mag[hi] == pytest.approx(N * m / 2, rel=1e-9)
    assert mag[lo] == pytest.approx(mag[hi], rel=1e-9)  # symmetric
    rest = mag.copy()
    rest[[lo, hi, c]] = 0.0
    assert rest.max() < 1e-6 * N  # no other products


def test_overmodulation_rejected_not_clipped():
    with pytest.raises(ValueError):
        am_baseband(tone(), 1.5, FS, FO)
    with pytest.raises(ValueError):
        am_baseband(tone(), -0.2, FS, FO)
    with pytest.raises(ValueError):  # just over full scale still rejected
        am_baseband(1.0001 * tone(), 1.0, FS, FO)


def test_finite_numbers():
    with pytest.raises(ValueError):
        envelope(np.full(8, np.nan, dtype=np.complex128))
    with pytest.raises(ValueError):
        mean_power(np.full(8, np.inf))
    with pytest.raises(ValueError):
        envelope(np.array([], dtype=np.complex128))
    assert np.all(np.isfinite(am_baseband(tone(), 1.0, FS, FO)))
    assert np.all(np.isfinite(dsb_sc_waveform(tone(), FS, FO)))


def test_power_scaling_bounds():
    for m in (0.0, 0.3, 0.6, 1.0):
        s = am_baseband(tone(), m, FS, FO)
        assert mean_power(s) == pytest.approx(1.0 + m * m / 2, rel=1e-9)
        assert envelope(s).max() <= 1.0 + m + 1e-12  # peak bound
        assert envelope(s).min() >= 1.0 - m - 1e-12
        p = mean_power(s)
        assert (1.0 - m) ** 2 - 1e-9 <= p <= (1.0 + m) ** 2 + 1e-9


def test_dsb_simulation_suppresses_carrier():
    mag = spectrum(dsb_sc_waveform(tone(), FS, FO))
    lo, hi, c = int(FO - FM), int(FO + FM), int(FO)
    assert mag[c] < 1e-6 * N  # no carrier term
    assert mag[lo] == pytest.approx(N / 2, rel=1e-9)
    assert mag[hi] == pytest.approx(N / 2, rel=1e-9)
    assert np.all(dsb_sc_waveform(np.zeros(N), FS, FO) == 0)
    with pytest.raises(ValueError):
        dsb_sc_waveform(1.5 * tone(), FS, FO)
