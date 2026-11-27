"""Alias-sweep regressions: full voice-band peak and truthful evidence."""
import json
from pathlib import Path
import numpy as np
import pytest
from tools.k6_gp0_alias_sweep import peak,sweep

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT/"docs/evidence/sotto-gp0-cic3-wide-alias-sweep-20261126.json"


def test_strongest_band_peak_finds_unexpected_alias_not_nominal_bin():
    t=np.arange(8000)/8000
    x=(1000*np.sin(2*np.pi*1000*t)+2000*np.sin(2*np.pi*1900*t)).astype(np.int16)
    hz,ampl=peak(x)
    assert abs(hz-1900)<1
    assert 1800<ampl<2200


def test_dense_sweep_evidence_explicitly_records_7500_hz_counterexample():
    result=json.loads(EVIDENCE.read_text())
    assert result["offline_only"]
    assert result["fixture_count"]==len(result["rows"])==31
    assert result["output_voice_band_hz"]==[80,3900]
    worst=result["worst_cic3_vs_aa3"][0]
    assert worst["input_hz"]==7500
    assert worst["cic3_minus_aa3_db"]>6
    assert abs(worst["cic3_peak_hz"]-3407)<5
    assert any(row["input_hz"]==9000 for row in result["rows"]) is False


def test_invalid_sweep_bounds_fail_before_model_access():
    for kws in (dict(frequencies=[]),dict(frequencies=[9000],duration=.2),
                dict(frequencies=[9000],density_amplitude=9.0),
                dict(frequencies=[float("nan")])):
        with pytest.raises(ValueError):
            sweep(**kws)


def test_one_synthetic_frequency_remains_finite_and_bounded():
    result=sweep(frequencies=[9000],duration=.40)
    assert result["fixture_count"]==1
    row=result["rows"][0]
    assert np.isfinite(row["cic3_minus_aa3_db"])
    assert 80<=row["cic3_peak_hz"]<=3900
