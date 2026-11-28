"""Receiver-side listening previews must stay separate from source evidence."""
from pathlib import Path
import numpy as np
import pytest
from scipy.io import wavfile
from tools.k6_sotto_voice_noise_preview import save_previews,spectral_soft,read_8k,prepare,FS


def test_preserves_duration_and_unmodified_original(tmp_path):
    t=np.arange(FS*2)/FS
    rng=np.random.default_rng(31)
    x=np.rint((.22*np.sin(2*np.pi*1000*t)+.045*rng.normal(size=len(t)))*32768).clip(-32768,32767).astype(np.int16)
    src=tmp_path/'original.wav'
    wavfile.write(src,FS,x)
    original=src.read_bytes()
    out=tmp_path/'previews';out.mkdir()
    r=save_previews(src,out)
    assert r['preview_quality_not_proven']
    assert original==src.read_bytes()
    for name in ('bandpass','soft','strong'):
        fs,y=wavfile.read(out/(name+'-preview.wav'))
        assert fs==FS and y.dtype==np.int16 and len(y)==len(x)
        assert r['variants'][name]['clipped_samples']==0
    with pytest.raises(FileExistsError):save_previews(src,out)


def test_preview_gate_rejects_invalid_wav_and_params(tmp_path):
    p=tmp_path/'short.wav';wavfile.write(p,FS,np.zeros(100,dtype=np.int16))
    with pytest.raises(ValueError):read_8k(p)
    with pytest.raises(ValueError):spectral_soft(np.zeros(5000),floor=0)
    with pytest.raises(ValueError):spectral_soft(np.zeros(5000),quantile=.9)


def test_level_matching_preserves_relative_listening_loudness(tmp_path):
    t=np.arange(FS*2)/FS
    rng=np.random.default_rng(41)
    audio=np.rint((.2*np.sin(2*np.pi*800*t)+.03*rng.normal(size=len(t)))*32768).clip(-32768,32767).astype(np.int16)
    src=tmp_path/'speech.wav';wavfile.write(src,FS,audio)
    out=tmp_path/'level';out.mkdir()
    r=save_previews(src,out,level_match_rms=True)
    assert r['level_matched_to_original_rms']
    for name in ('bandpass','soft','strong'):
        assert abs(r['variants'][name]['rms']-r['original']['rms'])<.002
        assert r['variants'][name]['clipped_samples']==0
        assert r['variants'][name]['applied_loudness_gain_db']>=0
