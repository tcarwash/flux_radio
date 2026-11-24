"""Reproducible offline checks for the experimental, NON-Olivia Whisper8 mode."""
import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "tools" / "k6_whisper8.py"
SPEC = importlib.util.spec_from_file_location("k6_whisper8", PY)
w = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(w)


@pytest.mark.parametrize("message", [b"", b"AG7SU", b"CQ AG7SU CN85", bytes(range(48))])
def test_symbols_crc_round_trip(message):
    symbols = w.packet_symbols(message)
    assert tuple(symbols[:24]) == w.PREAMBLE + w.SYNC
    assert w.decode_symbols(symbols) == message


def test_one_wrong_tone_in_each_triple_corrected():
    symbols = list(w.packet_symbols(b"HELLO"))
    prefix = len(w.PREAMBLE) + len(w.SYNC)
    for i in range(prefix, len(symbols), 3):
        symbols[i] = (symbols[i] + 3) % 8
    assert w.decode_symbols(symbols) == b"HELLO"


def test_two_wrong_tones_in_one_triple_rejected():
    symbols = list(w.packet_symbols(b"HELLO"))
    prefix = len(w.PREAMBLE) + len(w.SYNC)
    # Flip two of three copies of a symbol within the first payload byte.
    i = prefix + 9
    symbols[i] = symbols[i + 1] = (symbols[i] + 2) % 8
    with pytest.raises(ValueError, match="CRC"):
        w.decode_symbols(symbols)


def test_truncated_and_wrong_preamble_rejected():
    with pytest.raises(ValueError, match="exceeds"):
        w.packet_symbols(bytes(49))
    with pytest.raises(ValueError, match="truncated"):
        w.decode_symbols(w.packet_symbols(b"X")[:-9])
    symbols = list(w.packet_symbols(b"X"))
    symbols[0] = 3
    with pytest.raises(ValueError, match="preamble"):
        w.decode_symbols(symbols)


@pytest.mark.parametrize("snr_db", [None, 2.0, -4.0, -8.0])
def test_audio_recovery_with_noise_and_sample_offset(snr_db):
    payload = b"CQ AG7SU CN85"
    symbols = w.packet_symbols(payload)
    audio = w.render_audio(symbols, snr_db=snr_db, prepad_samples=91, seed=7)
    result, offset = w.recover_audio(audio)
    assert result == payload
    assert 0 <= offset <= 91


def test_negative_silence_and_noise_no_false_crc():
    symbols = w.packet_symbols(b"HELLO")
    count = len(symbols) * w.SAMPLES_PER_SYMBOL + 256
    for audio in (np.zeros(count), np.random.default_rng(123).normal(0, 1, count)):
        with pytest.raises(ValueError, match="no CRC-valid"):
            w.recover_audio(audio)


def test_fixture_and_frequency_math():
    assert w.SYMBOL_RATE == 31.25
    assert w.TONE_SPACING_HZ == 31.25
    assert w.TONES_HZ[0] == 1125.0
    assert w.TONES_HZ[-1] == 1343.75
    assert len(w.FIXTURE) == 32
    assert set(w.FIXTURE) == set(range(8))
    assert (ROOT / "c/k6/mod/whisper8_fixture.h").read_text() == w.fixture_header()
    assert w.reg71_word(1091.0) == 11263  # nearest lower hardware quantization


def _compile(tmp_path, name, flags):
    args = [
        "clang", "--target=armv6m-none-eabi", "-mcpu=cortex-m0", "-mthumb",
        "-std=c11", "-Oz", "-ffreestanding", "-fno-builtin", "-Werror",
        "-Wall", "-Wextra", *flags, "-c",
        str(ROOT / "c/k6/mod/whisper8_probe.c"), "-o", str(tmp_path / name),
    ]
    return subprocess.run(args, text=True, capture_output=True)


def test_cortex_m0_compiles_inert_and_gated(tmp_path):
    assert _compile(tmp_path, "inert.o", []).returncode == 0
    flags = ["-DW8_BENCH_ENABLE=1", "-DW8_RF_ENABLE=1"]
    result = _compile(tmp_path, "refused.o", flags)
    assert result.returncode != 0 and "RF output requires" in result.stderr
    flags += ["-DW8_DUMMY_LOAD_ACK=1", "-DW8_WATCHDOG_ACK=1"]
    result = _compile(tmp_path, "armed.o", flags)
    assert result.returncode == 0, result.stderr


def test_demod_rejects_bad_id_and_short_audio():
    with pytest.raises(ValueError, match="tone ID"):
        w.render_audio([8])
    with pytest.raises(ValueError, match="shorter"):
        w.recover_audio(np.zeros(5))
