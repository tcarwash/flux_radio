"""Offline actual ARM Sotto -> noisy IQ -> independent decoder -> speaker PCM."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import numpy as np
import pytest

from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_e2e_mock import (
    MockSottoRadio, decode_pcm, native_decoder, write_wav,
)

ROOT=Path(__file__).resolve().parents[1]
TREE=Path(os.environ.get('FLUX_SOTTO_TREE',ROOT))


@pytest.fixture(scope='module')
def built(tmp_path_factory):
    if not (TREE/'c/k6/mod/sotto_live_tx.c').is_file():
        pytest.skip('actual experimental Sotto module not available')
    if not all(shutil.which(x) for x in ('cc','clang','ld.lld','llvm-objcopy','llvm-nm')):
        pytest.skip('native/ARM build tools unavailable')
    work=tmp_path_factory.mktemp('sotto-e2e')
    art=prepare(TREE,work)
    dec=native_decoder(TREE,work/'decoder.so')
    return art,dec


def test_mode_uninstalled_and_installed_guard(built):
    a,dec=built
    r=MockSottoRadio(a,dec)
    assert r.status()==dict(installed=False,mode='FM',ptt=False,running=False,radio_state='FM',playback_ready=False)
    assert r.select()=='FM'
    with pytest.raises(RuntimeError,match='not installed'):
        r.transmit()
    assert not r.ptt and not r.running
    r.install()
    with pytest.raises(RuntimeError,match='already installed'):
        r.install()
    assert r.select()=='SOTTO'
    assert r.status()['mode']=='SOTTO'
    assert r.status()['radio_state']=='RX'
    r.running=True
    with pytest.raises(RuntimeError):
        r.remove()
    with pytest.raises(RuntimeError):
        r.select()
    r.running=False
    r.remove()
    assert r.status()==dict(installed=False,mode='FM',ptt=False,running=False,radio_state='FM',playback_ready=False)


@pytest.mark.parametrize('prefix', [0,2])
def test_actual_voice_over_virtual_rf_and_decoder(built,prefix,tmp_path):
    artifact,decoder=built
    r=MockSottoRadio(artifact,decoder)
    r.install()
    assert r.select()=='SOTTO'
    assert r.transmit(adc_cycles=4800,spi_us=10,channel=Channel(prefix_every=prefix))
    t=r.last
    assert t['accepted'] and t['codec_exact'] and t['wire_decoder_agrees']
    assert t['module_return']==t['codec_frames']==26
    assert t['received']==t['tx']['completed']==6
    assert t['session']['complete'] and t['session']['offset_2']==(3 if prefix else 0)
    assert not t['pcm_lost'] and not t['lease_expired']
    assert r.status()['radio_state']=='RX'
    assert r.status()['playback_ready'] and r.rx_audio.shape==(8320,)
    assert np.max(np.abs(r.rx_audio.astype(np.int32)))>100
    assert not r.ptt and not r.running and not t['tx']['active']
    assert r.radio_state=='RX'
    assert r.rx_audio.dtype.str=='<i2'
    assert np.array_equal(decode_pcm(decoder,t['received_codec_bytes']),r.rx_audio)
    speaker=[]
    while (chunk:=r.speaker_next()) is not None:
        speaker.append(chunk)
    assert len(speaker)==52 and all(len(chunk)==160 for chunk in speaker)
    assert np.array_equal(np.concatenate(speaker),r.rx_audio)
    assert r.speaker_next() is None
    wav=tmp_path/'received.wav'
    write_wav(wav,r.rx_audio)
    with wave.open(str(wav),'rb') as f:
        assert (f.getnchannels(),f.getframerate(),f.getsampwidth(),f.getnframes())==(1,8000,2,8320)
        assert f.readframes(f.getnframes())==r.rx_audio.tobytes()
    # Misstated reference bytes should not be silently decoded as speech.
    with pytest.raises(ValueError):
        decode_pcm(decoder,t['received_codec_bytes'][:-1])
    r.remove()
    assert not r.status()['playback_ready']


def test_forced_model_overflow_exits_idle_no_speech(built):
    art,dec=built
    r=MockSottoRadio(art,dec)
    r.install()
    r.select()
    assert not r.transmit(adc_cycles=5745,spi_us=34)
    assert not r.ptt and not r.running
    assert not r.last['accepted'] and r.last['pcm_lost']
    assert r.last['module_return']<0 and not r.last['tx']['active']
    assert r.rx_audio is None and r.speaker_next() is None
    r.remove()


def test_audio_decoder_rejects_malformed_payload(built):
    _,dec=built
    for data in (b'',b'1',b'1234567',b'1'*10002):
        with pytest.raises(ValueError):
            decode_pcm(dec,data)



def test_voice_runtime_identity_still_not_bound_by_production_vfo():
    # The offline mock *deliberately* bridges this gap; the actual radio
    # VFO must not be represented as install-ready by the bench runtime name.
    from tools.k6_sotto_installed_vfo import is_sotto_installed_module
    assert not is_sotto_installed_module(rc=0, flags=1, name='SOTTO TX')
    assert is_sotto_installed_module(rc=0, flags=1, name='SOTTO TEST')


def test_half_duplex_preempts_rx_and_restores_listen_on_failure(built,monkeypatch):
    artifact,decoder=built
    import tools.k6_sotto_e2e_mock as mock
    r=MockSottoRadio(artifact,decoder)
    r.install()
    assert r.select()=='SOTTO' and r.radio_state=='RX'
    # Pretend a previously CRC-validated session has been received.
    r._render_validated_receive(artifact['reference'])
    assert r.speaker_next() is not None
    assert r.speaker_cursor==160

    def checked_tx(*args,**kwargs):
        assert r.radio_state=='TX'
        assert r.ptt and r.running
        assert r.speaker_cursor==0 and r.rx_audio is None
        with pytest.raises(RuntimeError,match='speaker'):
            r.speaker_next()
        with pytest.raises(RuntimeError):
            r._render_validated_receive(artifact['reference'])
        # Simulate the actual ARM emulator raising, not a successful RF frame.
        raise RuntimeError('emulated FIFO abort')

    monkeypatch.setattr(mock,'trial',checked_tx)
    with pytest.raises(RuntimeError,match='FIFO abort'):
        r.transmit()
    assert r.radio_state=='RX' and not r.ptt and not r.running
    assert r.rx_audio is None and r.speaker_next() is None
    # Failures must never leave TX keyed or silently replay old voice.
    r.remove()
    assert r.radio_state=='FM'


def test_no_idle_sotto_receive_in_fm(built):
    artifact,decoder=built
    r=MockSottoRadio(artifact,decoder)
    with pytest.raises(RuntimeError,match='outside RX'):
        r._render_validated_receive(artifact['reference'])
    r.install()
    with pytest.raises(RuntimeError,match='outside RX'):
        r._render_validated_receive(artifact['reference'])
    r.select()
    r._render_validated_receive(artifact['reference'])
    assert r.radio_state=='RX' and r.status()['playback_ready']
    r.remove()
    assert r.radio_state=='FM' and not r.status()['playback_ready']
