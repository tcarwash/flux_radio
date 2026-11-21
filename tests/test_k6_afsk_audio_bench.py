"""Tests for tools/k6_afsk_audio_bench.py (host-only AFSK quality bench).

Covers: determinism, 1200/2200 spectral identity, phase continuity,
clean BER=0, noisy non-ideal behavior, CLI gen->analyze roundtrip, and
the absence of any packet-decode claims (BER needs known bits + known
symbol timing, never blind decode).
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

BENCH = Path(__file__).resolve().parents[1] / "tools" / "k6_afsk_audio_bench.py"


def load_bench():
    spec = importlib.util.spec_from_file_location("k6_afsk_audio_bench", BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


b = load_bench()


def test_determinism_same_seed_identical():
    bits_a = b.generate_bits(1200, seed=1)
    bits_b = b.generate_bits(1200, seed=1)
    assert bits_a == bits_b
    x = b.generate_afsk(bits_a, snr_db=0.0)
    y = b.generate_afsk(bits_b, snr_db=0.0)
    assert np.array_equal(x, y)


def test_different_seeds_differ():
    assert b.generate_bits(1200, seed=1) != b.generate_bits(1200, seed=2)


def test_spectral_identity_mark_and_space():
    # All-MARK must concentrate at 1200 Hz, all-SPACE at 2200 Hz,
    # confirmed two independent ways: detector scores and raw FFT peaks.
    for bits, freq, sign in (([1] * 300, 1200.0, 1), ([0] * 300, 2200.0, -1)):
        x = b.generate_afsk(bits)
        res = b.analyze(x, bits)
        assert res["ber"] == 0.0
        mean_score = float(np.mean(res["scores"]))
        assert sign * mean_score > 0.9
        spec = np.abs(np.fft.rfft(x))
        freqs = np.fft.rfftfreq(x.size, d=1.0 / b.SAMPLE_RATE)
        peak = float(freqs[int(np.argmax(spec[1:]) + 1)])
        assert abs(peak - freq) < 20.0, (freq, peak)


def test_phase_continuity_no_jumps():
    # Continuous-phase construction: worst-case per-sample step for a
    # 0.7-amplitude 2200 Hz sine at 48 kHz is ~0.201; anything near pi
    # would indicate a phase jump at a symbol boundary.
    bits = b.generate_bits(1200, seed=1)
    x = b.generate_afsk(bits)
    assert float(np.max(np.abs(np.diff(x)))) < 0.22


def test_clean_ber_zero():
    bits = b.generate_bits(1200, seed=1)
    res = b.analyze(b.generate_afsk(bits), bits)
    assert res["errors"] == 0
    assert res["ber"] == 0.0
    assert res["sample_rate"] == b.SAMPLE_RATE
    assert res["peak_abs"] == pytest.approx(0.7, abs=0.01)
    assert res["clipped_fraction"] == 0.0
    assert len(res["scores"]) == len(bits)
    assert all(-1.0 <= s <= 1.0 for s in res["scores"])


def test_noise_behavior_non_ideal():
    bits = b.generate_bits(1200, seed=1)
    clean = b.analyze(b.generate_afsk(bits), bits)
    noisy = b.analyze(b.generate_afsk(bits, snr_db=0.0), bits)
    assert noisy["ber"] > 0.0  # 0 dB in-band: a real detector must err
    assert noisy["margin_db"] < clean["margin_db"]


def test_ber_needs_known_bits_and_timing():
    bits = b.generate_bits(500, seed=1)
    x = b.generate_afsk(bits)
    flipped = [1 - v for v in bits]
    assert b.analyze(x, flipped)["ber"] > 0.9  # wrong key -> ~100% error
    with pytest.raises(ValueError):
        b.analyze(x[:100], bits)  # truncated input: timing/length unusable


def test_cli_roundtrip(tmp_path):
    out = tmp_path / "bench-out"
    gen = subprocess.run(
        [sys.executable, str(BENCH), "gen", "--out-dir", str(out),
         "--n-symbols", "600", "--seed", "7"],
        capture_output=True, text=True, check=False)
    assert gen.returncode == 0, gen.stderr
    wav = out / "afsk-bench.wav"
    meta = out / "afsk-bench.meta.json"
    assert wav.exists() and meta.exists()
    expected = json.loads(meta.read_text())["bits"]
    assert len(expected) == 600
    ana = subprocess.run(
        [sys.executable, str(BENCH), "analyze",
         "--wav", str(wav), "--meta", str(meta)],
        capture_output=True, text=True, check=False)
    assert ana.returncode == 0, ana.stderr
    summary = json.loads(ana.stdout)
    assert summary["ber"] == 0.0
    assert summary["errors"] == 0
    assert summary["n_symbols"] == 600
    assert summary["sample_rate"] == 48000


def test_cli_rejects_metadata_sample_rate_mismatch(tmp_path):
    out = tmp_path / "bench-out"
    gen = subprocess.run(
        [sys.executable, str(BENCH), "gen", "--out-dir", str(out), "--n-symbols", "32"],
        capture_output=True, text=True, check=False)
    assert gen.returncode == 0, gen.stderr
    meta = out / "afsk-bench.meta.json"
    payload = json.loads(meta.read_text(encoding="utf-8"))
    payload["sample_rate"] = 96000
    meta.write_text(json.dumps(payload), encoding="utf-8")
    ana = subprocess.run(
        [sys.executable, str(BENCH), "analyze", "--wav", str(out / "afsk-bench.wav"),
         "--meta", str(meta)], capture_output=True, text=True, check=False)
    assert ana.returncode == 2
    assert "WAV rate" in ana.stderr


def test_cli_refuses_repo_output_without_flag():
    repo_dir = Path(__file__).resolve().parents[1] / "results_cli_probe"
    gen = subprocess.run(
        [sys.executable, str(BENCH), "gen", "--out-dir", str(repo_dir),
         "--n-symbols", "10"],
        capture_output=True, text=True, check=False)
    assert gen.returncode == 2
    assert not repo_dir.exists()


def test_no_false_packet_decode_claims():
    for name in ("decode_packet", "decode", "hdlc", "ax25", "crc", "desense"):
        assert not hasattr(b, name), name
    src = BENCH.read_text(encoding="utf-8").lower()
    assert "not a packet modem" in src
    bits = b.generate_bits(64, seed=3)
    res = b.analyze(b.generate_afsk(bits), bits)
    assert set(res) & {"packets", "packet", "frames", "frame", "crc"} == set()
