"""Known-training FSK phase response model: pure and synthetic RF checks."""
from pathlib import Path
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT/"tests"))
from k6_fsk_phase_fir import fit_increments,analyze_capture,design,TRAIN,HELDOUT
from k6_fsk_pdm_chip_decode import decode,validate_manifest
from test_k6_fsk_pdm_chip_decode import simulate_iq


@pytest.fixture(scope="module")
def manifest():
    return json.loads((ROOT/"docs/k6-fsk-pdm300-vector.json").read_text())


def synthetic_phase_increments(manifest, *,seed=14):
    bits,_,_=validate_manifest(manifest)
    rng=np.random.default_rng(seed)
    state=2*bits.astype(float)-1
    result=.042 + 1.21*state
    for offset,weight in [(-1,.22),(1,.11)]:
        at=np.arange(3,1021)+offset
        result[3:1021] += weight*state[at]
    result += rng.normal(0,.014,size=1024)
    return result,bits


def test_training_fits_true_short_memory_without_using_holdout(manifest):
    increments,bits=synthetic_phase_increments(manifest)
    results=fit_increments(increments,bits)
    assert results["training_chip_increments"]==573
    assert results["heldout_chip_increments"]==445
    assert results["heldout_chip_range"]==[576,1020]
    assert results["no_heldout_measurements_used_in_coefficient_fit"] is True
    fitted=results["models"]["adjacent_three_tap"]
    assert fitted["trained_taps_rad_per_chip"] == pytest.approx([.22,1.21,.11],abs=.012)
    assert fitted["heldout_chip_increment_rmse_rad"]<.035
    assert fitted["heldout_accumulated_phase_coherence"]>.88
    assert fitted["heldout_accumulated_phase_coherence"] > (
        results["models"]["single_binary"]["heldout_accumulated_phase_coherence"]+.3)


def test_modifying_only_heldout_measurements_cannot_change_trained_coefficients(manifest):
    inc,bits=synthetic_phase_increments(manifest)
    baseline=fit_increments(inc,bits)
    altered=inc.copy()
    altered[HELDOUT]+=.15*np.sin(np.arange(len(HELDOUT))*.11)
    changed=fit_increments(altered,bits)
    for model in ("single_binary","causal_three_tap","adjacent_three_tap","adjacent_seven_tap"):
        a=baseline["models"][model]
        b=changed["models"][model]
        assert a["trained_taps_rad_per_chip"]==b["trained_taps_rad_per_chip"]
        assert a["trained_intercept_rad_per_chip"]==b["trained_intercept_rad_per_chip"]
        assert a["heldout_chip_increment_rmse_rad"]!=b["heldout_chip_increment_rmse_rad"]


def test_synthetic_full_iq_yields_coherent_prediction(manifest):
    raw=simulate_iq(manifest,seed=18)
    acquired=decode(raw,manifest)
    result=analyze_capture(raw,manifest,acquired)
    assert result["heldout_chip_increments"]==445
    assert result["models"]["adjacent_three_tap"]["heldout_chip_increment_rmse_rad"]<.14
    assert result["models"]["adjacent_three_tap"]["heldout_accumulated_phase_coherence"]>.40


def test_invalid_phase_training_and_capture_rejected(manifest):
    bits,_,_=validate_manifest(manifest)
    with pytest.raises(ValueError):
        fit_increments(np.zeros(1024),bits)
    with pytest.raises(ValueError):
        fit_increments(np.zeros(1023),bits)
    with pytest.raises(ValueError):
        design(bits,np.array([0]),(-1,0,1))
    bad={"known_pilot_acquired":False,"native_heldout_bits":448,"native_baud_estimate":4800}
    with pytest.raises(ValueError):
        analyze_capture(simulate_iq(manifest),manifest,bad)


def test_analysis_only_no_transmitter_control():
    code=(ROOT/"tools/k6_fsk_phase_fir.py").read_text()
    for forbidden in ("import serial","from k6_live","run_module(",
                      "RtlCapture(","tx_on(","ptt_burst("):
        assert forbidden not in code
