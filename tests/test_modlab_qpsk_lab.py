"""Tests for the bounded offline QPSK lab prototype (``sim/modulation/qpsk_lab.py``).

Offline simulation only: seeded NumPy AWGN, known timing, optional static
phase offset. CFO/PLL/timing recovery are out of scope by design, and true
linear RRC QPSK on K6 hardware is unproven -- nothing here claims otherwise.
"""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

_LAB = Path(__file__).resolve().parents[1] / "sim" / "modulation" / "qpsk_lab.py"
_spec = importlib.util.spec_from_file_location("qpsk_lab", _LAB)
assert _spec is not None and _spec.loader is not None
lab = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lab)


def _bits(n, seed):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 2, n)


def test_zero_noise_exact_roundtrip_varying_lengths():
    for nbits, sps, alpha in ((8, 4, 0.35), (64, 4, 0.35), (256, 8, 0.5),
                              (100, 2, 0.25), (1024, 4, 1.0)):
        bits = _bits(nbits, seed=11)
        tx = lab.modulate(bits, sps=sps, alpha=alpha)
        got = lab.demodulate_bits(tx, nbits // 2, sps=sps, alpha=alpha)
        assert lab.bit_error_rate(bits, got) == 0.0


def test_zero_noise_exact_roundtrip_differential():
    for nbits in (8, 64, 256):
        bits = _bits(nbits, seed=23)
        tx = lab.modulate(bits, differential=True)
        got = lab.demodulate_bits(tx, nbits // 2, differential=True)
        assert lab.bit_error_rate(bits, got) == 0.0


def test_noiseless_rrc_finite_filter_evm_and_phase_offset_correction():
    bits = _bits(128, seed=5)
    ref = lab.bits_to_symbols(bits)
    tx = lab.modulate(bits)
    phase = 0.4
    rx = tx * np.exp(1j * phase)
    uncorrected = lab.demodulate_symbols(rx, len(ref))
    assert lab.evm_rms(uncorrected, ref) > 0.1
    corrected = lab.demodulate_symbols(rx, len(ref), phase_offset_rad=phase)
    # A truncated finite-span RRC pair leaves small residual ISI even with
    # zero noise; the phase correction must nevertheless preserve decisions.
    assert lab.evm_rms(corrected, ref) < 0.005
    assert lab.bit_error_rate(bits, lab.symbols_to_bits(corrected)) == 0.0


def test_invalid_parameters():
    with pytest.raises(ValueError):
        lab.bits_to_symbols([0, 1, 0])  # odd bit count
    with pytest.raises(ValueError):
        lab.bits_to_symbols([])  # empty
    with pytest.raises(ValueError):
        lab.bits_to_symbols([0, 1, 2, 0])  # non-binary
    with pytest.raises(ValueError):
        lab.symbols_to_bits([])  # empty symbols
    for bad in (0, -1, 1.5, True):
        with pytest.raises(ValueError):
            lab.rrc_taps(bad)
    for bad_alpha in (0.0, -0.5, 1.5, np.inf, np.nan):
        with pytest.raises(ValueError):
            lab.rrc_taps(4, bad_alpha)
    with pytest.raises(ValueError):
        lab.rrc_taps(4, 0.35, 0)
    with pytest.raises(ValueError):
        lab.modulate([0, 1, 0], sps=4)  # odd bits
    with pytest.raises(ValueError):
        lab.add_awgn([1.0, 2.0], seed=0)  # neither snr_db nor noise_std
    with pytest.raises(ValueError):
        lab.add_awgn([1.0, 2.0], snr_db=10.0, noise_std=0.1, seed=0)  # both
    with pytest.raises(ValueError):
        lab.add_awgn([1.0, 2.0], snr_db=10.0, seed=-1)  # negative seed
    with pytest.raises(ValueError):
        lab.add_awgn([1.0, 2.0], noise_std=-0.1, seed=0)
    bits = _bits(32, seed=1)
    tx = lab.modulate(bits)
    with pytest.raises(ValueError):
        lab.demodulate_symbols(tx, 16 + 10_000)  # waveform too short
    with pytest.raises(ValueError):
        lab.demodulate_symbols(tx, 0)
    with pytest.raises(ValueError):
        lab.bit_error_rate(bits, bits[:8])  # length mismatch
    with pytest.raises(ValueError):
        lab.bit_error_rate([], [])
    with pytest.raises(ValueError):
        lab.evm_rms(np.ones(4), np.zeros(4))  # zero-power reference
    with pytest.raises(ValueError):
        lab.evm_rms(np.ones(4), np.ones(8))  # length mismatch


def test_psk_normalized_power():
    rng = np.random.default_rng(7)
    for diff in (False, True):
        syms = lab.bits_to_symbols(rng.integers(0, 2, 2000), differential=diff)
        assert np.allclose(np.abs(syms), 1.0, atol=1e-12)
        assert abs(float(np.mean(np.abs(syms) ** 2)) - 1.0) < 1e-12
    taps = lab.rrc_taps(4, 0.35)
    assert abs(float(np.sum(taps ** 2)) - 1.0) < 1e-12


def test_evm_monotonic_with_noise():
    bits = _bits(512, seed=9)
    tx = lab.modulate(bits)
    ref = lab.demodulate_symbols(tx, len(bits) // 2)
    evms = []
    for snr_db in (30.0, 15.0, 5.0, 0.0):
        noisy = lab.add_awgn(tx, snr_db=snr_db, seed=123)
        rx = lab.demodulate_symbols(noisy, len(bits) // 2)
        evms.append(lab.evm_rms(rx, ref))
    assert evms[0] < evms[1] < evms[2] < evms[3]
    assert evms[0] < 0.05
    assert evms[3] > 0.3


def test_awgn_is_deterministic_for_seed():
    bits = _bits(64, seed=3)
    tx = lab.modulate(bits)
    a = lab.add_awgn(tx, snr_db=10.0, seed=42)
    b = lab.add_awgn(tx, snr_db=10.0, seed=42)
    c = lab.add_awgn(tx, snr_db=10.0, seed=43)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_differential_behavior():
    bits = _bits(128, seed=13)
    plain = lab.bits_to_symbols(bits, differential=False)
    encoded = lab.bits_to_symbols(bits, differential=True)
    # Differential encoding preserves unit magnitude but changes the symbols.
    assert np.allclose(np.abs(encoded), 1.0, atol=1e-12)
    assert not np.array_equal(plain, encoded)
    # A static 90-degree rotation scrambles coherent decisions ...
    rotated = plain * 1j
    assert lab.symbol_error_rate(rotated, plain) > 0.99
    # ... while differential decoding is invariant except the first symbol.
    # Differential decoding applies to the *encoded* symbol stream. Applying
    # it to arbitrary unencoded QPSK can land exactly on decision axes.
    diff_plain = lab.symbols_to_bits(encoded, differential=True)
    diff_rotated = lab.symbols_to_bits(encoded * 1j, differential=True)
    assert np.array_equal(diff_plain[2:], diff_rotated[2:])
    # End-to-end differential roundtrip through noise-free channel is exact.
    tx = lab.modulate(bits, differential=True)
    got = lab.demodulate_bits(tx, len(bits) // 2, differential=True)
    assert lab.bit_error_rate(bits, got) == 0.0
