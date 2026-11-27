"""Compiled ARM GP0 Sotto fast-divider RF regression, entirely offline.

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
    code,entry=build(host_resources=True,gp0_irq_tx=True,fast_div=True)
    optimized={**original,'code':code,'entry':entry}
    assert original['entry']==entry
    assert len(code)<len(original['code'])
    return original,optimized

def replay(artifact,*,cpi=2.5,adc=4000):
    return trial(artifact,Channel(),cpi=cpi,adc_cycles=adc,
                 spi_us=SPI_US,spi_byte_us=SPI_BYTE_US,fd_us=60)

def test_original_gp0_overflows_at_high_load_and_dekeys(actual_modules):
    base,_=actual_modules
    r=replay(base)
    assert not accepted(r)
    assert r['pcm_lost'] and r['pcm_highwater']==4
    assert r['module_return']==-12
    assert not r['tx']['active'] and not r['session']['complete']
    assert r['gp0_starts']==1 and r['gp0_stops']==1

def test_fast_div_survives_same_high_load_and_matches_native_voice(actual_modules):
    _,fast=actual_modules
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
    original,fast=actual_modules
    a=replay(original,cpi=3,adc=4000)
    b=replay(fast,cpi=3,adc=4000)
    assert a['pcm_lost'] and b['pcm_lost']
    assert b['samples']>a['samples']
    assert b['pcm_highwater']==4 and b['module_return']==-12
    assert not b['tx']['active'] and not b['session']['complete']
    assert b['gp0_starts']==1 and b['gp0_stops']==1

def test_fast_div_receives_prefixed_fsk_frames_under_high_load(actual_modules):
    _,fast=actual_modules
    r=trial(fast,Channel(prefix_every=2),cpi=2.5,adc_cycles=4000,
            spi_us=SPI_US,spi_byte_us=SPI_BYTE_US,fd_us=60)
    assert accepted(r),r
    assert r['codec_exact'] and r['wire_decoder_agrees']
    assert r['session']['complete'] and r['session']['valid']==6
    assert r['session']['offset_2']==3
    assert not r['pcm_lost'] and r['pcm_highwater']<=2
