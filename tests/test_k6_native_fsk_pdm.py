"""Offline RF-frequency-DAC feasibility: never opens serial or keys a radio."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_native_fsk_pdm import (
    average_offsets, ideal_rf_model, pdm_encode, probe_vector,
)


def test_deterministic_8level_probe():
    ids1, offsets1 = probe_vector(seed=19)
    ids2, offsets2 = probe_vector(seed=19)
    assert np.array_equal(ids1, ids2)
    assert np.array_equal(offsets1, offsets2)
    assert len(np.unique(ids1)) >= 5
    assert set(offsets1).issubset(set(np.arange(8) * 300. - 1050.))


def test_duty_average_tracks_target_with_quantization():
    target = np.array([-1050, -750, -450, -150, 150, 450, 750, 1050.])
    for baud in (2400, 4800):
        bits = pdm_encode(target, native_baud=baud)
        assert len(bits) == len(target) * (baud // 75)
        offsets = average_offsets(bits, native_baud=baud)
        assert np.max(np.abs(offsets - target)) <= 8000 * 75 / baud


def test_error_integrator_continues_across_symbols():
    samples = np.array([123., -321., 701., -740.])
    together = pdm_encode(samples)
    # Each run begins from zero, so independent encoding is not generally
    # equivalent to continuous phase-error integration.
    independent = np.concatenate([pdm_encode([s]) for s in samples])
    assert np.count_nonzero(together != independent) > 0


def test_full_rate_stream_and_phase_continuity():
    bits = pdm_encode([0, 1000, -1000, 300, -300])
    model = ideal_rf_model(bits)
    assert len(model["iq"]) == len(bits) * 40
    assert np.max(np.abs(model["iq"])) == pytest.approx(1, abs=1e-10)
    assert np.max(np.abs(np.angle(model["iq"][1:] *
                                  model["iq"][:-1].conj()))) < np.pi
    assert model["occupied_bw_99_hz"] > 0
    assert 0 < model["power_outside_plus_minus_3khz"] < 1


def test_input_guards():
    for inp in ([], [float("nan")], [4001], [[0, 1]]):
        with pytest.raises(ValueError):
            pdm_encode(inp)
    with pytest.raises(ValueError):
        pdm_encode([50], native_baud=2300)
    with pytest.raises(ValueError):
        pdm_encode([50], native_baud=0)
    with pytest.raises(ValueError):
        ideal_rf_model([0, 1], sample_rate=11025)
    with pytest.raises(ValueError):
        average_offsets(np.array([2] * 32, dtype=np.uint8))


def test_realized_error_is_improved_at_4800():
    _, target = probe_vector(n_symbols=16, seed=17)
    a = average_offsets(pdm_encode(target, native_baud=2400), native_baud=2400)
    b = average_offsets(pdm_encode(target, native_baud=4800), native_baud=4800)
    assert np.mean((b-target)**2) < np.mean((a-target)**2)


def test_cli_output_is_offline_and_bounded(tmp_path):
    out = tmp_path / "pdm"
    p = subprocess.run(
        [sys.executable, str(ROOT/"tools/k6_native_fsk_pdm.py"),
         "--out-dir", str(out), "--n-symbols", "16",
         "--native-baud", "4800"],
        capture_output=True, text=True, timeout=10,
    )
    assert p.returncode == 0, p.stderr
    info = json.loads((out/"native-fsk-pdm.json").read_text())
    data = (out/"native-fsk-pdm.bin").read_bytes()
    assert info["simulation_only"] and not info["transmitted"]
    assert info["words"] == 64 and len(data) == 128
    assert info["payload_hex"] == data.hex()
    assert not list(tmp_path.rglob("*key*"))


def test_cli_rejects_overfull_fixed_fifo(tmp_path):
    p = subprocess.run(
        [sys.executable, str(ROOT/"tools/k6_native_fsk_pdm.py"),
         "--out-dir", str(tmp_path), "--native-baud", "4800",
         "--n-symbols", "32"],
        capture_output=True, text=True, timeout=10,
    )
    assert p.returncode != 0
    assert "max-words" in p.stderr
