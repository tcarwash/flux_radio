"""Host-only cross-capture FSK phase model tests. No radio or SDR control."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_phase_transfer import cross_validate, phase_increments
from k6_fsk_phase_fir import TRAIN,HELDOUT,design
from k6_fsk_pdm_chip_decode import validate_manifest

MANIFEST=json.loads((ROOT/"docs/k6-fsk-pdm300-vector.json").read_text())
BITS,_,_=validate_manifest(MANIFEST)


def synthetic_runs():
    rng=np.random.default_rng(919)
    valid=np.arange(3,1021)
    trueslopes=np.array([0.22,1.20,0.12])
    wave=design(BITS,valid,(-1,0,1))[:,1:]@trueslopes
    rows=[]
    for i in range(4):
        y=np.zeros(1024)
        y[valid]=0.2+0.035*i+wave+rng.normal(0,.001,size=len(valid))
        rows.append(y)
    return rows


def test_transferred_taps_and_pilot_offset_generalize_to_heldout():
    report=cross_validate(BITS,synthetic_runs())
    assert len(report["cases"])==4
    assert report["input_transmitted_bitstream_identical_across_all_cases"]
    for row in report["cases"]:
        model=row["models"]["adjacent_three_tap"]
        assert model["trained_other_captures"]==3
        assert model["heldout_chip_increments"]==len(HELDOUT)
        assert model["target_heldout_phase_accumulation_coherence"]>.995
        assert model["target_heldout_increment_rmse_rad"]<.01
        assert model["heldout_not_used_for_training_or_model_selection"]
        assert model["target_pilot_used_only_for_constant_offset"]
        assert model["donor_pilot_only_taps_rad_per_chip"]==pytest.approx(
            [0.22,1.20,0.12],abs=.01)


def test_heldout_corruption_cannot_change_donor_coefficients_or_target_pilot():
    truth=synthetic_runs()
    baseline=cross_validate(BITS,truth)
    corrupted=[row.copy() for row in truth]
    corrupted[0][HELDOUT] += 0.08  # Constant excess heldout phase slope.
    changed=cross_validate(BITS,corrupted)
    r0=baseline["cases"][0]["models"]["adjacent_three_tap"]
    c0=changed["cases"][0]["models"]["adjacent_three_tap"]
    assert c0["donor_pilot_only_taps_rad_per_chip"]==pytest.approx(
        r0["donor_pilot_only_taps_rad_per_chip"],abs=1e-12)
    assert c0["target_pilot_only_intercept_rad_per_chip"]==pytest.approx(
        r0["target_pilot_only_intercept_rad_per_chip"],abs=1e-12)
    assert c0["target_heldout_phase_accumulation_coherence"] < .2
    # Even as a donor, corrupted run 0 contributes ONLY its training pilot.
    r1=baseline["cases"][1]["models"]["adjacent_three_tap"]
    c1=changed["cases"][1]["models"]["adjacent_three_tap"]
    assert c1["target_heldout_phase_accumulation_coherence"]==pytest.approx(
        r1["target_heldout_phase_accumulation_coherence"],abs=1e-12)


def test_changed_physical_response_is_visible_when_left_out():
    original=synthetic_runs()
    changed=[x.copy() for x in original]
    valid=np.arange(3,1021)
    difference=design(BITS,valid,(-1,0,1))[:,1:]@np.array([-.21,.29,-.08])
    changed[-1][valid]+=difference
    out=cross_validate(BITS,changed)
    other=out["cases"][0]["models"]["adjacent_three_tap"]
    odd=out["cases"][3]["models"]["adjacent_three_tap"]
    assert odd["target_heldout_increment_rmse_rad"] > .1
    assert odd["target_heldout_increment_rmse_rad"] > other["target_heldout_increment_rmse_rad"]


def test_bad_vectors_and_unlocked_raw_rejected():
    with pytest.raises(ValueError):
        cross_validate(BITS,synthetic_runs()[:1])
    with pytest.raises(ValueError):
        cross_validate(BITS[:-1],synthetic_runs())
    with pytest.raises(ValueError,match="pilot"):
        phase_increments(np.zeros(100,dtype=np.uint8),{"known_pilot_acquired":False})


def test_no_hardware_control_imports():
    src=(ROOT/"tools/k6_fsk_phase_transfer.py").read_text()
    for bad in ("import serial","import pyrtlsdr","tx_on(",
                "fd_write(","run_module(","flash(","apc_set("):
        assert bad not in src
