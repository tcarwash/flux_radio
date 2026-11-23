"""Synthetic short (<30 ms) SDR burst detector tests, no radio access."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_micin_rf_edge_analyze import measure


def iq(*, sample_rate=256000, duration=.25, burst_s=None, burst_ms=26,
       amplitude=18, seed=101):
    n = int(sample_rate * duration)
    rng = np.random.default_rng(seed)
    x = (rng.normal(0, 1, n) + 1j * rng.normal(0, 1, n)).astype(np.complex64)
    if burst_s is not None:
        start = int(burst_s * sample_rate)
        end = min(n, start + int(burst_ms * sample_rate / 1000))
        indices = np.arange(end - start, dtype=np.float64)
        x[start:end] += amplitude * np.exp(2j * np.pi * (-50000) * indices / sample_rate)
    raw = np.empty(2 * n, dtype=np.uint8)
    raw[::2] = np.clip(np.round(x.real + 127.5), 0, 255).astype(np.uint8)
    raw[1::2] = np.clip(np.round(x.imag + 127.5), 0, 255).astype(np.uint8)
    return raw


def test_short_26ms_rf_edge_detected():
    result = measure(iq(burst_s=.10), sample_rate=256000,
                     expected_offset_hz=-50000, fft_size=512)
    assert len(result["detected_segments"]) == 1
    burst = result["detected_segments"][0]
    assert abs(burst["start_s"] - .10) <= .004
    assert abs(burst["duration_ms"] - 26) <= 5
    assert abs(burst["carrier_offset_hz_uncalibrated"] + 50000) <= 500
    assert burst["peak_local_bin_vs_flanks_db"] > 15
    assert result["adc_clip_fraction"] == 0


def test_noise_only_produces_no_false_burst():
    result = measure(iq(), sample_rate=256000,
                     expected_offset_hz=-50000, fft_size=512)
    assert result["detected_segments"] == []


def test_isolated_noise_spike_rejected():
    # Single 2-ms window exceeds threshold but fails minimum run length.
    result = measure(iq(burst_s=.1, burst_ms=1.5), sample_rate=256000,
                     expected_offset_hz=-50000, fft_size=512)
    assert result["detected_segments"] == []


@pytest.mark.parametrize("bad", [np.zeros(11, dtype=np.uint8),
                                  np.zeros(5, dtype=np.int16),
                                  np.zeros((16, 2), dtype=np.uint8)])
def test_invalid_iq_refused(bad):
    with pytest.raises(ValueError):
        measure(bad)


def test_invalid_reference_offset_refused():
    with pytest.raises(ValueError):
        measure(iq(), sample_rate=256000, expected_offset_hz=-115000, fft_size=512)
