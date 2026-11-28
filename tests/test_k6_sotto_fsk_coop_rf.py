"""Offline ARM codec and virtual RF comparison for the opt-in FSK preload.

The seven-stage FSK host in tools/k6_sotto_virtual_link.py is a behavioral
model, not firmware execution. The real Rust host is compiled and tested in
`test_k6_sotto_fsk_coop_native.py`. No physical radio is involved.
"""
from pathlib import Path

import numpy as np
import pytest

from tools.k6_sotto_module import build
from tools.k6_sotto_virtual_link import prepare, trial, accepted
from sim.k6emu.rf.virtual_link import Channel

ROOT=Path(__file__).resolve().parents[1]
FIXED=dict(spi_us=6.19212962962963,spi_byte_us=0.9403935185185185,
           adc_cycles=4000,fd_us=330)


@pytest.fixture(scope='module')
def artifact(tmp_path_factory):
    n=9600;t=np.arange(n)/8000
    pcm=(6500*np.sin(2*np.pi*320*t)+2200*np.sin(2*np.pi*870*t)).astype('<i2')
    a=prepare(ROOT,tmp_path_factory.mktemp('fsk-coop-rf'),pcm=pcm,gp0=True)
    code,entry=build(host_resources=True,gp0_irq_tx=True,fast_div=True,
                     fast_history_copy=True,fast_cb_pair=True,fast_lag_pair=True)
    assert len(code)==9784
    return {**a,'code':code,'entry':entry}


def run(artifact,*,cpi,coop,fd=330):
    return trial(artifact,Channel(),cpi=cpi,fsk_cooperative_model=coop,
                 **{**FIXED,'fd_us':fd})


def test_coop_preserves_real_arm_codec_and_independent_wire_decoder(artifact):
    old=run(artifact,cpi=1.5,coop=False)
    new=run(artifact,cpi=1.5,coop=True)
    assert accepted(old) and accepted(new)
    for r in (old,new):
        assert r['samples']==r['samples_expected']==9600
        assert r['codec_exact'] and r['codec_frames']==30
        assert r['session']['valid']==6 and r['session']['invalid']==0
        assert r['session']['complete'] and r['wire_decoder_agrees']
        assert r['gp0_starts']==r['gp0_stops']==1
        assert not r['tx']['active'] and not r['pcm_lost']
    assert old['pcm_highwater']==3 and new['pcm_highwater']==2
    # Spreading writes plus 20ms settling increases time-to-first-RF.
    assert new['first_rf_ms']>old['first_rf_ms']+50
    assert new['fsk_cooperative_model']


def test_coop_delays_but_does_not_hide_real_queue_overflow(artifact):
    old=run(artifact,cpi=2.5,coop=False)
    new=run(artifact,cpi=2.5,coop=True)
    assert not accepted(old) and not accepted(new)
    assert old['samples']==1120 and new['samples']==1600
    for r in (old,new):
        assert r['pcm_lost'] and r['pcm_highwater']==4
        assert r['module_return']==-12
        assert not r['session']['complete'] and not r['tx']['active']
        assert r['gp0_starts']==r['gp0_stops']==1
    assert new['wire_decoder_agrees']


def test_less_spi_cost_has_complete_session_even_with_cpu_contention(artifact):
    old=run(artifact,cpi=2.5,coop=False,fd=200)
    new=run(artifact,cpi=2.5,coop=True,fd=200)
    assert accepted(old) and accepted(new)
    assert old['pcm_highwater']==new['pcm_highwater']==3
    assert new['codec_exact'] and new['session']['valid']==6
    assert new['session']['invalid']==0 and new['wire_decoder_agrees']


def test_three_second_mixed_audio_stays_exact_and_within_pcm_queue(tmp_path, artifact):
    """Long capture also checks RF pacing and the bounded module FIFO queue."""
    n=24000;t=np.arange(n)/8000
    x=np.zeros(n);a=n//6
    x[a:2*a]=.5*np.sin(2*np.pi*185*t[a:2*a])
    x[2*a:3*a]=.35*np.sin(2*np.pi*315*t[2*a:3*a])+.2*np.sin(2*np.pi*631*t[2*a:3*a])
    x[3*a:4*a]=.2*np.sin(2*np.pi*250*t[3*a:4*a])+.16*np.sin(2*np.pi*730*t[3*a:4*a])
    x[4*a:5*a]=np.random.default_rng(2711).normal(0,.24,size=a)
    x[5*a:]=.22*np.sin(2*np.pi*110*t[5*a:])+.3*np.sin(2*np.pi*540*t[5*a:])
    pcm=(x*20000).clip(-32768,32767).astype('<i2')
    scratch=tmp_path/'mixed-long'
    scratch.mkdir()
    long=prepare(ROOT,scratch,pcm=pcm,gp0=True)
    long={**long,'code':artifact['code'],'entry':artifact['entry']}
    normal=run(long,cpi=1.5,coop=False)
    cooperative=run(long,cpi=1.5,coop=True)
    assert accepted(normal) and accepted(cooperative)
    assert normal['pcm_highwater']==3 and cooperative['pcm_highwater']==2
    assert cooperative['samples']==24000 and cooperative['codec_frames']==75
    assert cooperative['codec_exact'] and cooperative['session']['valid']==12
    assert cooperative['session']['invalid']==0 and cooperative['wire_decoder_agrees']
    assert cooperative['gp0_starts']==cooperative['gp0_stops']==1
    assert not cooperative['pcm_lost'] and not cooperative['tx']['active']
