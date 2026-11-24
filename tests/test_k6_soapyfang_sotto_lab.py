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
        {"rx_threshold":0}, {"rx_threshold":8},
        {"rx_poll_us":100}, {"rx_poll_us":30000}, {"rx_poll_us":500.1},
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
    assert r['rx_fifo_status']['frames']==6
    assert r['rx_fifo_status']['words']==6*33
    assert r['rx_fifo_status']['overruns']==r['rx_fifo_status']['underflows']==0
    assert r['rx_threshold']==4 and r['rx_poll_us']==1000
    assert 0 < r['stream_rx_peak_blocks'] <= r['stream_rx_capacity_blocks'] == 16
    assert r['keyed_spi_reads']>0 and r['spi_read_bytes']>r['keyed_spi_read_bytes']
    assert 'SoapyFang RFBackend/VirtualAir IQ' in r['radio_path']
    out=client.get('/api/sotto/audio.wav')
    assert out.status_code==200 and out.mimetype=='audio/wav'
    with wave.open(io.BytesIO(out.data),'rb') as w:
        assert (w.getframerate(),w.getnchannels(),w.getsampwidth(),w.getnframes())==(8000,1,2,9600)
        assert any(w.readframes(9600))
    assert lab.state()['audio_available']
    assert not r['compiled_artifacts_reused']
    assert lab.state()['cached_fixtures']==['mixed']
    # Vary only RF parameters: no expensive ARM/native reference rebuild.
    second=client.post('/api/sotto/run',json={
        "fixture":"mixed","cn_db":25,"offset_hz":0,
        "prefix_every":0,"adc_cycles":4800,"spi_us":10,
    })
    assert second.status_code==200,second.json
    again=second.json['report']
    assert again['compiled_artifacts_reused']
    assert again['compile_setup_seconds']<1.0
    assert again['codec_frames']==30 and again['rf_packets_received']==6
    assert again['audio_samples']==9600 and again['tx_idle']
    assert second.json['report']['source_revision']==r['source_revision']

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

def test_content_hash_invalidates_build_cache_without_git_changes(monkeypatch):
    lab=SottoVoiceLab(ROOT)
    original=lab._fingerprint()
    read_bytes=Path.read_bytes

    def changed_bytes(p):
        data=read_bytes(p)
        return data+b"\n/* dry-run source mutation */" if p.name=="sotto_live_tx.c" else data

    with monkeypatch.context() as patch:
        patch.setattr(Path,"read_bytes",changed_bytes)
        changed=lab._fingerprint()
    assert original!=changed
    assert lab._fingerprint()==original
    lab.close()
    with pytest.raises(RuntimeError,match="closed"):
        lab.run()


def test_rx_fifo_threshold_polling_can_miss_final_session_without_overrun():
    # An intentionally slow 5-ms service loop is adequate for threshold=4
    # in this finite virtual session but misses a packet with threshold=6,
    # despite neither case reporting a raw FIFO overflow.
    lab=SottoVoiceLab(ROOT)
    try:
        ok=lab.run(fixture="mixed",rx_threshold=4,rx_poll_us=5000)
        assert ok["passed"] and ok["rf_packets_received"]==6
        assert ok["rx_fifo_status"]["overruns"]==0
        fail=lab.run(fixture="mixed",rx_threshold=6,rx_poll_us=5000)
        assert not fail["passed"] and fail["rf_packets_received"]==5
        assert fail["rx_fifo_status"]["overruns"]==0
        assert fail["compiled_artifacts_reused"]
        assert fail["tx_idle"] and fail["radio_returned_to_rx"]
        assert not lab.state()["audio_available"]
    finally:
        lab.close()
