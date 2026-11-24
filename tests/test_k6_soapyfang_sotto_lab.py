"""SoapyFang voice-lab UI controls a real ARM modem over offline virtual IQ."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import io
import wave

import pytest

from sim.k6ide import create_app
from sim.k6ide_sotto import SottoVoiceLab


ROOT=Path(__file__).resolve().parents[1]


def app_for(lab):
    # Flask endpoints and HTML do not require firmware power-up to perform
    # this *separate* ARM module + RFBackend bench qualification.
    mock=SimpleNamespace(state=lambda: {"running":False})
    return create_app(mock,sotto_lab=lab).test_client()


def test_disabled_lab_cannot_key_anything():
    client=app_for(None)
    assert client.get('/api/sotto/state').json['enabled'] is False
    assert client.post('/api/sotto/run',json={}).status_code==404
    assert client.get('/api/sotto/audio.wav').status_code==404
    assert b'id="sotto-lab"' not in client.get('/').data


def test_validation_and_no_stale_wav():
    lab=SottoVoiceLab(ROOT)
    client=app_for(lab)
    assert b'id="sotto-lab"' in client.get('/').data
    assert client.get('/api/sotto/audio.wav').status_code==409
    for body in [
        {"fixture":"not-sotto"}, {"cn_db":float("inf")},
        {"adc_cycles":9999}, {"spi_us":-1},
        {"prefix_every":7}, {"prefix_every":2.3}, {"offset_hz":9000},
        {"rf_source":"soapy:driver=hackrf"}, {"fixture":[]},
    ]:
        res=client.post('/api/sotto/run',json=body)
        assert res.status_code==400,body
        assert res.json['ok'] is False
    assert not lab.state()['audio_available']
    assert lab._lock.acquire(False)
    try:
        assert client.post('/api/sotto/run',json={}).status_code==400
    finally:
        lab._lock.release()


def test_real_arm_virtual_rf_speaker_and_overload():
    lab=SottoVoiceLab(ROOT)
    client=app_for(lab)
    result=client.post('/api/sotto/run',json={
        "fixture":"mixed","cn_db":25,"offset_hz":0,
        "prefix_every":2,"adc_cycles":4800,"spi_us":10,
    })
    assert result.status_code==200,result.json
    assert result.json['ok']
    r=result.json['report']
    assert r['half_duplex'] and r['passed']
    assert r['codec_exact'] and r['wire_decoder_agrees']
    assert r['session_complete'] and r['tx_idle'] and r['radio_returned_to_rx']
    assert r['codec_frames']==30 and r['rf_packets_received']==6
    assert r['samples_in']==r['samples_captured']==r['audio_samples']==9600
    assert r['speaker_blocks_20ms']==60
    assert r['module_bytes']+24 <= 10240
    assert r['keyed_spi_reads']>0 and r['spi_read_bytes']>r['keyed_spi_read_bytes']
    assert 'SoapyFang RFBackend/VirtualAir IQ' in r['radio_path']
    out=client.get('/api/sotto/audio.wav')
    assert out.status_code==200 and out.mimetype=='audio/wav'
    with wave.open(io.BytesIO(out.data),'rb') as w:
        assert (w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes())==(8000,1,2,9600)
        assert any(w.readframes(9600))
    assert lab.state()['audio_available']

    # Stress failure must clear the previous valid audio and return RX safely.
    fail=client.post('/api/sotto/run',json={
        "fixture":"mixed","adc_cycles":5745,"spi_us":34,
    })
    assert fail.status_code==422,fail.json
    f=fail.json['report']
    assert not f['passed'] and f['pcm_loss']
    assert f['tx_idle'] and f['radio_returned_to_rx']
    assert not lab.state()['audio_available']
    assert client.get('/api/sotto/audio.wav').status_code==409
