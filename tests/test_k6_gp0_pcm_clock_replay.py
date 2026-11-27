"""Offline only: clock correction, verified partial prefix, no guessed audio."""
import hashlib
import math
import subprocess
import sys
from pathlib import Path
import numpy as np
import pytest
from tools.k6_gp0_pcm_clock_replay import (PcmCapture,collect_packets,clock_correct,
                                            replay,read_verified_wav,source_rate)


def packet(seq,token=100,ticks=1876):
    n=min(24,1600-seq*24)
    return dict(seq=seq,total=67,token=token,fs=8000,samples=1600,
                capture_100us=ticks,payload=np.arange(n,dtype='<i2').tobytes())


def test_reject_partial_unless_explicit_then_reconstruct_only_prefix():
    inputs=[packet(i) for i in range(20)]
    with pytest.raises(ValueError,match='incomplete'):collect_packets(inputs)
    c=collect_packets(inputs,allow_partial=True)
    assert not c.complete and c.accepted_packets==20
    assert len(c.pcm)==480*2
    assert source_rate(c)==16000000/1876
    result,out,filtered=replay(c,expected_tone_hz=500)
    assert result['partial_prefix_only'] and not result['fabricated_missing_samples']
    assert 449<=len(out)<=452 and filtered is None


def test_reject_gaps_mixed_sessions_or_conflicting_duplicates():
    with pytest.raises(ValueError,match='gaps'):collect_packets([packet(0),packet(2)],allow_partial=True)
    with pytest.raises(ValueError,match='mixed'):collect_packets([packet(0),packet(1,token=101)],allow_partial=True)
    q=packet(0);r=packet(0);r['payload']=b'\xff'*len(r['payload'])
    with pytest.raises(ValueError,match='conflicting'):collect_packets([q,r],allow_partial=True)
    with pytest.raises(ValueError,match='incomplete'):collect_packets([None,packet(0)])


def test_complete_full_run_does_not_pad_and_last_fragment_is_short():
    data=[packet(i) for i in range(67)]
    c=collect_packets(data)
    assert c.complete and len(c.pcm)==3200
    assert len(data[-1]['payload'])==32


@pytest.mark.parametrize('tone_hz',[500,1000])
def test_polyphase_corrected_pitch_retains_true_time_and_level(tone_hz):
    ticks=1876
    actual=16000000/ticks
    t=np.arange(1600)/actual
    pcm=np.rint(10000*np.sin(2*np.pi*tone_hz*t)).astype(np.int16)
    corrected=clock_correct(pcm,ticks)
    c=PcmCapture(pcm.tobytes(),ticks,100,True,67,'synthetic test')
    report,out,_=replay(c,expected_tone_hz=tone_hz)
    assert np.array_equal(corrected,out)
    assert len(out)==math.ceil(1600*ticks/2000)
    assert abs(report['corrected']['peak_hz']-tone_hz)<2.5
    assert abs(report['corrected']['tone_amp_peak_ratio']-10000/32768)<.025
    assert report['source_sha256']==hashlib.sha256(pcm.tobytes()).hexdigest()
    assert report['status']=='offline playback only; no firmware/radio change'


def test_parameter_validation():
    with pytest.raises(ValueError):clock_correct(np.arange(100,dtype=np.float32),1876)
    with pytest.raises(ValueError):clock_correct(np.zeros(100,dtype=np.int16),500)
    c=PcmCapture(np.zeros(100,dtype='<i2').tobytes(),1876,1,False,3,'synthetic')
    with pytest.raises(ValueError,match='lowpass'):replay(c,lowpass_hz=400)


def test_direct_command_help_from_unrelated_working_directory(tmp_path):
    script=Path(__file__).resolve().parents[1]/'tools'/'k6_gp0_pcm_clock_replay.py'
    r=subprocess.run([sys.executable,str(script),'--help'],cwd=tmp_path,
                     capture_output=True,text=True,timeout=15)
    assert r.returncode==0,r.stderr
    assert '--allow-partial' in r.stdout and '--lowpass-hz' in r.stdout
