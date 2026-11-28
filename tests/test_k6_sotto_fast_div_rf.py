"""Compiled ARM GP0 Sotto divider/history-copy RF regression, entirely offline.

Uses actual relocatable module, real codebook/deck bytes, independent native
codec oracle, FSK virtual RF, and compiled ARM wire decoder. No physical PTT.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pytest

from tools.k6_sotto_module import build
from tools.k6_sotto_virtual_link import prepare,trial,accepted
from sim.k6emu.rf.virtual_link import Channel

ROOT=Path(__file__).resolve().parents[1]
SPI_US=6.19212962962963
SPI_BYTE_US=0.9403935185185185

@pytest.fixture(scope='module')
def actual_modules(tmp_path_factory):
    n=8293
    t=np.arange(n)
    pcm=(7000*np.sin(2*np.pi*320*t/8000)+
         2000*np.sin(2*np.pi*870*t/8000)).astype('<i2')
    original=prepare(ROOT,tmp_path_factory.mktemp('sotto-fast-div-e2e'),pcm=pcm,gp0=True)
    variants={'baseline':original}
    for name,kw in (
        ('fast_div',{'fast_div':True}),
        ('fast_history',{'fast_history_copy':True}),
        ('combined',{'fast_div':True,'fast_history_copy':True}),
        ('combined_cb_pair',{'fast_div':True,'fast_history_copy':True,'fast_cb_pair':True}),
    ):
        code,entry=build(host_resources=True,gp0_irq_tx=True,**kw)
        assert original['entry']==entry
        assert len(code)<len(original['code'])
        variants[name]={**original,'code':code,'entry':entry}
    return variants

def replay(artifact,*,cpi=2.5,adc=4000):
    return trial(artifact,Channel(),cpi=cpi,adc_cycles=adc,
                 spi_us=SPI_US,spi_byte_us=SPI_BYTE_US,fd_us=60)

def test_original_gp0_overflows_at_high_load_and_dekeys(actual_modules):
    base=actual_modules['baseline']
    r=replay(base)
    assert not accepted(r)
    assert r['pcm_lost'] and r['pcm_highwater']==4
    assert r['module_return']==-12
    assert not r['tx']['active'] and not r['session']['complete']
    assert r['gp0_starts']==1 and r['gp0_stops']==1

def test_fast_div_survives_same_high_load_and_matches_native_voice(actual_modules):
    fast=actual_modules['fast_div']
    r=replay(fast)
    assert accepted(r),r
    assert r['samples']==r['samples_expected']==8293
    assert r['pcm_highwater']<=2 and not r['pcm_lost']
    assert r['codec_exact'] and r['codec_frames']==26
    assert r['session']['complete'] and r['session']['valid']==6
    assert r['session']['invalid']==0 and r['wire_decoder_agrees']
    assert r['gp0_starts']==1 and r['gp0_stops']==1
    assert not r['tx']['active'] and not r['lease_expired']

def test_fast_div_still_fails_closed_under_more_extreme_contention(actual_modules):
    original=actual_modules['baseline']
    fast=actual_modules['fast_div']
    a=replay(original,cpi=3,adc=4000)
    b=replay(fast,cpi=3,adc=4000)
    assert a['pcm_lost'] and b['pcm_lost']
    assert b['samples']>a['samples']
    assert b['pcm_highwater']==4 and b['module_return']==-12
    assert not b['tx']['active'] and not b['session']['complete']
    assert b['gp0_starts']==1 and b['gp0_stops']==1

def test_fast_div_receives_prefixed_fsk_frames_under_high_load(actual_modules):
    fast=actual_modules['fast_div']
    r=trial(fast,Channel(prefix_every=2),cpi=2.5,adc_cycles=4000,
            spi_us=SPI_US,spi_byte_us=SPI_BYTE_US,fd_us=60)
    assert accepted(r),r
    assert r['codec_exact'] and r['wire_decoder_agrees']
    assert r['session']['complete'] and r['session']['valid']==6
    assert r['session']['offset_2']==3
    assert not r['pcm_lost'] and r['pcm_highwater']<=2


def test_history_copy_is_bit_exact_when_not_cpu_limited(actual_modules):
    history=actual_modules['fast_history']
    r=replay(history,cpi=1.5,adc=4000)
    assert accepted(r),r
    assert r['samples']==r['samples_expected']==8293
    assert r['codec_exact'] and r['codec_frames']==26
    assert r['session']['complete'] and r['session']['valid']==6
    assert r['session']['invalid']==0 and r['wire_decoder_agrees']
    assert r['pcm_highwater']<=2 and not r['pcm_lost']
    assert not r['tx']['active'] and not r['lease_expired']


def test_history_copy_improves_queue_progress_and_combined_is_exact(actual_modules):
    base=actual_modules['baseline']
    history=actual_modules['fast_history']
    combined=actual_modules['combined']
    a=replay(base,cpi=2.5,adc=4000)
    b=replay(history,cpi=2.5,adc=4000)
    c=replay(combined,cpi=2.5,adc=4000)
    assert a['pcm_lost'] and a['samples']==1920
    assert b['pcm_lost'] and b['samples']==2880
    assert b['samples']>a['samples']
    assert accepted(c),c
    assert c['samples']==c['samples_expected']==8293
    assert c['pcm_highwater']<=2 and not c['pcm_lost']
    assert c['codec_exact'] and c['codec_frames']==26
    assert c['session']['complete'] and c['session']['valid']==6
    assert c['session']['invalid']==0 and c['wire_decoder_agrees']
    assert c['gp0_starts']==1 and c['gp0_stops']==1
    assert not c['tx']['active'] and not c['lease_expired']


def test_combined_fast_math_passes_moderate_and_fails_closed_at_extreme_load(actual_modules):
    combined=actual_modules['combined']
    moderate=replay(combined,cpi=3,adc=3000)
    extreme=replay(combined,cpi=3,adc=4000)
    assert accepted(moderate),moderate
    assert moderate['samples']==moderate['samples_expected']==8293
    assert moderate['codec_exact'] and moderate['session']['valid']==6
    assert moderate['session']['invalid']==0 and moderate['wire_decoder_agrees']
    assert not moderate['tx']['active'] and not moderate['lease_expired']
    assert extreme['pcm_lost'] and extreme['pcm_highwater']==4
    assert extreme['samples']==2560 and extreme['module_return']==-12
    assert not extreme['tx']['active'] and not extreme['session']['complete']
    assert extreme['gp0_starts']==1 and extreme['gp0_stops']==1


def test_paired_codebook_score_preserves_exact_voice_and_improves_extreme_progress(actual_modules):
    combined=actual_modules['combined']
    paired=actual_modules['combined_cb_pair']
    nominal=replay(paired,cpi=3,adc=3000)
    assert accepted(nominal),nominal
    assert nominal['samples']==nominal['samples_expected']==8293
    assert nominal['codec_exact'] and nominal['codec_frames']==26
    assert nominal['session']['complete'] and nominal['session']['valid']==6
    assert nominal['session']['invalid']==0 and nominal['wire_decoder_agrees']
    assert not nominal['tx']['active'] and not nominal['lease_expired']
    a=replay(combined,cpi=3,adc=4000)
    b=replay(paired,cpi=3,adc=4000)
    assert a['pcm_lost'] and b['pcm_lost']
    assert a['samples']==2560 and b['samples']==2880 and b['samples']>a['samples']
    assert b['pcm_highwater']==4 and b['module_return']==-12
    assert b['gp0_starts']==1 and b['gp0_stops']==1
    assert not b['tx']['active'] and not b['session']['complete']
