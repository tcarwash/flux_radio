"""Full synthetic FM IQ -> independent discriminator -> public-sync scorer tests."""
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import k6_whisper8 as w  # noqa: E402
import k6_whisper8_iq as rx  # noqa: E402


def fixture_iq(symbols, *, start_audio_samples=256, deviation_hz=800):
    wave = np.concatenate((
        np.zeros(start_audio_samples),
        w.render_audio(symbols),
        np.zeros(256),
    ))
    fs = 1024000
    audio = np.repeat(wave, fs // w.SAMPLE_RATE)
    phase = np.cumsum(
        (2.0 * np.pi / fs) * (-250000.0 + deviation_hz * audio),
        dtype=np.float64,
    )
    return np.exp(1j * phase).astype(np.complex64)


def test_synthetic_complex_iq_full_chain_and_public_holdout():
    iq = fixture_iq(w.FIXTURE)
    output = rx.iq_to_discriminator_audio(iq)
    scored = rx.score_fixture(output)
    assert scored["verdict"] == "PASS", scored
    assert scored["first_public_sync_candidate"]["holdout_errors"] == 0
    assert scored["first_public_sync_candidate"]["public_sync_tones"] == 24


def test_synthetic_iq_wrong_holdout_rejected():
    symbols = list(w.FIXTURE)
    symbols[-3] = (symbols[-3] + 1) % 8
    scored = rx.score_fixture(rx.iq_to_discriminator_audio(fixture_iq(symbols)))
    assert scored["verdict"] == "FAIL", scored
    assert scored["first_public_sync_candidate"]["holdout_errors"] >= 1


def test_synthetic_iq_no_public_sync_rejected():
    symbols = list(w.FIXTURE)
    symbols[:24] = [5] * 24
    scored = rx.score_fixture(rx.iq_to_discriminator_audio(fixture_iq(symbols)))
    assert scored["verdict"] == "FAIL", scored


def test_inputs_invalid():
    with pytest.raises(ValueError, match="complex"):
        rx.iq_to_discriminator_audio(np.zeros(600000))
    with pytest.raises(ValueError, match="shorter"):
        rx.iq_to_discriminator_audio(np.zeros(100, dtype=np.complex64))
    with pytest.raises(ValueError, match="not enough"):
        rx.score_fixture(np.zeros(12))
