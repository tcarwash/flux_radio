"""Tests for the offline CW/keying envelope reference (sim/modulation/cw_envelope.py).

Simulation-only checks: envelope shape, timing, bounds, IQ continuity, and a
reproducible FFT leakage comparison of shaped vs rectangular keying. These
assert model properties; they are not measurements of hardware clicks, spurs,
or APC behavior, and no such claim is made.

The module is loaded by file path so this test needs no sim/modulation
package ``__init__`` and changes no shared files. Worker does not run these;
run with:  python -m pytest tests/test_modlab_cw_envelope.py -v
SPDX-License-Identifier: MIT"""

import importlib.util
import os

import numpy as np
import pytest

MOD_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "sim", "modulation", "cw_envelope.py",
)


def load_module():
    assert os.path.isfile(MOD_PATH), f"missing reference module: {MOD_PATH}"
    spec = importlib.util.spec_from_file_location("cw_envelope_ref", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cw = load_module()
FS = 8000.0


def test_cq_pattern_timing_and_keyup_gaps():
    sched = cw.text_to_schedule("CQ", 20)
    dit = 1.2 / 20
    # C (-.-.): on 3+1+3+1 = 8 dits; Q (--.-): on 3+3+1+3 = 10 dits
    on_total = sum(s for on, s in sched if on)
    assert on_total == pytest.approx(18 * dit)
    # C: 8 on + 4 intra-gap + 2 letter-pad = 14 dits; Q: 10 + 4 + 2 = 16 dits
    assert cw.schedule_seconds(sched) == pytest.approx(30 * dit)
    env = cw.raised_cosine_envelope(sched, FS, 0.005)
    assert len(env) == round(cw.schedule_seconds(sched) * FS)
    assert env[0] == 0.0 and env[-1] == 0.0  # zero after key-up
    # the C->Q letter gap (last 3 dits of C's 14-dit span) is exactly zero
    n_dit = round(dit * FS)
    assert np.all(env[11 * n_dit:14 * n_dit] == 0.0)


def test_ramp_quantized_bounded_no_overshoot():
    sched = cw.schedule_from_durations([(0.1, 0.05)])
    ramp_s = 0.005
    env = cw.raised_cosine_envelope(sched, FS, ramp_s)
    r = round(ramp_s * FS)
    assert r == 40
    assert env.min() >= 0.0 and env.max() <= 1.0
    assert env.max() == pytest.approx(1.0)
    assert np.all(np.diff(env[:r]) > 0)  # monotone rise over exactly r samples
    assert np.all(env[round(0.1 * FS):] == 0.0)  # gap is exactly zero
    assert np.all(np.isfinite(env))


def test_zero_ramp_matches_rectangular():
    sched = cw.text_to_schedule("CQ", 20)
    shaped = cw.raised_cosine_envelope(sched, FS, 0.0)
    rect = cw.rectangular_envelope(sched, FS)
    np.testing.assert_array_equal(shaped, rect)
    assert set(np.unique(rect)).issubset({0.0, 1.0})


def test_two_short_pulses_with_long_ramp():
    # pulses shorter than 2x ramp: ramp clamps, peak still reaches 1.0
    sched = cw.schedule_from_durations([(0.002, 0.003), (0.002, 0.0)])
    env = cw.raised_cosine_envelope(sched, FS, 0.005)
    assert len(env) == round(0.007 * FS)
    assert env.max() == pytest.approx(1.0)
    assert env.min() >= 0.0 and env.max() <= 1.0
    assert env[-1] == 0.0
    assert cw.max_edge_step(env) < 1.0


def test_high_wpm_runs_clean():
    sched = cw.text_to_schedule("CQ", 60)
    env = cw.raised_cosine_envelope(sched, FS, 0.005)
    assert len(env) > 0 and np.all(np.isfinite(env))
    assert env.min() >= 0.0 and env.max() <= 1.0


def test_empty_pattern():
    assert cw.text_to_schedule("", 20) == []
    env = cw.raised_cosine_envelope([], FS, 0.005)
    assert env.size == 0
    iq = cw.cw_iq(env, FS)
    assert iq.size == 0
    assert cw.max_edge_step(env) == 0.0
    assert cw.leakage_ratio(iq, FS, 100.0) == 0.0


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        cw.dit_seconds(0)
    with pytest.raises(ValueError):
        cw.text_to_schedule("CQ!", 20)  # unsupported character
    with pytest.raises(ValueError):
        cw.schedule_from_durations([(-0.1, 0.0)])
    with pytest.raises(ValueError):
        cw.raised_cosine_envelope(cw.text_to_schedule("E", 20), 0, 0.005)
    with pytest.raises(ValueError):
        cw.raised_cosine_envelope(cw.text_to_schedule("E", 20), FS, -0.001)
    with pytest.raises(ValueError):
        cw.cw_iq(np.ones(8), FS, FS)  # carrier beyond Nyquist


def test_iq_magnitude_and_continuous_phase():
    sched = cw.text_to_schedule("T", 20)  # single dash: long flat plateau
    env = cw.raised_cosine_envelope(sched, FS, 0.004)
    freq = 500.0
    iq = cw.cw_iq(env, FS, freq)
    np.testing.assert_allclose(np.abs(iq), env, rtol=1e-12, atol=1e-15)
    plateau = env == 1.0
    assert plateau.sum() > 10
    steps = np.diff(np.unwrap(np.angle(iq[plateau])))
    np.testing.assert_allclose(steps, 2 * np.pi * freq / FS, rtol=1e-9)
    # zero carrier: IQ is the real envelope, no phase jumps possible
    iq0 = cw.cw_iq(env, FS, 0.0)
    np.testing.assert_allclose(iq0.imag, 0.0, atol=1e-15)


def test_shaped_beats_rectangular_numerically():
    # baseband (freq 0) so the cutoff measures keying sidebands, not the carrier offset
    sched = cw.text_to_schedule("CQ", 20)
    res = cw.compare_rectangular(sched, FS, 0.005, freq_hz=0.0)
    assert res["step_rect"] == pytest.approx(1.0)
    assert res["step_shaped"] < res["step_rect"]
    assert res["leakage_rect"] > 0.0
    assert 0.0 <= res["leakage_shaped"] < res["leakage_rect"]
    assert res["leakage_ratio"] < 1.0
    # deterministic: identical inputs give identical outputs
    res2 = cw.compare_rectangular(sched, FS, 0.005, freq_hz=0.0)
    assert res2["leakage_shaped"] == res["leakage_shaped"]
    assert res2["leakage_rect"] == res["leakage_rect"]
