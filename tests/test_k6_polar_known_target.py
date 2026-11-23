"""Synthetic independent ideal-FSK/APC target tests; no radio access."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_polar_known_target import make_target
from k6_iq_holdout_verify import verify


@pytest.fixture()
def manifest():
    return json.loads((ROOT/"docs/k6-fsk-lab-vector.json").read_text())


def test_manifest_target_shape_levels_and_phase(manifest):
    x,report=make_target(manifest)
    assert x.dtype==np.complex64
    assert len(x)==1024*20
    assert report["source_fifo_bits"]==1024
    assert report["nominal_amplitude_transition_count"]>=9
    assert report["physical_joint_fsk_apc_demonstrated"] is False
    assert np.min(np.abs(x))==pytest.approx(.8,abs=1e-6)
    assert np.max(np.abs(x))==pytest.approx(1.0,abs=1e-6)
    bits=np.unpackbits(np.frombuffer(bytes.fromhex(manifest["payload_bytes_hex"]),dtype=np.uint8))
    phase=np.unwrap(np.angle(x[:20*60]))
    derivative=np.diff(phase)*96000/(2*np.pi)
    # Only check within-bit steady frequency, never assume exact edge phase.
    for bit in range(1,55):
        got=float(np.median(derivative[bit*20+3:bit*20+16]))
        assert got==pytest.approx((2*int(bits[bit])-1)*1200,abs=.1)


def test_ideal_synthetic_capture_end_to_end(manifest):
    x,_=make_target(manifest)
    lag=53
    cfo=63.
    rng=np.random.default_rng(123)
    n=len(x)
    obs=np.r_[np.zeros(lag,complex),
              .75*x*np.exp(1j*(.4+2*np.pi*cfo*np.arange(n)/96000))+
              .001*(rng.normal(size=n)+1j*rng.normal(size=n)),
              np.zeros(70,complex)]
    report,_=verify(x,obs,fs=96000,pilot_samples=4800,
                    max_lag_samples=70,max_cfo_hz=100)
    assert report["pilot_only_estimated_capture_offset_samples"]==lag
    assert report["pilot_only_estimated_carrier_offset_hz"]==pytest.approx(cfo,abs=.4)
    assert report["heldout_metrics"]["normalized_evm"]<.01
    assert report["heldout_apc_level_diagnostics"]["available"]
    assert report["physical_hardware_iq_evm_verified"] is False


def test_manifest_corruption_or_bad_sampling_rejected(manifest):
    manifest["payload_sha256_le_words"]="bad"
    with pytest.raises(ValueError,match="inconsistency"):
        make_target(manifest)
    manifest=json.loads((ROOT/"docs/k6-fsk-lab-vector.json").read_text())
    with pytest.raises(ValueError,match="exactly divisible"):
        make_target(manifest,sample_rate=44100)


def test_no_transmitter_dependency():
    source=(ROOT/"tools/k6_polar_known_target.py").read_text()
    for forbidden in ("import serial","import pyrtlsdr","tx_on(",
                      "fd_write(","run_module(","flash(","apc_set("):
        assert forbidden not in source
    assert "physical_joint_fsk_apc_demonstrated" in source
