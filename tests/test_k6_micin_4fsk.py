"""Deterministic, offline TX analog baseband waveform tests."""
from pathlib import Path
import json
import sys
import wave

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_micin_4fsk import LEVELS, SYNC, make_symbols, make_shaped_4fsk, calibration_tone, run


def test_symbol_vector_has_fixed_sync_and_repeats_by_seed():
    x, y = make_symbols(128, 17), make_symbols(128, 17)
    assert np.array_equal(x, y)
    assert np.array_equal(x[:len(SYNC)], SYNC)
    assert len(set(x)) == 4 and len(x) == 128


def test_shaped_levels_have_expected_order_and_bounds():
    ids = np.array([0]*32 + [1]*32 + [2]*32 + [3]*32, dtype=np.uint8)
    y = make_shaped_4fsk(ids, amplitude=.03)
    assert abs(y).max() <= .03
    assert np.all(y[:1000] == 0)
    for i, target in enumerate(LEVELS):
        center = 1920 + (32*i + 16)*40
        assert y[center] == pytest.approx(.03*target, abs=0.00002)
    assert np.max(np.abs(np.diff(y))) < .006


def test_zero_level_symbol_might_be_ac_coupled_away():
    # The average signed level of an unbalanced real packet is generally not
    # zero. An AC-coupled soundcard can distort precisely this DC component.
    y = make_shaped_4fsk([3]*128)
    assert y.mean() > .01


def test_calibration_is_low_level_click_faded():
    x = calibration_tone()
    assert len(x) == 21600
    assert np.max(np.abs(x)) <= .03
    assert x[0] == 0 and x[-1] == 0
    assert np.std(x) > 0


@pytest.mark.parametrize("bad", [[], [4], [-1], [[1,2]], [[1],[2]]])
def test_reject_invalid_symbols(bad):
    with pytest.raises(ValueError):
        make_shaped_4fsk(bad)


def test_bad_sample_clock_or_level_rejected():
    for kwargs in [
        {"sample_rate":44100}, {"symbol_baud":0}, {"amplitude":.2},
        {"bt":.01}, {"sample_rate":2000}, {"guard_ms":300},
    ]:
        with pytest.raises(ValueError):
            make_shaped_4fsk([0,1,2,3], **kwargs)


def test_cli_outputs_wav_and_known_metadata(tmp_path):
    info = run(tmp_path, n_symbols=128, seed=12)
    assert info["offline_only"] and not info["transmitted"]
    assert info["expected_duration_s"] == pytest.approx(128/1200 + .08)
    assert info["fullscale_amplitude"] == .03
    meta = json.loads((tmp_path/"micin-4fsk.json").read_text())
    assert meta["symbol_ids"][:16] == SYNC.tolist()
    for name in ("micin-4fsk.wav", "micin-1khz.wav"):
        with wave.open(str(tmp_path/name), "rb") as f:
            assert f.getframerate() == 48000
            assert f.getsampwidth() == 2
            assert f.getnchannels() == 1
            wave_data = np.frombuffer(f.readframes(f.getnframes()),dtype="<i2")
            assert np.max(np.abs(wave_data)) < 3500
    assert (tmp_path/"micin-4fsk.wav").read_bytes()


def test_deterministic_wav(tmp_path):
    run(tmp_path/"a", n_symbols=128, seed=9)
    run(tmp_path/"b", n_symbols=128, seed=9)
    assert ((tmp_path/"a"/"micin-4fsk.wav").read_bytes() ==
            (tmp_path/"b"/"micin-4fsk.wav").read_bytes())


def test_reject_long_keyed_payload(tmp_path):
    with pytest.raises(ValueError):
        run(tmp_path, n_symbols=2400, symbol_baud=1000)
