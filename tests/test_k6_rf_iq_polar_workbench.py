"""Offline generic RF I/Q -> phase/frequency and envelope controls."""
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_rf_iq_polar_workbench import (
    analyze,complex_to_polar,complex_fit,reconstruct,reference
)


def test_constant_envelope_chirp_feasible_as_ideal_fm():
    x=reference("chirp",fs=48000,duration_s=.5)
    report,arrays=analyze(x,48000,freq_limit_hz=1200)
    assert report["constant_envelope_target"]
    assert report["max_required_abs_frequency_hz"]<1000
    assert report["results"]["fm_only"]["normalized_evm"]<1e-8
    assert report["results"]["fm_only"]["clipped_frequency_samples"]==0
    assert np.allclose(arrays["mode_fm_only"],x)


def test_fm_only_cannot_reproduce_amplitude_modulation():
    x=reference("am",fs=48000,duration_s=.5)
    report,signals=analyze(x,48000)
    assert not report["constant_envelope_target"]
    assert report["results"]["fm_only"]["normalized_evm"]>.3
    assert report["results"]["ideal_continuous_envelope"]["normalized_evm"]<1e-8
    assert report["results"]["four_level_envelope"]["normalized_evm"]>.02


def test_qam_has_amplitude_demand_and_or_phase_rate_limit():
    x=reference("qam16",fs=48000,duration_s=.5)
    report,arrays=analyze(x,48000,freq_limit_hz=1200)
    assert not report["constant_envelope_target"]
    assert report["target_envelope_peak_to_rms"]>1.1
    assert report["results"]["fm_only"]["normalized_evm"]>.3
    assert report["results"]["ideal_continuous_envelope"]["normalized_evm"]>=0


def test_tight_frequency_limit_clips_chirp():
    x=reference("chirp",fs=48000,duration_s=.5)
    tight,_=analyze(x,48000,freq_limit_hz=80)
    loose,_=analyze(x,48000,freq_limit_hz=1200)
    assert tight["results"]["fm_only"]["clipped_frequency_fraction"]>.6
    assert tight["results"]["fm_only"]["normalized_evm"]>.3
    assert loose["results"]["fm_only"]["normalized_evm"]<1e-8


def test_carrier_and_polar_roundtrip():
    x=reference("cw",fs=48000,duration_s=.12)
    p=complex_to_polar(x,48000)
    assert np.allclose(p["frequency_hz"],0)
    z=reconstruct(p,freq_limit_hz=1200,envelope_mode="none")["iq"]
    assert np.allclose(z,x)


def test_zeros_use_interpolated_phase_without_nan():
    x=reference("chirp",fs=48000,duration_s=.12)
    x[100:140]=0
    p=complex_to_polar(x,48000)
    assert np.isfinite(p["frequency_hz"]).all()
    assert np.isfinite(p["phase"]).all()
    assert np.all(p["envelope"][100:140]==0)


def test_source_vectors_deterministic():
    for mode in ("cw","chirp","fsk","am","qpsk","qam16"):
        a=reference(mode,fs=48000,duration_s=.12,seed=17)
        b=reference(mode,fs=48000,duration_s=.12,seed=17)
        assert np.array_equal(a,b)
        assert a.dtype.kind=="c" and len(a)==5760


@pytest.mark.parametrize("sample",[
    np.zeros(100),np.array([np.inf+0j]*100),
    np.ones(2,dtype=np.complex128),np.ones((10,10),dtype=complex)
])
def test_reject_bad_iq(sample):
    with pytest.raises(ValueError):
        complex_to_polar(sample,48000)


def test_invalid_controls():
    p=complex_to_polar(reference("chirp"),48000)
    for args in (dict(freq_limit_hz=-1),dict(freq_limit_hz=100),
                 dict(freq_limit_hz=24001),
                 dict(freq_limit_hz=1200,envelope_mode="fake"),
                 dict(freq_limit_hz=1200,envelope_levels=1)):
        # The limit of 100 Hz itself is valid, not all candidates invalid.
        if args==dict(freq_limit_hz=100):
            continue
        with pytest.raises(ValueError):
            reconstruct(p,**args)


def test_complex_fit_global_gain_only():
    a=reference("chirp",fs=48000,duration_s=.12)
    fit=complex_fit(a,a*np.exp(1j*.8)*3)
    assert fit["normalized_evm"]<1e-7
    assert fit["coherent_correlation"]==pytest.approx(1)


def test_cli_outputs_generic_target_no_hardware(tmp_path):
    for mode in ("chirp","am"):
        prefix=tmp_path/mode
        command=[sys.executable,str(ROOT/"tools/k6_rf_iq_polar_workbench.py"),
                 "--mode",mode,"--out-prefix",str(prefix)]
        first=subprocess.run(command,capture_output=True,text=True,timeout=30)
        assert first.returncode==0,first.stderr
        report=json.loads(prefix.with_suffix(".json").read_text())
        assert report["source_mode"]==mode
        assert report["results"]["fm_only"]["physical_rf_validated"] is False
        assert prefix.with_suffix(".npz").exists()
        second=subprocess.run(command,capture_output=True,text=True,timeout=30)
        assert second.returncode!=0
        assert "refusing to overwrite" in second.stderr


def test_source_is_offline():
    s=(ROOT/"tools/k6_rf_iq_polar_workbench.py").read_text()
    for token in ("import serial","import SoapySDR","from k6_live",
                  "tx_on(","fd_write(","mutate_library(","run_module(",
                  "subprocess.run("):
        assert token not in s


def test_measured_apc_feasibility_distinguishes_slow_narrow_and_fast_am():
    fs=48000
    t=np.arange(fs,dtype=float)/fs
    small=(.9+.1*np.sin(2*np.pi*13*t))*np.exp(2j*np.pi*120*t)
    r,_=analyze(small,fs)
    apc=r['fd6818b_measured_apc_feasibility']
    assert apc['status']=='slow_80_to_100_percent_apc_candidate_only'
    assert apc['samples_below_stable_amplitude_floor']==0
    assert apc['envelope_ac_spectral_energy_above_50hz_fraction']<.01
    assert not apc['physical_target_waveform_transmission_verified']
    high=(.9+.1*np.sin(2*np.pi*100*t))*np.exp(2j*np.pi*120*t)
    out,_=analyze(high,fs)
    fast=out['fd6818b_measured_apc_feasibility']
    assert fast['status']=='rate_or_duration_unvalidated'
    assert fast['envelope_ac_spectral_energy_above_90hz_fraction']>.8
    wide=reference('am',fs=fs,duration_s=.5)
    old,_=analyze(wide,fs)
    limited=old['fd6818b_measured_apc_feasibility']
    assert limited['status']=='outside_measured_amplitude_range'
    assert limited['fraction_below_stable_amplitude_floor']>.1
    assert limited['ideal_80percent_floor_clamped_amplitude_only_evm_lower_bound']>.01


def test_measured_apc_constant_envelope_requires_no_actuator():
    x=reference('cw',fs=48000,duration_s=.25)
    r,_=analyze(x,48000)
    f=r['fd6818b_measured_apc_feasibility']
    assert f['status']=='constant_envelope_no_apc_needed'
    assert f['envelope_ac_spectral_energy_above_50hz_fraction']==0
    assert f['samples_below_stable_amplitude_floor']==0
