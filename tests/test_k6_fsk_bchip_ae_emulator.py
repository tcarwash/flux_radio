"""Real Thumbv6-M Unicorn execution of frozen FSK-09S-H module.

The RFIC is an explicitly labeled behavioral mock (NOT physical evidence),
with 300 words/second FSK drain, FIFO threshold register [9:3], IRQ write
0 before read, and programmable IRQ encoding. No radio, USB, SDR or TX.
The compiled Cortex-M0 binary is executed by Unicorn, not a Python port.
"""
from __future__ import annotations
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'))
sys.path.insert(0,str(ROOT/'tools'))
from test_k6_fsklab import run_m0,saved_regs,TX_REGS
from k6_fsk_lab_vector import make_probe_vector
from k6_fsk_bchip_ae_analyze import parse_ae_report,assess_ae

SRC=ROOT/'c/k6/mod/fsk_bchip_ae_qual_armed.c'


class BChipIRQModel:
    """One test-only hypothesis: AE flag at [9:3] with latched REG02 events."""
    def __init__(self,*,encoding='bit14',ae=True,done=True,require_latch=True,
                 early=False,stuck=False,rate_words_per_s=300,
                 threshold_layout='upstream', irq_silent=False):
        self.encoding,self.ae,self.done=encoding,ae,done
        self.require_latch,self.early,self.stuck=require_latch,early,stuck
        self.rate=rate_words_per_s
        self.threshold_layout=threshold_layout
        self.irq_silent=irq_silent
        self.irq_enabled=False
        self.register_writes=[]
        self.frames=[]
        self.current=[]
        self.frame_start=None
        self.preloaded=None
        self.irq_latched=0
        self.threshold=0
        self.irq_read_without_latch=0
        self.irq_clear_count=0
        self.max_occupancy=0
        self.started_times=[]
        self.unsafe_mid_tx_fifo_writes=[]
        self.last_register_write=None

    def key(self,name,us):
        return 0

    def _consumed(self,us):
        if self.frame_start is None:
            return 0
        return max(0,min(len(self.current),int((us-self.frame_start)*self.rate/1e6)))

    def pending(self,us):
        if (self.frame_start is None or not self.ae or
                not self.irq_enabled or self.irq_silent):return 0
        consumed=self._consumed(us)
        occupancy=len(self.current)-consumed
        activated=(occupancy<=self.threshold)
        if self.early:activated=True
        if self.stuck:activated=False
        if not activated:return 0
        return 0x4000 if self.encoding=='bit14' else 0x000e

    def write(self,reg,value,us):
        self.register_writes.append((reg,value,us))
        self.last_register_write=(reg,value,us)
        if reg==0x3f:
            self.irq_enabled=bool(value&0x4000)
        if reg==0x5e:
            self.threshold=((value>>3)&0x7f) if self.threshold_layout=='upstream' else ((value>>6)&0x3f)
        if reg==0x59 and value==0x80f8:
            # Clear only when explicitly requested, not on every TX rearm.
            self.current=[]
            self.frame_start=None
            self.irq_latched=0
        if reg==0x5f:
            if self.frame_start is not None:
                self.unsafe_mid_tx_fifo_writes.append((value,us))
            self.current.append(value)
            self.max_occupancy=max(self.max_occupancy,len(self.current)-self._consumed(us))
        if reg==0x59 and value==0x08f8:
            self.frame_start=us
            self.preloaded=tuple(self.current)
            self.frames.append(self.preloaded)
            self.started_times.append(us)
        if reg==0x02 and value==0:
            self.irq_clear_count+=1
            self.irq_latched=self.pending(us)

    def read(self,reg,us):
        if reg==0x02:
            if self.last_register_write is None or self.last_register_write[0]!=0x02:
                self.irq_read_without_latch+=1
            return self.irq_latched if self.require_latch else self.pending(us)
        if reg==0x0c:
            # TX completion is independently gated on modem's entire 49-word
            # frame being transmitted: no shortcut based on REG02 AE.
            return int(self.done and self.frame_start is not None
                and us-self.frame_start>=49/self.rate*1e6+8000)
        return None


def emulate(*,encoding='bit14',ae=True,done=True,early=False,stuck=False,
            threshold_layout='upstream',irq_silent=False,
            keys=(),rx_hz=433500000,chip=0x4829,level=0x3044):
    baseline={**saved_regs(),0x02:0,0x5e:level,0x00:chip}
    chip_model=BChipIRQModel(encoding=encoding,ae=ae,done=done,early=early,stuck=stuck,
                             threshold_layout=threshold_layout,irq_silent=irq_silent)
    tx={}
    ret,lines,after,writes=run_m0(SRC,tx=tx,fsk=chip_model,registers=baseline,
                                  keys=keys,rx_hz=rx_hz)
    return ret,lines,after,writes,tx,chip_model,baseline


@pytest.mark.parametrize('encoding', ['bit14','event14'])
def test_real_cortex_m0_binary_emulates_two_49_word_frames_with_threshold_shift(encoding):
    pytest.importorskip('unicorn')
    ret,lines,after,writes,tx,m,baseline=emulate(encoding=encoding)
    assert ret==98,(ret,lines)
    assert len(m.frames)==2
    words,_=make_probe_vector(word_count=64,seed=0xACE1)
    wanted=(*words[:48],0xa55a)
    assert all(tuple(frame)==wanted for frame in m.frames)
    assert len(m.started_times)==2
    assert not m.unsafe_mid_tx_fifo_writes
    assert m.max_occupancy<=49
    assert m.irq_read_without_latch==0
    assert m.irq_clear_count>=2
    assert [x[1] for x in writes if x[0]==0x5e][:2]==[0x30c4,0x3044]
    assert (0x3f,0xc000) in writes
    assert (0x5d,0x6100) in writes
    assert len([x for x in writes if x==(0x59,0x08f8)])==2
    assert tx['log'][0][0]=='tx_on'
    assert tx['log'][-1][0]=='tx_off'
    assert [name for name,_,_ in tx['log']].count('tx_on')==1
    assert [name for name,_,_ in tx['log']].count('tx_off')==1
    for reg in (*TX_REGS,0x5e):assert after.get(reg)==baseline.get(reg),reg
    parsed=parse_ae_report(lines)
    a=assess_ae(lines)
    assert parsed[0]['th']==24 and parsed[1]['th']==8
    yes='bit' if encoding=='bit14' else 'evt'
    got=next(x for x in a['possible_interrupt_encodings'] if x['mode']==yes)
    assert got['passes_timing_discriminator'],(lines,a)
    assert 40<=got['measured_delta_ms']<=67
    assert a['not_yet_safe_for_refill']


@pytest.mark.parametrize('mode', ['noirq','early','stuck','legacy_threshold','noirqsource','nocomplete'])
def test_real_binary_emulated_negative_irq_and_completion_cases_fail_closed(mode):
    pytest.importorskip('unicorn')
    args=dict(ae=mode!='noirq',early=mode=='early',stuck=mode=='stuck',
              threshold_layout='legacy' if mode=='legacy_threshold' else 'upstream',
              irq_silent=mode=='noirqsource',done=mode!='nocomplete')
    ret,lines,after,writes,tx,m,baseline=emulate(**args)
    assert tx['log'][-1][0]=='tx_off'
    assert not m.unsafe_mid_tx_fifo_writes
    for reg in (*TX_REGS,0x5e):assert after.get(reg)==baseline.get(reg),reg
    if mode=='nocomplete':
        assert ret==-14
        assert any('aborted' in l for l in lines)
    else:
        assert ret==98
        assert not assess_ae(lines)['provisional_b_ae_present']


@pytest.mark.parametrize('hz,chip',[(144390000,0x4829),(433500000,0x4819)])
def test_real_binary_preflight_refuses_without_keying(hz,chip):
    pytest.importorskip('unicorn')
    ret,lines,after,writes,tx,m,baseline=emulate(rx_hz=hz,chip=chip)
    assert ret in (-2,-6)
    assert not tx.get('log')
    assert not writes


def test_inert_real_binary_emulation_never_keys():
    pytest.importorskip('unicorn')
    tx={}
    ret,lines,after,writes=run_m0(ROOT/'c/k6/mod/fsk_bchip_ae_qual.c',tx=tx)
    assert ret==-9
    assert 'inert' in ' '.join(lines)
    assert not tx.get('log') and not writes
