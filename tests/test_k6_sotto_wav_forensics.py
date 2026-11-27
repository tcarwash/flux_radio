"""Offline WAV forensics contract; no radio, recording or network required."""
from pathlib import Path
import numpy as np
from scipy.io import wavfile
import pytest
from tools.k6_sotto_wav_forensics import analyze, compare, peak_components


def write_wav(path,frequency=500,amplitude=.2,duration=2.5):
    fs=8000
    n=int(fs*duration)
    x=(32767*amplitude*np.sin(2*np.pi*frequency*np.arange(n)/fs)).astype('<i2')
    wavfile.write(path,fs,x)


def test_sine_frequency_and_level(tmp_path):
    p=tmp_path/"sine.wav"
    write_wav(p,frequency=482,amplitude=.25)
    r=analyze(p,expected_hz=500)
    assert r["rate_hz"]==8000
    assert r["samples"]==20000
    assert abs(r["dominant_hz"]-482)<1
    assert .96<r["dominant_to_nominal_ratio"]<.97
    assert -19<r["rms_dbfs"]<-10
    assert r["clipped_samples"]==0
    assert "/" not in r["file_basename"]


def test_harmonic_spectrum_does_not_claim_peak_equals_fundamental():
    fs=8000
    t=np.arange(fs*3)/fs
    x=.13*np.sin(2*np.pi*5*191.2*t)+.08*np.sin(2*np.pi*6*191.2*t)
    r=peak_components(x,fs)
    assert abs(r["dominant_hz"]-956)<3
    assert any(abs(v["hz"]-1147.2)<3 for v in r["spectral_peaks"])


def test_off_baseline_is_only_statistical(tmp_path):
    for n,f in [("250hz_at_.1vpp.wav",250),("500hz_at_.1vpp.wav",500),
                ("1khz_at_.1vpp.wav",1000),("2k_at_.1vpp.wav",2000)]:
        write_wav(tmp_path/n,f,.2)
    write_wav(tmp_path/"gen_off.wav",800,.02)
    result=compare(tmp_path)
    assert result["schema"]==1
    assert len(result["rows"])==5
    assert result["source_wav_bytes_included"] is False
    assert "not proven ADC" in result["statistical_warning"]
    assert all(r["noise_subtracted_snr_db"]>10 for r in result["rows"][:4])
    assert "noise_subtracted_snr_db" not in result["rows"][4]


def test_short_wav_is_rejected(tmp_path):
    wavfile.write(tmp_path/"short.wav",8000,np.zeros(160,dtype=np.int16))
    with pytest.raises(ValueError):
        analyze(tmp_path/"short.wav")
