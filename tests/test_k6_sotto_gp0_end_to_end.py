"""Run the actual Cortex-M0 SOTTO GP0 module through PCM, encoder, FSK RF and RX.

The physical DMA IRQ service and mic are simulated here; codec and module
machine code, PTT-release flush, packetization, RF modem and C wire decoder
are real implementations. This must not be presented as an on-radio test.
"""
from pathlib import Path
import numpy as np
import pytest
from sim.k6emu.rf.virtual_link import Channel
from tools.k6_sotto_virtual_link import prepare,trial,accepted

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def gp0(tmp_path_factory):
    n=8293  # ends 13 samples into the final 80-sample PCM block
    t=np.arange(n)
    pcm=(9000*np.sin(2*np.pi*320*t/8000)).astype('<i2')
    return prepare(ROOT,tmp_path_factory.mktemp('sotto-gp0-e2e'),pcm=pcm,gp0=True)

@pytest.mark.parametrize('prefix',[0,2])
def test_gp0_midblock_ptt_flushes_voice_and_end_control(gp0,prefix):
    r=trial(gp0,Channel(prefix_every=prefix),cpi=1.5,spi_us=10,
            adc_cycles=2400,capture_codec=True)
    assert accepted(r),r
    assert r['gp0'] and r['gp0_starts']==1 and r['gp0_stops']==1
    assert r['samples']==r['samples_expected']==8293
    assert r['module_return']==r['codec_frames']==26
    assert r['tx_before_release']>=1
    assert r['received']==r['tx']['completed']==6
    assert r['session']['complete'] and r['session']['valid']==6
    assert r['session']['invalid']==0
    assert r['session']['offset_2']==(3 if prefix else 0)
    assert not r['tx']['active'] and not r['lease_expired'] and not r['pcm_lost']
    assert len(r['received_codec_bytes'])==156


def test_gp0_scratch_fits_expanded_arena(gp0):
    from sim.k6emu.rf.virtual_link import VirtualAir,VirtualTxBackend
    from tools.k6_sotto_virtual_link import HZ,LiveTx
    air=VirtualAir(Channel())
    backend=VirtualTxBackend(air,240000,HZ)
    try:
        r=LiveTx(gp0,backend)
        assert r.scratch >= 4080 and r.scratch<4150
        assert r.gp0
    finally:backend.close()


def test_gp0_abort_under_excessive_isr_contention(gp0):
    # The old decoder model makes codec work interrupt-preemptible and has
    # a known failing high-contention setting. A lost PCM block must never
    # be misreported as a successful complete digital-voice session.
    r=trial(gp0,Channel(),cpi=1.5,spi_us=34,adc_cycles=5745)
    assert not accepted(r)
    assert r['module_return']<0 and r['pcm_lost']
    assert not r['tx']['active'] and r['gp0_starts']==1
    assert not r['session']['complete']
