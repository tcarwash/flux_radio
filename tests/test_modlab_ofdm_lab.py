"""Tests for the offline OFDM/QPSK reference (sim/modulation/ofdm_lab.py).

Host/simulator viability checks only: exact IFFT/FFT roundtrip, cyclic-prefix
handling, PAPR above a constant-envelope reference, CFO/clipping penalties,
and input validation. Nothing here transmits or claims radio capability.
"""
import os
import sys

import pytest

np = pytest.importorskip("numpy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sim.modulation import ofdm_lab as lab  # noqa: E402

N_FFT = 64
N_ACTIVE = 16
CP = 16


def make_link(n_ofdm_symbols=8, seed=1234, n_fft=N_FFT, cp_len=CP,
              n_active=N_ACTIVE):
    bits = lab.deterministic_bits(n_ofdm_symbols * n_active, seed=seed)
    syms = lab.qpsk_modulate(bits)
    tx = lab.ofdm_modulate(syms, n_fft=n_fft, cp_len=cp_len, n_active=n_active)
    return bits, syms, tx


def test_gray_mapping_matches_psk_convention():
    # (b0, b1) -> ((1-2*b0) + 1j*(1-2*b1)) / sqrt(2), as in sim/k6if/psk.py
    out = lab.qpsk_modulate(np.array([0, 0, 0, 1, 1, 0, 1, 1]))
    expected = np.array([1 + 1j, 1 - 1j, -1 + 1j, -1 - 1j]) / np.sqrt(2.0)
    np.testing.assert_allclose(out, expected, rtol=0, atol=0)
    np.testing.assert_array_equal(lab.qpsk_demodulate(out),
                                  np.array([0, 0, 0, 1, 1, 0, 1, 1]))


def test_deterministic_bits_reproducible():
    a = lab.deterministic_bits(64, seed=7)
    b = lab.deterministic_bits(64, seed=7)
    c = lab.deterministic_bits(64, seed=8)
    np.testing.assert_array_equal(a, b)
    assert a.shape == (128,)
    assert set(np.unique(a)) <= {0, 1}
    assert np.any(c != a)


@pytest.mark.parametrize("seed", [0, 1, 2])
@pytest.mark.parametrize("cp_len", [0, 8, 16])
def test_zero_noise_roundtrip_bit_perfect(seed, cp_len):
    bits, _, tx = make_link(seed=seed, cp_len=cp_len)
    np.testing.assert_array_equal(lab.demodulate_bits(tx, cp_len=cp_len), bits)


def test_prefix_size_and_content():
    n_sym = 4
    _, _, tx = make_link(n_ofdm_symbols=n_sym)
    assert tx.shape == (n_sym * (N_FFT + CP),)
    # each cyclic prefix repeats its own symbol's tail
    frames = tx.reshape(n_sym, N_FFT + CP)
    np.testing.assert_allclose(frames[:, :CP], frames[:, CP + N_FFT - CP:CP + N_FFT],
                               rtol=0, atol=1e-12)


def test_carrier_map_nulls_dc_nyquist_and_count():
    idx = lab.active_indices(N_FFT, N_ACTIVE)
    assert idx.shape == (N_ACTIVE,)
    assert 0 not in idx                      # DC null
    assert N_FFT // 2 not in idx             # Nyquist null
    assert len(set(idx.tolist())) == N_ACTIVE
    # symmetric sidebands: every positive carrier has a negative mirror
    assert sorted(((-i) % N_FFT for i in idx)) == sorted(idx.tolist())


def test_papr_above_constant_envelope_reference():
    _, _, tx = make_link(n_ofdm_symbols=16, seed=20261124)
    # constant-envelope reference: unit-magnitude phasor -> 0 dB
    n = np.arange(tx.size)
    tone = np.exp(2j * np.pi * n / N_FFT)
    assert abs(lab.papr_db(tone)) < 1e-9
    # nontrivial multicarrier QPSK data sits clearly above it
    assert lab.papr_db(tx) > 2.0


def test_cfo_penalty():
    n_sym = 16
    bits, syms, tx = make_link(n_ofdm_symbols=n_sym, seed=99)
    clean = lab.ofdm_demodulate(tx)
    assert lab.evm_db(syms, clean) < -200  # float-roundtrip floor, no impairment
    # sample rate N_FFT per OFDM symbol body -> subcarrier spacing is 1 Hz;
    # a quarter-spacing offset destroys orthogonality within a few symbols
    bad = lab.add_cfo(tx, 0.25, N_FFT)
    est = lab.ofdm_demodulate(bad)
    assert lab.evm_db(syms, est) > lab.evm_db(syms, clean) + 20.0
    assert np.sum(lab.qpsk_demodulate(est) != bits) > 0


def test_clipping_penalty_evm_and_oob():
    n_sym = 16
    _, syms, tx = make_link(n_ofdm_symbols=n_sym, seed=555)
    clipped = lab.clip_signal(tx, 1.0)  # rms power is 1, peaks reach higher
    assert np.max(np.abs(clipped)) <= 1.0 + 1e-12
    est = lab.ofdm_demodulate(clipped)
    assert lab.evm_rms(syms, est) > 1e-6
    assert lab.oob_ratio_db(clipped) > lab.oob_ratio_db(tx) + 20.0


def test_high_snr_awgn_bit_perfect_and_deterministic():
    bits, _, tx = make_link(seed=11)
    t1 = lab.add_awgn(tx, 80.0, seed=3)
    t2 = lab.add_awgn(tx, 80.0, seed=3)
    np.testing.assert_array_equal(t1, t2)
    np.testing.assert_array_equal(lab.demodulate_bits(t1), bits)


def test_low_snr_awgn_causes_errors():
    bits, _, tx = make_link(n_ofdm_symbols=16, seed=11)
    noisy = lab.add_awgn(tx, 0.0, seed=3)
    assert np.sum(lab.demodulate_bits(noisy) != bits) > 0


def test_input_validation():
    good_bits = lab.deterministic_bits(N_ACTIVE, seed=0)
    good_syms = lab.qpsk_modulate(good_bits)
    good_tx = lab.ofdm_modulate(good_syms)
    with pytest.raises(ValueError):
        lab.active_indices(48, 16)          # not a power of two
    with pytest.raises(ValueError):
        lab.active_indices(64, 7)           # odd carrier count
    with pytest.raises(ValueError):
        lab.active_indices(64, 64)          # no room for DC/Nyquist nulls
    with pytest.raises(ValueError):
        lab.ofdm_modulate(good_syms, cp_len=-1)
    with pytest.raises(ValueError):
        lab.ofdm_modulate(good_syms, cp_len=64)
    with pytest.raises(ValueError):
        lab.ofdm_modulate(good_syms[:-1])   # not a multiple of n_active
    with pytest.raises(ValueError):
        lab.ofdm_modulate(np.array([], dtype=complex))
    with pytest.raises(ValueError):
        lab.qpsk_modulate(np.array([0, 1, 0]))
    with pytest.raises(ValueError):
        lab.qpsk_modulate(np.array([0, 2]))
    with pytest.raises(ValueError):
        lab.ofdm_demodulate(good_tx[:-1])   # truncated waveform
    with pytest.raises(ValueError):
        lab.add_awgn(good_tx, float("inf"))
    with pytest.raises(ValueError):
        lab.add_cfo(good_tx, 1.0, 0.0)
    with pytest.raises(ValueError):
        lab.clip_signal(good_tx, 0.0)
    with pytest.raises(ValueError):
        lab.papr_db(np.array([], dtype=complex))
    with pytest.raises(ValueError):
        lab.evm_rms(good_syms, good_syms[:-1])
    with pytest.raises(ValueError):
        lab.deterministic_bits(0)
