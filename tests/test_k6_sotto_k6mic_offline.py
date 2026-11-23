"""End-to-end offline K6 microphone PCM -> Sotto-1200 -> L64 packet CRC.

Never accesses a K6 radio, serial connection, SDR or shared hardware lock.
The user's successfully recorded microphone clip is optional local evidence;
generic synthetic 8k microphone samples are also used for a reproducible test.
"""
import json
from pathlib import Path
import wave

import numpy as np
import pytest

from modem import pager
from tools.k6_sotto_k6mic_offline import (FS,load_k6_audio,resample_voice,
                                           encode_packets,run)

CODEC_DIR=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed')
ENC=CODEC_DIR/'sotto_enc_fx'
DEC=CODEC_DIR/'sotto_dec_fx'
USER_SAMPLE=Path('/home/tyler/flux-bench/micvoice-20261123/micvoice-speech-band.wav')
USER_META=Path('/home/tyler/flux-bench/micvoice-20261123/micvoice-capture.json')


def _fake_capture(tmp_path, *,rate=7957,samples=7957,clip=0):
    t=np.arange(samples)/rate
    s=np.round(5000*np.sin(2*np.pi*531*t)).astype('<i2')
    wav=tmp_path/'fake-mic.wav'
    with wave.open(str(wav),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(rate)
        w.writeframes(s.tobytes())
    meta=tmp_path/'fake-mic.json'
    meta.write_text(json.dumps({'samples':samples,'measured_sample_rate_hz':float(rate),
                                 'clipping_events':clip}))
    return wav,meta


def test_real_mic_capture_source_header_and_precise_timing():
    if not USER_SAMPLE.exists():pytest.skip('Recorded user mic sample is not on this host')
    x,rate,meta=load_k6_audio(USER_SAMPLE,USER_META)
    assert len(x)==8164 and 7950<rate<7970
    assert meta['clipping_events']==0
    encoded,padding=resample_voice(x,rate)
    assert len(encoded)%320==0 and 0<=padding<320
    assert len(encoded)==8320


def test_sample_rate_metadata_and_clipping_must_match(tmp_path):
    src,meta=_fake_capture(tmp_path)
    x,rate,_=load_k6_audio(src,meta)
    assert len(x)==7957 and rate==7957
    bad=dict(json.loads(meta.read_text()))
    bad['measured_sample_rate_hz']=7800
    meta.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='sample-rate'):
        load_k6_audio(src,meta)
    bad['measured_sample_rate_hz']=7957
    bad['clipping_events']=1
    meta.write_text(json.dumps(bad))
    with pytest.raises(ValueError,match='clipping'):
        load_k6_audio(src,meta)


def test_resample_and_zero_padding_exact_40ms_frames():
    src=np.ones(7960,dtype='<i2')*1024
    y,extra=resample_voice(src,7960.)
    assert y.dtype==np.dtype('<i2')
    assert len(y)==8000 and extra==0
    src=np.ones(1000,dtype='<i2')*1024
    y,extra=resample_voice(src,7957.)
    assert len(y)%320==0 and 0<=extra<320
    assert np.max(np.abs(y.astype(int)))<=32767


@pytest.mark.skipif(not (ENC.exists() and DEC.exists()),reason='host Sotto codec binaries not installed')
def test_synthetic_audio_encodes_then_crc_roundtrips(tmp_path):
    src,meta=_fake_capture(tmp_path)
    x,rate,_=load_k6_audio(src,meta)
    pcm,_=resample_voice(x,rate)
    out=tmp_path/'generated'
    report=encode_packets(pcm,ENC,DEC,out,msg_id=87)
    assert report['packets']==4 and report['encoded_superframes']==25
    assert report['crc_good_packets']==4
    assert report['reassembled_frames']==25
    assert report['decoded_8k_pcm_samples']==len(pcm)
    assert report['actual_radio_tx'] is False
    assert (out/'k6mic-sotto1200.bit').read_bytes()==(out/'k6mic-sotto-rx-reassembled.bit').read_bytes()
    with wave.open(str(out/'k6mic-received-8k.wav')) as wav:
        assert wav.getframerate()==FS and wav.getnframes()==len(pcm)
    profiles=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packet_file=(out/'k6mic-l64-packets.bin').read_bytes()
    assert len(packet_file)==4*64
    parsed=[profiles.unpack(packet_file[64*i:64*(i+1)]) for i in range(4)]
    assert [p['seq'] for p in parsed]==[0,1,2,3]
    bad=bytearray(packet_file[:64]);bad[32]^=0x20
    assert profiles.unpack(bad) is None
    with pytest.raises(FileExistsError):encode_packets(pcm,ENC,DEC,out,msg_id=87)


@pytest.mark.skipif(not (ENC.exists() and DEC.exists() and USER_SAMPLE.exists()),
                    reason='host K6 voice capture or codec missing')
def test_actual_k6_mic_voice_end_to_end(tmp_path):
    r=run(USER_SAMPLE,USER_META,ENC,DEC,tmp_path/'voice',msg_id=93)
    assert r['source_adc_clipping_events']==0
    assert r['encoded_superframes']==26 and r['voice_seconds']==1.04
    assert r['packets']==4 and r['crc_good_packets']==4
    assert r['reassembled_frames']==26
    assert r['decoded_8k_pcm_samples']==26*320
    assert r['synthetic_queue_model']['concealed_frames']==0
    assert (tmp_path/'voice/k6mic-received-8k.wav').is_file()
    assert (tmp_path/'voice/k6mic-sotto1200.bit').read_bytes()==(tmp_path/'voice/k6mic-sotto-rx-reassembled.bit').read_bytes()
