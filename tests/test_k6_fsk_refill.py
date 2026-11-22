"""Experimental FD6818B guarded 128-word FIFO streaming module emulator tests."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from modem import k6mod
from test_k6_i2s import need_clang
from test_k6_fsklab import run_m0,saved_regs,TX_REGS
from k6_fsk_lab_vector import make_probe_vector

SRC=ROOT/"c/k6/mod/fsk_refill.c"


class RefillModel:
    def __init__(self, *, interrupt=True, complete=True):
        self.words=[]
        self.started=None
        self.interrupt=interrupt
        self.complete=complete
        self.threshold=1
        self.max_occupancy=0
        self.irq_reads=0
        self.writes=[]
        self.wrong_count=False

    def key(self,name,us):
        return 0

    def consumed(self,us):
        if self.started is None:
            return 0
        return max(0,int((us-self.started)*4800/(16*1000000)))

    def write(self,reg,value,us):
        self.writes.append((reg,value,us))
        if reg==0x5e:
            self.threshold=(value>>6)&63
        if reg==0x59 and value==0x08f8:
            self.started=us
        if reg==0x5f:
            self.words.append(value)
            occupancy=len(self.words)-self.consumed(us)
            self.max_occupancy=max(self.max_occupancy,occupancy)
            if occupancy>64:
                self.wrong_count=True

    def read(self,reg,us):
        if reg==0x02:
            self.irq_reads+=1
            if self.interrupt and self.started is not None and (
                    len(self.words)-self.consumed(us))<=self.threshold:
                return 0x4000
            return 0
        if reg==0x0c:
            return int(self.complete and self.started is not None
                       and len(self.words)==128 and self.consumed(us)>=128)
        return None


@pytest.fixture
def enabled(tmp_path):
    p=tmp_path/"refill-enabled.c"
    p.write_text(
        "#define FSK_REFILL_ENABLE 1\n"
        "#define FSK_REFILL_FIXTURE_ACK 1\n"
        "#define FSK_REFILL_WATCHDOG_ACK 1\n"
        f'#include "{SRC}"\n'
    )
    return p


@need_clang
def test_inert_image_never_keys():
    blob,entry=k6mod.build(str(SRC))
    assert entry==0 and len(blob)<512
    tx={}
    rc,lines,after,w=run_m0(SRC,tx=tx)
    assert rc==-9 and not tx.get("log") and not w


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_reject_compile_without_both_guard_flags(tmp_path,fixture,watchdog):
    p=tmp_path/"refill-guard.c"
    p.write_text(
        "#define FSK_REFILL_ENABLE 1\n"
        f"#define FSK_REFILL_FIXTURE_ACK {fixture}\n"
        f"#define FSK_REFILL_WATCHDOG_ACK {watchdog}\n"
        f'#include "{SRC}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_refill_full_128_word_frame_and_restore(enabled):
    blob,entry=k6mod.build(str(enabled))
    assert entry==0 and len(blob)<4096
    tx={}
    regs={**saved_regs(),0x5e:0x3044,0x02:0}
    m=RefillModel()
    rc,lines,after,w=run_m0(enabled,tx=tx,fsk=m,registers=regs)
    assert rc==128,(rc,lines)
    assert not m.wrong_count
    assert m.max_occupancy<=64
    assert m.threshold==1  # REG_5E is restored to its original snapshot
    assert any(reg==0x5e and value==0x3604 for reg,value in w)
    orig,_=make_probe_vector()
    wanted=(*orig[:56],*[0]*8,*[0]*8,*[0xffff]*8,
            *[~v & 65535 for v in orig[16:]])
    assert tuple(m.words)==wanted
    assert len(m.words)==128
    assert (0x5d,0xff00) in w
    assert (0x3f,0xc000) in w
    assert (0x5e,0x3604) in w
    assert len([v for v in w if v==(0x59,0x08f8)])==1
    assert tx["log"][-1][0]=="tx_off"
    assert [x[0] for x in tx["log"]].count("tx_on")==1
    assert [x[0] for x in tx["log"]].count("tx_off")==1
    for reg in (*TX_REGS,0x5e):
        assert after.get(reg,0)==regs.get(reg,0),(reg,after.get(reg))


@need_clang
def test_no_ae_irq_aborts_safely_without_refill(enabled):
    regs={**saved_regs(),0x5e:0x3044,0x02:0}
    tx={}
    m=RefillModel(interrupt=False)
    rc,lines,after,w=run_m0(enabled,tx=tx,fsk=m,registers=regs)
    assert rc==-16,(rc,lines)
    assert len(m.words)==48
    assert tx["log"][-1][0]=="tx_off"
    for reg in (*TX_REGS,0x5e):
        assert after.get(reg,0)==regs.get(reg,0)


@need_clang
def test_no_completion_aborts_and_restores(enabled):
    regs={**saved_regs(),0x5e:0x3044,0x02:0}
    tx={}
    m=RefillModel(complete=False)
    rc,lines,after,w=run_m0(enabled,tx=tx,fsk=m,registers=regs)
    assert rc==-14
    assert tx["log"][-1][0]=="tx_off"
    assert len(m.words)==128
    for reg in (*TX_REGS,0x5e):
        assert after.get(reg,0)==regs.get(reg,0)


@need_clang
@pytest.mark.parametrize("kwargs,code",[
    ({"rx_hz":144390000},-2),
    ({"registers":{**saved_regs(),0:0x4819}},-6),
    ({"registers":{**saved_regs(),0x67:400}},-7),
    ({"registers":{**saved_regs(),0x3f:0x8000}},-8),
])
def test_refusal_before_tx(enabled,kwargs,code):
    tx={}
    rc,lines,after,w=run_m0(enabled,tx=tx,**kwargs)
    assert rc==code
    assert not tx.get("log")
    assert not w


def test_source_keeps_unverified_threshold_explicit():
    src=SRC.read_text()
    assert "REG_5E" in src
    assert "NOT established for FD6818B" in src
    assert "FSK_LEASE_MS 1900u" in src
    for forbidden in ("cal_write(","fl_prog(","fl_erase("):
        assert forbidden not in src
