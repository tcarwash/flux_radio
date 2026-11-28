"""Regression tests for RX-only, fixed-gain FM source-vs-RF qualification."""
from pathlib import Path
import numpy as np
import pytest
from tools.k6_fm_source_qualify import (
    OUT_FS, fm_discriminator, analyze_audio, load_iq, compare_stages, analyze_iq
)


def fm_iq(*,fs=128000,seconds=3.,if_hz=23500,dev_peak_hz=0,noise=.0004,seed=8,carrier=True):
    n=round(fs*seconds)
    t=np.arange(n)/fs
    rng=np.random.default_rng(seed)
    d=dev_peak_hz*np.sin(2*np.pi*1000*t)
    phase=2*np.pi*(if_hz*t+np.cumsum(d)/fs)
    src=.35*np.exp(1j*phase) if carrier else np.zeros(n)
    return (src+noise*(rng.normal(size=n)+1j*rng.normal(size=n))).astype(np.complex64)


def test_strong_fm_carrier_tone_and_quieting():
    fs=128000;offset=23500.
    iq_tone=fm_iq(fs=fs,if_hz=offset,dev_peak_hz=2000)
    iq_silence=fm_iq(fs=fs,if_hz=offset,dev_peak_hz=0)
    iq_off=fm_iq(fs=fs,if_hz=offset,carrier=False)
    tone,pt=fm_discriminator(iq_tone,fs,carrier_if_hz=offset)
    silent,ps=fm_discriminator(iq_silence,fs,carrier_if_hz=offset)
    no,po=fm_discriminator(iq_off,fs,carrier_if_hz=offset)
    a=analyze_audio(tone);b=analyze_audio(silent);c=analyze_audio(no)
    assert abs(a['measured_tone_hz']-1000)<1
    assert 1700<a['tone_peak_deviation_hz']<2100
    assert a['tone_to_residual_db']>22
    assert c['in_band_rms_deviation_hz']>b['in_band_rms_deviation_hz']*10
    comparison=compare_stages({**po,**c},{**ps,**b},{**pt,**a})
    assert comparison['carrier_over_no_carrier_rf_power_db']>20
    assert comparison['strong_carrier_quieting_rms_db']>20
    assert comparison['mic_tone_demod_tone_to_residual_db']>22


def test_extra_mic_source_noise_lowers_snr():
    fs=128000
    # Analog microphone source noise is *frequency modulation* before RF.
    n=3*fs;t=np.arange(n)/fs
    rng=np.random.default_rng(74)
    from scipy.signal import resample_poly
    source=np.sin(2*np.pi*1000*t)*2000+resample_poly(rng.normal(scale=250,size=3*8000),16,1)
    z=np.exp(2j*np.pi*(23500*t+np.cumsum(source)/fs)).astype(np.complex64)
    out,_=fm_discriminator(z,fs,carrier_if_hz=23500)
    metrics=analyze_audio(out)
    assert 15<metrics['tone_to_residual_db']<30
    assert abs(metrics['measured_tone_hz']-1000)<3


def test_receive_capture_parser_and_validity(tmp_path):
    z=fm_iq()
    f=tmp_path/'data.c64';z.tofile(f)
    data=load_iq(f,128000,start_s=.5,length_s=2)
    assert data.shape[0]==256000
    _,m=fm_discriminator(data,128000,carrier_if_hz=23500)
    assert m['input_iq_sample_rate']==128000
    full=analyze_iq(f,128000,23500,.5,2,1000)
    assert 'tone_residual_rms_deviation_hz' in full
    assert full['source_iq_bytes']==len(z)*8
    with pytest.raises(ValueError,match='beyond'):
        load_iq(f,128000,start_s=2,length_s=2)
    with pytest.raises(ValueError):
        fm_discriminator(z,128000,carrier_if_hz=100000)
    with pytest.raises(ValueError):
        analyze_audio(np.zeros(80))
    with pytest.raises(ValueError):
        analyze_audio(np.zeros(8000),tone_hz=8000)
    with pytest.raises(ValueError):
        load_iq(f,48000)
    bad=tmp_path/'bad.c64';bad.write_bytes(b'abc')
    with pytest.raises(ValueError):
        load_iq(bad,128000)
