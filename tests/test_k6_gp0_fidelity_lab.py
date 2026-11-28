"""Offline GP0 raw-group fidelity what-ifs; never access radio or bench."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pytest
from scipy.io import wavfile

from tools.k6_gp0_fidelity_lab import (
    candidate_source, late_box_source, compile_lab, fit_tone, load_groups,
    FIR_CANDIDATES, TRIM_PCM,
)
from tools.k6_gp0_noise_filter_model import SOURCE, build_runner, run
from tools.k6_sotto_fidelity_previews import (
    FS, VARIANTS, load_speech, variants, save, normalize_for_audition
)
from tools.k6_sotto_blind_review import build as blind_build


def test_cannot_generate_an_unbounded_or_ambiguous_filter():
    base=build_runner(SOURCE.read_text())
    for invalid in [(), (1,)*12, (0,0,0), (-1,2,1),(100,)]:
        with pytest.raises(ValueError):
            candidate_source(base,kernel=invalid)
    with pytest.raises(ValueError):
        candidate_source(base)
    with pytest.raises(ValueError):
        candidate_source(base,kernel=(1,1),iir=(1,1))
    with pytest.raises(ValueError):
        candidate_source(base,iir=(4,1))
    with pytest.raises(ValueError):
        late_box_source(base,11)
    with pytest.raises(ValueError):
        candidate_source("unrelated source",kernel=(1,2,1))


def test_actual_rust_candidates_deterministic_and_bounded(tmp_path):
    progs=compile_lab(tmp_path)
    assert {'legacy','aa3','fir11','fir7_box','fir7_box_late',
            'fir5_box','fir5_box_late','iir2_half'} <= set(progs)
    patterns=[
        np.zeros(6000,dtype=np.uint8),
        np.full(6000,64,dtype=np.uint8),
        np.full(6000,32,dtype=np.uint8),
        np.random.default_rng(626).integers(0,65,6000,dtype=np.uint8),
    ]
    for counts in patterns:
        original=run(progs['legacy'],counts)
        assert len(original)==(len(counts)*7500)//87260
        for name in progs:
            a=run(progs[name],counts)
            b=run(progs[name],counts)
            np.testing.assert_array_equal(a,b)
            assert a.dtype==np.int16 and len(a)==len(original)
            assert np.min(a)>=-32768 and np.max(a)<=32767  # signed16 saturated limits
        if np.all(counts==32):
            assert not np.any(run(progs['fir7_box_late'],counts)[45:])


def test_running_sum_and_direct_seventap_agree_within_quantization(tmp_path):
    progs=compile_lab(tmp_path)
    rng=np.random.default_rng(123)
    x=np.clip(np.rint(32+3*np.sin(2*np.pi*np.arange(20000)/93.3)
                       +rng.normal(size=20000)*1.3),0,64).astype(np.uint8)
    early=run(progs['fir7_box'],x).astype(np.int32)
    late=run(progs['fir7_box_late'],x).astype(np.int32)
    assert len(early)==len(late)
    # Q7 per-group vs late one-division-per-PCM are not bit-identical.
    assert np.max(np.abs(early-late))<=16
    assert np.std(early-late)<8


def test_strong_sine_can_be_fitted_but_too_short_rejected():
    fs=8019.39
    t=np.arange(400)/fs
    sig=.3*np.sin(2*np.pi*1000*t)+np.random.default_rng(13).normal(size=len(t))*.01
    r=fit_tone(sig,fs,1000)
    assert abs(r['fitted_hz']-1000)<5
    assert .29<r['tone_peak_fs']<.31
    with pytest.raises(ValueError):
        fit_tone(np.zeros(TRIM_PCM+90),fs,1000)


def test_partial_capture_rejects_truncated_frames_and_complete_requires_hash(tmp_path):
    (tmp_path/'off-accepted66.bin').write_bytes(b'\0'*65)
    with pytest.raises(ValueError,match="length"):
        load_groups(tmp_path,'off',True)
    raw=bytes([32])*4096
    (tmp_path/'tone.bin').write_bytes(raw)
    (tmp_path/'tone.json').write_text(json.dumps({
        'crc_accepted':86,'complete_popcounts':True,'raw_popcount_sha256':'0'*64,
        'capture_duration_ticks_100us':439}))
    with pytest.raises(ValueError,match="digest"):
        load_groups(tmp_path,'tone',False)
    manifest=json.loads((tmp_path/'tone.json').read_text())
    manifest['raw_popcount_sha256']=hashlib.sha256(raw).hexdigest()
    (tmp_path/'tone.json').write_text(json.dumps(manifest))
    data,meta=load_groups(tmp_path,'tone',False)
    assert len(data)==4096 and meta['complete'] and meta['crc_verified_packets']==86
    with pytest.raises(ValueError,match="invalid popcount"):
        (tmp_path/'tone.bin').write_bytes(bytes([255])*4096)
        manifest['raw_popcount_sha256']=hashlib.sha256(bytes([255])*4096).hexdigest()
        (tmp_path/'tone.json').write_text(json.dumps(manifest))
        load_groups(tmp_path,'tone',False)


def test_audio_variants_preserve_duration_level_and_refuse_overwrite(tmp_path):
    sample=FS*2
    t=np.arange(sample)/FS
    wav=(.13*np.sin(2*np.pi*240*t)+.04*np.sin(2*np.pi*2750*t)
          +np.random.default_rng(18).normal(scale=.02,size=sample))
    inp=tmp_path/'fixture.wav'
    wavfile.write(inp,FS,np.rint(np.clip(wav,-1,.999)*32767).astype(np.int16))
    out=tmp_path/'previews';out.mkdir()
    manifest=save(inp,out)
    assert set(manifest['variants'])==set(VARIANTS)
    for name,item in manifest['variants'].items():
        rate,y=wavfile.read(out/(name+'.wav'))
        assert rate==FS and y.dtype==np.int16 and len(y)==sample
        assert item['clipped_samples']==0
        # Output gain is deliberately capped at 1.8x and never hard-clips.
        assert 0.65*manifest['original_metrics']['rms'] < item['rms'] <= manifest['original_metrics']['rms']*1.001
    with pytest.raises(FileExistsError):
        save(inp,out)
    with pytest.raises((ValueError,FileNotFoundError)):
        load_speech(tmp_path/'not-here.wav')  # any invalid/nonexistent source


def test_blind_trials_are_one_to_one_and_safely_reproducible(tmp_path):
    import json
    src=tmp_path/'previews';src.mkdir()
    t=np.arange(FS*3)/FS
    x=np.rint((.1*np.sin(2*np.pi*400*t))*32767).astype(np.int16)
    wavfile.write(tmp_path/'source.wav',FS,x)
    save(tmp_path/'source.wav',src)
    a=tmp_path/'a';a.mkdir();b=tmp_path/'b';b.mkdir()
    blind_build(src,a,start_s=.25,seconds=1,seed=123)
    blind_build(src,b,start_s=.25,seconds=1,seed=123)
    key=json.loads((a/'answer_key.json').read_text())
    assert set(key['mapping'])==set('ABCDE')
    assert set(key['mapping'].values())==set(VARIANTS)
    for letter in 'ABCDE':
        fa=(a/(letter+'.wav')).read_bytes()
        fb=(b/(letter+'.wav')).read_bytes()
        assert fa==fb
        rate,y=wavfile.read(a/(letter+'.wav'))
        assert rate==FS and len(y)==FS
    with pytest.raises(FileExistsError):
        blind_build(src,a,start_s=.25,seconds=1,seed=123)
    with pytest.raises(ValueError):
        blind_build(src,b,start_s=2.5,seconds=1,seed=234)
