"""Offline-only native FSK waveform bench regression tests.

No hardware, serial ports, RF transmitter, or firmware are used.
"""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from k6_native_fsk_pdm import pdm_encode, pdm_encode_frequency_samples
from k6_fsk_waveform_bench import analyze, make_frequency_trajectory


def test_legacy_pdm_output_matches_chip_trajectory_for_each_symbol():
    symbols = np.array([-1050.0, -450.0, 150.0, 750.0])
    original = pdm_encode(symbols, native_baud=2400,
                          symbol_baud=75, deviation_hz=1200)
    samples = np.repeat(symbols, 2400 // 75)
    assert np.array_equal(original,
                          pdm_encode_frequency_samples(samples,
                                                       deviation_hz=1200))


@pytest.mark.parametrize("kind", [
    "4fsk", "8fsk", "gaussian-8fsk", "chirp", "msk600",
])
def test_all_waveforms_are_chip_aligned_and_bounded(kind):
    a, baud = make_frequency_trajectory(
        kind, native_baud=4800, symbol_baud=75,
        deviation_hz=1200, symbols=24, seed=7)
    b, _ = make_frequency_trajectory(
        kind, native_baud=4800, symbol_baud=75,
        deviation_hz=1200, symbols=24, seed=7)
    assert np.array_equal(a, b)
    assert len(a) == 24 * 4800 // baud
    assert np.max(np.abs(a)) <= 1200
    bits = pdm_encode_frequency_samples(a, deviation_hz=1200)
    assert bits.size == a.size
    assert set(np.unique(bits)).issubset({0, 1})


def test_msk_ideal_frequency_has_modulation_index_half():
    target, baud = make_frequency_trajectory(
        "msk600", native_baud=4800, symbol_baud=75,
        deviation_hz=1200, symbols=16, seed=4)
    assert baud == 600
    assert set(np.unique(target)) == {-150.0, 150.0}
    assert 2 * 150.0 / baud == 0.5


def test_frequency_chip_invalid_inputs():
    for x in ([], [float("nan")], [1300], [[0, 1]]):
        with pytest.raises(ValueError):
            pdm_encode_frequency_samples(x, deviation_hz=1200)
    with pytest.raises(ValueError):
        pdm_encode_frequency_samples([0], deviation_hz=0)
    with pytest.raises(ValueError):
        make_frequency_trajectory("8fsk", native_baud=1200, symbol_baud=75,
                                  deviation_hz=900, symbols=20, seed=2)
    with pytest.raises(ValueError):
        make_frequency_trajectory("8fsk", native_baud=3600, symbol_baud=75,
                                  deviation_hz=1200, symbols=20, seed=2)


def test_phase_pulses_obey_chip_resolution_guard():
    with pytest.raises(ValueError):
        make_frequency_trajectory(
            "phase-pulse", native_baud=1200, symbol_baud=75,
            deviation_hz=1200, symbols=24, seed=3)
    target, baud = make_frequency_trajectory(
        "phase-pulse", native_baud=4800, symbol_baud=75,
        deviation_hz=1200, symbols=24, seed=3)
    assert baud == 75
    assert np.count_nonzero(target) == 8  # 4 pulses * 2 chips


def test_lower_deviation_improves_complex_rf_fidelity_in_ideal_model():
    low, _ = analyze("8fsk", native_baud=4800, symbol_baud=75,
                     deviation_hz=1200, symbols=24, seed=17)
    high, _ = analyze("8fsk", native_baud=4800, symbol_baud=75,
                      deviation_hz=4000, symbols=24, seed=17)
    assert low["complex_coherence"] > high["complex_coherence"]
    assert low["ideal_rf_obw_99_hz"] < high["ideal_rf_obw_99_hz"]
    assert not low["verified_on_hardware"]


def test_metrics_are_bounded_and_the_frame_limit_is_explicit():
    result, _ = analyze("msk600", native_baud=4800, symbol_baud=75,
                        deviation_hz=1200, symbols=24, seed=17)
    assert 0 <= result["complex_coherence"] <= 1
    assert result["normalized_evm"] >= 0
    assert result["native_fsk_bits"] == 24 * 8
    assert not result["exceeds_existing_64_word_lab_frame"]

    large, _ = analyze("8fsk", native_baud=4800, symbol_baud=75,
                       deviation_hz=1200, symbols=24, seed=17)
    assert large["exceeds_existing_64_word_lab_frame"]


def test_cli_writes_offline_vectors_and_report(tmp_path):
    p = subprocess.run([
        sys.executable, str(ROOT/"tools/k6_fsk_waveform_bench.py"),
        "--out-dir", str(tmp_path), "--modes", "8fsk,msk600",
        "--native-bauds", "2400,4800",
        "--deviations-hz", "1200", "--symbols", "16",
    ], capture_output=True, text=True, timeout=25)
    assert p.returncode == 0, p.stderr
    report = json.loads((tmp_path/"report.json").read_text())
    assert report["receive_only"] and not report["rf_transmission"]
    assert len(report["cases"]) == 4
    for row in report["cases"]:
        vectors = np.load(tmp_path/row["vectors"])
        assert len(vectors["native_bits"]) == row["native_fsk_bits"]
        assert len(vectors["desired_hz_per_native_chip"]) == row["native_fsk_bits"]
    assert not list(tmp_path.glob("*.bin"))  # no radio-loadable bitstream


def test_cli_rejects_unrepresentable_waveform(tmp_path):
    p = subprocess.run([
        sys.executable, str(ROOT/"tools/k6_fsk_waveform_bench.py"),
        "--out-dir", str(tmp_path), "--modes", "8fsk",
        "--native-bauds", "2400", "--deviations-hz", "800",
    ], capture_output=True, text=True, timeout=15)
    assert p.returncode != 0
    assert "exceed" in p.stderr
