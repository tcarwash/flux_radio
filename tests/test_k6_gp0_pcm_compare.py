"""Pre-vocoder WAV OFF/ON analysis with exact physical-clock direction.

All WAV and metadata are synthetic. Test never opens radio, USB SDR or
connected user files. A 500Hz nominal generator shifted to 482Hz by a faster
PCM producer must be corrected *up* using the measured physical cadence.
"""
from pathlib import Path
from scipy.io import wavfile
import hashlib,json
import numpy as np
import pytest
from tools.k6_gp0_pcm_compare import compare,spectral_peak,read_capture,FS,N


def sample_file(d:Path,stem:str,x,token,ticks):
    w=d/(stem+".wav")
    j=d/(stem+".json")
    pcm=np.rint(np.asarray(x)*32767).clip(-32768,32767).astype("<i2")
    assert len(pcm)==1600
    wavfile.write(w,8000,pcm)
    j.write_text(json.dumps(dict(sample_rate_hz=8000,sample_count=1600,
        capture_duration_ticks_100us=ticks,
        duration_if_labeled_8k_ms=200.0,
        token=token,pcm_sha256=hashlib.sha256(pcm.tobytes()).hexdigest())))
    return w,j


def test_nominal_generator_faster_actual_pcm_clock_causes_lower_wav_pitch(tmp_path):
    rng=np.random.default_rng(7)
    off=rng.normal(0,.008,N)
    # A 500Hz *real* signal sampled at about 8300 actual samples/s will
    # play back at 482Hz when WAV is mislabeled at 8k.
    actual_pcm_hz=8300.
    modeled_playback_hz=500*8000/actual_pcm_hz
    t=np.arange(N)/FS
    on=.13*np.sin(2*np.pi*modeled_playback_hz*t)+rng.normal(0,.008,N)
    ow,oj=sample_file(tmp_path,"off",off,1234,2000)
    tw,tj=sample_file(tmp_path,"tone",on,5678,round(1600*10000/actual_pcm_hz))
    r=compare(ow,oj,tw,tj,500)
    assert r["schema"]==1 and r["offline_pre_vocoder"]
    assert r["off_capture_samples_per_wall_second"]==8000
    assert 8290<r["on_capture_samples_per_wall_second"]<8310
    assert abs(r["tone"]["decoded_precodec_peak_hz"]-modeled_playback_hz)<1
    assert abs(r["input_frequency_inferred_from_timing_hz"]-500)<1.5
    assert r["tone_to_off_noise_rms_db"]>15
    assert r["generator_off"]["near_fullscale_samples"]==0
    assert r["generator_on"]["near_fullscale_samples"]==0


def test_refuse_tampered_capture_metadata_and_same_session(tmp_path):
    x=np.zeros(N)
    ow,oj=sample_file(tmp_path,"off",x,7,2000)
    tw,tj=sample_file(tmp_path,"tone",x,7,2000)
    with pytest.raises(ValueError,match="distinct"):
        compare(ow,oj,tw,tj,500)
    metadata=json.loads(tj.read_text())
    metadata["token"]=8
    metadata["pcm_sha256"]="0"*64
    tj.write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match="hash"):
        compare(ow,oj,tw,tj,500)
    metadata["pcm_sha256"]=hashlib.sha256(np.zeros(N,dtype="<i2").tobytes()).hexdigest()
    metadata["capture_duration_ticks_100us"]=0
    tj.write_text(json.dumps(metadata))
    with pytest.raises(ValueError,match="Nonphysical"):
        read_capture(tw,tj)


def test_refuse_invalid_nominal_tone_and_short_wav(tmp_path):
    ow,oj=sample_file(tmp_path,"off",np.zeros(N),1,2000)
    tw,tj=sample_file(tmp_path,"tone",np.zeros(N),2,2000)
    for freq in (-1,100,4000,float("nan")):
        with pytest.raises(ValueError):
            compare(ow,oj,tw,tj,freq)
    wavfile.write(tw,FS,np.zeros(1200,dtype=np.int16))
    with pytest.raises(ValueError,match="exactly 1600"):
        read_capture(tw,tj)


def test_spectral_peak_bounds():
    t=np.arange(N)/FS
    row=spectral_peak(.1*np.sin(2*np.pi*1000*t),target_hz=1000)
    assert abs(row["decoded_precodec_peak_hz"]-1000)<.2
    assert abs(row["tone_amplitude_peak_ratio"]-.1)<.001
    for freq in (0,4000):
        with pytest.raises(ValueError):
            spectral_peak(np.zeros(N),target_hz=freq)
