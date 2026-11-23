"""Offline holdout complex IQ verification for Flux; synthetic input ONLY."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_iq_holdout_verify import verify

FS = 16000


def fixture_iq(n=8000, *, seed=31):
    rng = np.random.default_rng(seed)
    samples_per_bit = 40
    frequencies = rng.choice([-320., 320.], (n + samples_per_bit - 1) // samples_per_bit)
    f = np.repeat(frequencies, samples_per_bit)[:n]
    i = np.arange(n)
    a = np.where((i // 320) % 2 == 0, 1., .8)
    x = a * np.exp(2j * np.pi * np.cumsum(f) / FS)
    return x


def received(target, *, lag=37, cfo=72.25, noise=.005, seed=42):
    rng = np.random.default_rng(seed)
    n = len(target)
    t = np.arange(n) / FS
    yy = .65 * target * np.exp(1j * (.35 + 2*np.pi*cfo*t))
    yy = yy + noise * (rng.normal(size=n) + 1j * rng.normal(size=n))
    return np.r_[np.zeros(lag, dtype=np.complex128), yy,
                 np.zeros(22, dtype=np.complex128)]


def kwargs():
    return dict(fs=FS, pilot_samples=1600, max_lag_samples=55,
                max_cfo_hz=150.)


def test_pilot_only_alignment_and_heldout_iq():
    x = fixture_iq()
    report, arrays = verify(x, received(x), **kwargs())
    assert report["pilot_only_estimated_capture_offset_samples"] == 37
    assert report["pilot_only_estimated_carrier_offset_hz"] == pytest.approx(72.25, abs=1)
    assert report["pilot_metrics"]["normalized_evm"] < .02
    assert report["heldout_metrics"]["normalized_evm"] < .025
    assert report["heldout_metrics"]["phase_error_rms_rad"] < .025
    assert report["heldout_posthoc_diagnostics"]["heldout_used_to_recalibrate_cfo_or_gain"] is False
    assert len(report["heldout_posthoc_diagnostics"]["heldout_evm_quartiles_frozen_pilot_calibration"]) == 4
    assert abs(report["heldout_posthoc_diagnostics"]["posthoc_linear_phase_error_slope_hz"]) < 1.
    assert report["heldout_apc_level_diagnostics"]["available"]
    assert abs(report["heldout_apc_level_diagnostics"]["high_minus_low_median_phase_rad"]) < .03
    assert len(arrays["heldout_residual_iq"]) == 6400
    assert report["combined_polar_tx_demonstrated"] is False
    assert report["physical_hardware_iq_evm_verified"] is False


def test_damaged_holdout_cannot_be_hidden_by_training_fit():
    x = fixture_iq()
    y = received(x, noise=0)
    y[37+1600:] *= .60
    report, _ = verify(x, y, **kwargs())
    assert report["pilot_metrics"]["normalized_evm"] < .001
    assert report["heldout_metrics"]["normalized_evm"] > .35


def test_pilot_only_fit_detects_phase_drift_in_holdout():
    x = fixture_iq()
    y = received(x, noise=0)
    t = np.arange(len(x)) / FS
    # Additional nonlinear oscillator drift cannot be corrected by one CFO fit.
    phase_drift = 2 * np.pi * 10 * (t**2)
    y[37:37+len(x)] *= np.exp(1j*phase_drift)
    report, _ = verify(x, y, **kwargs())
    assert report["heldout_metrics"]["normalized_evm"] > report["pilot_metrics"]["normalized_evm"] * 3
    assert abs(report["heldout_posthoc_diagnostics"]["posthoc_linear_phase_error_slope_hz"]) > 2.


def test_reject_invalid_inputs_or_no_pilot_lock():
    x = fixture_iq()
    with pytest.raises(ValueError, match="finite"):
        verify(x.real.astype(float), received(x), **kwargs())
    with pytest.raises(ValueError, match="pre-roll"):
        verify(x, x, **kwargs())
    with pytest.raises(ValueError, match="pilot"):
        verify(x, received(x), fs=FS, pilot_samples=8000,
               max_lag_samples=40)
    rng = np.random.default_rng(7)
    fake = rng.normal(size=8100) + 1j*rng.normal(size=8100)
    with pytest.raises(ValueError, match="acquisition"):
        verify(x, fake, **kwargs())


def test_no_hw_control_or_transmission_claims():
    src = (ROOT/"tools/k6_iq_holdout_verify.py").read_text()
    for forbidden in ("import serial", "import pyrtlsdr", "tx_on(",
                      "fd_write(", "run_module(", "flash(", "apc_set("):
        assert forbidden not in src
    assert "physical_hardware_iq_evm_verified" in src
    assert "heldout_metrics" in src
