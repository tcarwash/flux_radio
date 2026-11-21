"""Deterministic synthetic tests for measured-rate Bell 202 soft metrics."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tools/k6_bell202_soft.py"
spec = importlib.util.spec_from_file_location("bell202", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def make_signal(fs, bits, lead=80, tone_offset=0, snr_db=None, seed=7):
    rng = np.random.default_rng(seed)
    n = int(np.ceil(len(bits) * fs / 1200))
    pos = np.arange(n)
    idx = np.minimum((pos * 1200 / fs).astype(int), len(bits) - 1)
    freq = np.where(np.asarray(bits)[idx] == 1, 1200 + tone_offset, 2200 + tone_offset)
    phase = np.cumsum(2 * np.pi * freq / fs)
    y = 0.5 * np.sin(phase)
    if snr_db is not None:
        noise = rng.standard_normal(n)
        noise *= np.sqrt(np.mean(y**2)/np.mean(noise**2) / (10**(snr_db / 10)))
        y += noise
    return np.r_[np.zeros(lead), y]


@pytest.mark.parametrize("fs,lead", [(48000, 61), (17534, 83), (17504, 29)])
def test_known_sync_recovers_arbitrary_rate(fs, lead):
    bits = np.random.default_rng(17).integers(0, 2, 220).tolist()
    x = make_signal(fs, bits, lead=lead)
    r = m.decode_known_reference(x, fs, bits, max_start=120)
    assert abs(r["symbol_start_sample"] - lead) <= 2
    assert r["errors"] == 0
    assert r["sync_score"] > 0.75


def test_frequency_offset_search():
    bits = np.random.default_rng(12).integers(0, 2, 160).tolist()
    x = make_signal(17534, bits, lead=64, tone_offset=50)
    r = m.decode_known_reference(x, 17534, bits, max_start=110)
    assert r["errors"] == 0
    assert r["tone_offset_hz"] == 50


def test_noisy_capture_degrades_soft_decisions():
    bits = np.random.default_rng(11).integers(0, 2, 256).tolist()
    clean = m.decode_known_reference(make_signal(17534, bits), 17534, bits)
    noisy = m.decode_known_reference(make_signal(17534, bits, snr_db=5), 17534, bits)
    assert clean["errors"] == 0
    assert noisy["ber"] <= 0.12
    assert noisy["sync_score"] < clean["sync_score"]


def test_noise_only_refused():
    bits = np.random.default_rng(10).integers(0, 2, 180).tolist()
    rng = np.random.default_rng(33)
    x = rng.standard_normal(2900)
    with pytest.raises(ValueError, match="synchronization"):
        m.decode_known_reference(x, 17534, bits, max_start=120)


def test_bad_arguments_refused():
    with pytest.raises(ValueError):
        m.decode_known_reference(np.zeros(150), 1000, [1] * 80)
    with pytest.raises(ValueError):
        m.decode_known_reference(np.zeros(3000), 17534, [2] * 80)
    with pytest.raises(ValueError):
        m.decode_known_reference(np.zeros(3000), 17534, [0] * 80, sync_len=12)
