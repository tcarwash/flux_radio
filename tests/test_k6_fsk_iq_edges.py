"""Host-only tests for RF IQ archive analyzer; no device/radio access."""
from pathlib import Path
import json
import subprocess
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from k6_fsk_iq_edges import analyze_iq, error_windows, read_iq


def synthetic_fsk(*, baud=4800, fs=128000, nbits=512,
                  phase_jump_rad=0.0, seed=23, attenuate_start=False):
    """Ideal continuous-phase 2-FSK plus optional deliberate phase resets."""
    rng = np.random.default_rng(seed)
    bits = rng.integers(0, 2, nbits, dtype=np.uint8)
    # Force repeatable transitions/plateaus and both frequency levels.
    bits[:12] = np.array([0]*6 + [1]*6)
    first_center_s = .300 + .5 / baud
    nsamples = int(fs * (.300 + (nbits + 6)/baud))
    t = np.arange(nsamples)/fs
    index = np.floor((t-.300)*baud).astype(int)
    valid = (index >= 0) & (index < nbits)
    sample_bits = bits[np.clip(index, 0, nbits-1)]
    freq = np.where(valid, np.where(sample_bits > 0, 5000., -5000.), 0.0)
    phase = np.cumsum(freq) * 2 * np.pi/fs
    if phase_jump_rad:
        change = np.zeros(nsamples)
        transition = np.where((index[1:] != index[:-1]) & valid[1:] &
                              (index[1:] > 0) & (sample_bits[1:] != sample_bits[:-1]))[0]+1
        change[transition] = phase_jump_rad
        phase += np.cumsum(change)
    amplitude = np.where(valid, 10.0, 0.0)
    if attenuate_start:
        amplitude = np.where(valid & (index < 100), 0.05, amplitude)
    noise = .15*(rng.normal(size=nsamples) + 1j*rng.normal(size=nsamples))
    iq = amplitude * np.exp(1j*phase) + noise
    return iq, bits, first_center_s


def test_ideal_fsk_stable_tail_and_known_phase():
    iq, bits, center = synthetic_fsk()
    info = analyze_iq(iq, bits, baud=4800, first_center_s=center)
    assert info["fsk_mark_separation_hz"] == pytest.approx(10000, rel=.05)
    assert info["simple_discriminator_settled_errors"] == 0
    assert info["simple_discriminator_error_windows"]["after_168"] == 0
    assert info["phase_bridge_diagnostic"]["proves_phase_continuity"] is False
    assert info["phase_bridge_diagnostic"]["isolated_transition_count"] > 2
    assert info["phase_bridge_diagnostic"]["median_abs_transition_bridge_rad"] < .7


def test_deliberate_phase_reset_is_detectable_as_bridge_residual():
    iq, bits, center = synthetic_fsk(phase_jump_rad=np.pi/2)
    info = analyze_iq(iq, bits, baud=4800, first_center_s=center)
    assert info["phase_bridge_diagnostic"]["median_abs_transition_bridge_rad"] > .8
    assert info["phase_bridge_diagnostic"]["proves_phase_continuity"] is False


def test_faint_startup_does_not_count_as_a_settled_failure():
    iq, bits, center = synthetic_fsk(attenuate_start=True)
    info = analyze_iq(iq, bits, baud=4800, first_center_s=center)
    assert info["median_startup_to_noise_amplitude"] < info["median_settled_to_noise_amplitude"]
    assert info["simple_discriminator_error_windows"]["first_168"] > 0
    assert info["simple_discriminator_error_windows"]["after_168"] == 0


def test_rejects_invalid_iq_and_timing():
    iq, bits, center = synthetic_fsk()
    with pytest.raises(ValueError, match="invalid"):
        analyze_iq(iq, bits, baud=50000, first_center_s=center)
    with pytest.raises(ValueError, match="beyond"):
        analyze_iq(iq, bits, baud=4800, first_center_s=1.0)
    with pytest.raises(ValueError, match="invalid"):
        analyze_iq(iq, bits.astype(float)*3, baud=4800, first_center_s=center)


def test_error_partition_has_correct_cumulative_windows():
    err = np.zeros(512, dtype=bool)
    err[[0, 3, 170]] = True
    assert error_windows(err) == {
        "all":3, "first_32":2, "first_64":2, "first_168":2,
        "after_168":1, "last_error_bit":170,
    }


def test_read_iq_enforces_u8_interleaved_format(tmp_path):
    bad = tmp_path/"bad.npy"
    np.save(bad,np.zeros(500,dtype=np.float32))
    with pytest.raises(ValueError, match="interleaved"):
        read_iq(bad)


def test_cli_rejects_missing_archived_data_without_device_access(tmp_path):
    result=subprocess.run([
        sys.executable, str(ROOT/"tools/k6_fsk_iq_edges.py"),
        "--data-dir",str(tmp_path), "--out",str(tmp_path/"report.json")
    ],capture_output=True,text=True,timeout=15)
    assert result.returncode != 0
    assert not (tmp_path/"report.json").exists()


def test_archived_capture_cli_round_trip_with_pre_rf_filter(tmp_path):
    """End-to-end U8 IQ + metadata replay, entirely synthetic and offline."""
    from k6_rf_matrix_decode import payload
    bits = payload(16)
    baud, fs, carrier = 4800, 1_024_000, 280_375.
    sample_count = int((.3 + (len(bits)+8)/baud)*fs)
    time = np.arange(sample_count)/fs
    idx = np.floor((time-.3)*baud).astype(int)
    valid = (idx>=0)&(idx<len(bits))
    bv = bits[np.clip(idx,0,len(bits)-1)]
    rf_freq = np.where(valid, np.where(bv,5500.,-5500.),0.)
    phase = np.cumsum(rf_freq)*2*np.pi/fs - 2*np.pi*carrier*time
    rng = np.random.default_rng(15)
    waveform = np.where(valid,55.,0.)*np.exp(1j*phase)
    waveform += .8*(rng.normal(size=sample_count)+
                    1j*rng.normal(size=sample_count))
    raw = np.empty(sample_count*2,dtype=np.uint8)
    raw[0::2] = np.clip(np.rint(waveform.real+127.5),0,255).astype(np.uint8)
    raw[1::2] = np.clip(np.rint(waveform.imag+127.5),0,255).astype(np.uint8)
    np.save(tmp_path/"synthetic.npy",raw)
    timing = [{
        "capture":"synthetic","nbytes":16,"baud":baud,"rate":baud,
        "start_s":.3+.5/baud,"correlation":1.0,
        "recovered_hex":np.packbits(bits).tobytes().hex()
    }]
    (tmp_path/"decoded.json").write_text(json.dumps(timing))
    result=subprocess.run([
        sys.executable,str(ROOT/"tools/k6_fsk_iq_edges.py"),
        "--data-dir",str(tmp_path),"--captures","synthetic",
        "--iq-lowpass-hz","8000","--out",str(tmp_path/"report.json"),
    ],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    report=json.loads((tmp_path/"report.json").read_text())
    assert report["radio_access"] is False and report["transmitted"] is False
    row=report["cases"][0]
    assert row["original_known_assisted_error_windows"]["all"]==0
    assert row["iq_lowpass_comparison"]["simple_discriminator_error_windows"]["all"]==0
    assert row["input_sha256"] and len(row["input_sha256"])==64
