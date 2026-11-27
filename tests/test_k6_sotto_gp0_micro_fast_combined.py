"""Source-preserving GP0 RAM-micro + fast4 codec integration and exact RF oracle.

Uses compiled ARM module, virtual microphone DMA, virtual FSK and native
Sotto decoder. Intentionally performs no physical RF, UART, flash, SPI writes.
"""
from pathlib import Path
import hashlib
import numpy as np
import pytest
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_module import (build, PIN, SOTTO_ARENA_BYTES,
                                    SOTTO_LIVE_SCRATCH_REQUIRED,
                                    SOTTO_SCRATCH_RESERVE,
                                    SOTTO_MODULE_OVERHEAD,
                                    SOTTO_MICRO_SCRATCH_SAVED)
from tools.k6_sotto_virtual_link import prepare,trial,accepted

ROOT=Path(__file__).resolve().parents[1]
OPTS=dict(fast_div=True,fast_history_copy=True,fast_cb_pair=True,fast_lag_pair=True)

@pytest.fixture(scope='module')
def pair(tmp_path_factory):
    t=np.arange(8293)
    pcm=(7000*np.sin(2*np.pi*320*t/8000)+2000*np.sin(2*np.pi*870*t/8000)).astype('<i2')
    d=tmp_path_factory.mktemp('micro-fast4-rf')
    a=prepare(ROOT,d,pcm=pcm,gp0=True)
    code,entry=build(host_resources=True,gp0_irq_tx=True,**OPTS)
    code_m,entry_m=build(host_resources=True,gp0_irq_tx=True,ram_micro=True,**OPTS)
    return { 'fast4':{**a,'code':code,'entry':entry},
             'fast4_micro':{**a,'code':code_m,'entry':entry_m}}

def test_pinned_source_untouched_and_module_scratch_margin(pair):
    import subprocess
    assert subprocess.check_output(['git','-C',str(ROOT/'sotto'),'rev-parse','HEAD'],text=True).strip()==PIN
    fast4=pair['fast4']['code']
    micro=pair['fast4_micro']['code']
    assert (len(fast4),len(micro))==(9784,9760)
    assert pair['fast4']['entry']==pair['fast4_micro']['entry']==16
    assert hashlib.sha256(fast4).hexdigest()=='9834e44bb81595a69c354c39fdc14b2a945e2fdc653a46293dd618b14e4e5bc0'
    assert hashlib.sha256(micro).hexdigest()=='7c91f220ad6d7577dfac86cd2eaf3d53a6a14b2ad671aa870596031025092121'
    margin=lambda code, micro=False: (SOTTO_ARENA_BYTES-SOTTO_SCRATCH_RESERVE-SOTTO_MODULE_OVERHEAD-
                    len(code)-SOTTO_LIVE_SCRATCH_REQUIRED+(SOTTO_MICRO_SCRATCH_SAVED if micro else 0))
    assert margin(fast4)==48
    assert margin(micro,True)==268

def test_micro_fast4_arm_rf_is_codec_exact_and_no_pcm_loss(pair):
    r=trial(pair['fast4_micro'],Channel(),cpi=2.5,adc_cycles=4000,
            spi_us=6.19213,spi_byte_us=.940394,fd_us=60,
            fsk_cooperative_model=True)
    assert accepted(r)
    assert r['samples']==r['samples_expected']==8293
    assert r['codec_exact'] and r['wire_decoder_agrees'] and r['codec_frames']==26
    assert r['session']['complete'] and r['session']['valid']==6 and r['session']['invalid']==0
    assert r['pcm_highwater']<=2 and not r['pcm_lost']
    assert not r['tx']['active'] and not r['lease_expired']

def test_extreme_contention_remains_fail_closed(pair):
    r=trial(pair['fast4_micro'],Channel(),cpi=3.0,adc_cycles=4000,
            spi_us=6.19213,spi_byte_us=.940394,fd_us=60,
            fsk_cooperative_model=True)
    assert not accepted(r)
    assert r['pcm_highwater']==4 and r['pcm_lost']
    assert r['module_return']==-12 and not r['session']['complete']
    assert not r['tx']['active'] and not r['lease_expired']
