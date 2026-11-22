"""Two-frame FSK FIFO rearm lab, Unicorn tests; never touches live RF."""
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

SOURCE=ROOT/"c/k6/mod/fsk_boundary.c"


class TwoFrameFskModel:
    def __init__(self, *, completed_frames=2):
        self.fifos=[[]]
        self.enabled=[]
        self.started=None
        self.completed_frames=completed_frames
        self.start_count=0
        self.events=[]

    def key(self,name,us):
        self.events.append((name,us))
        return 0

    def write(self,reg,value,us):
        self.events.append((reg,value,us))
        if reg==0x59 and value==0x80f8:
            if self.started is not None:
                self.fifos.append([])
            self.started=None
        if reg==0x5f:
            self.fifos[-1].append(value)
        if reg==0x59 and value==0x08f8:
            self.started=us
            self.start_count+=1
            self.enabled.append(us)

    def read(self,reg,us):
        if reg==0x0c:
            return int(self.started is not None
                       and self.start_count<=self.completed_frames
                       and us-self.started>=300000)
        return None


@pytest.fixture
def enabled(tmp_path):
    path=tmp_path/"fsk_boundary_enabled.c"
    path.write_text(
        "#define FSK_BOUNDARY_ENABLE 1\n"
        "#define FSK_BOUNDARY_FIXTURE_ACK 1\n"
        "#define FSK_BOUNDARY_WATCHDOG_ACK 1\n"
        f'#include "{SOURCE}"\n'
    )
    return path


@need_clang
def test_inert_default_does_not_key_or_write():
    code,entry=k6mod.build(str(SOURCE))
    assert entry==0
    assert len(code)<512
    tx={}
    rc,lines,regs,write=run_m0(SOURCE,tx=tx)
    assert rc==-9
    assert lines==["fsk boundary inert; bench build required"]
    assert not tx.get("log") and not write


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(1,0),(0,1)])
def test_gating_rejects_missing_ack(tmp_path,fixture,watchdog):
    p=tmp_path/"boundary_no_ack.c"
    p.write_text(
        "#define FSK_BOUNDARY_ENABLE 1\n"
        f"#define FSK_BOUNDARY_FIXTURE_ACK {fixture}\n"
        f"#define FSK_BOUNDARY_WATCHDOG_ACK {watchdog}\n"
        f'#include "{SOURCE}"\n'
    )
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_two_frames_one_tx_lease_and_register_restore(enabled):
    code,entry=k6mod.build(str(enabled))
    assert entry==0 and len(code)<4096
    baseline=saved_regs()
    tx={}
    fsk=TwoFrameFskModel()
    rc,lines,after,w=run_m0(enabled,tx=tx,fsk=fsk,registers=baseline)
    assert rc==128,(rc,lines)
    assert lines[-1]=="fsk boundary: two frames sent; RX restored"
    assert len(fsk.enabled)==2
    assert len(fsk.fifos)==2
    expected,_=make_probe_vector()
    assert tuple(fsk.fifos[0])==tuple([*expected[:56],*[0]*8])
    assert tuple(fsk.fifos[1])==tuple([*[0]*8,*[0xffff]*8,
                                      *[~x&65535 for x in expected[16:]]])
    assert [k[0] for k in tx["log"]].count("tx_on")==1
    assert [k[0] for k in tx["log"]].count("tx_off")==1
    assert [k[0] for k in tx["log"]].count("tx_pa")==3
    assert tx["log"][-1][0]=="tx_off"
    assert fsk.enabled[1]>fsk.enabled[0]
    assert all(reg not in (0x30,0x33,0x36,0x37,0x40)
               for reg,_ in w)
    for reg in TX_REGS:
        assert after.get(reg,0)==baseline.get(reg,0),reg


@need_clang
def test_second_frame_timeout_releases_and_restores(enabled):
    tx={}
    fsk=TwoFrameFskModel(completed_frames=1)
    baseline=saved_regs()
    rc,lines,after,w=run_m0(enabled,tx=tx,fsk=fsk,registers=baseline)
    assert rc==-14,(rc,lines)
    assert len(fsk.enabled)==2
    assert tx["log"][-1][0]=="tx_off"
    for reg in TX_REGS:
        assert after.get(reg,0)==baseline.get(reg,0)


@need_clang
@pytest.mark.parametrize("kwargs,expected",[
    ({"rx_hz":144390000},-2),
    ({"registers":{**saved_regs(),0:0x4819}},-6),
    ({"registers":{**saved_regs(),0x67:400}},-7),
    ({"registers":{**saved_regs(),0x3f:0x8000}},-8),
])
def test_refusal_before_tx(enabled,kwargs,expected):
    tx={}
    rc,lines,after,w=run_m0(enabled,tx=tx,**kwargs)
    assert rc==expected
    assert not tx.get("log")
    assert not w


@need_clang
def test_key_abort_during_first_frame_restores(enabled):
    tx={}
    baseline=saved_regs()
    fsk=TwoFrameFskModel()
    rc,lines,after,w=run_m0(
        enabled,tx=tx,fsk=fsk,registers=baseline,
        keys=((130000,1400000),)
    )
    assert rc==-4,(rc,lines)
    assert tx["log"][-1][0]=="tx_off"
    for reg in TX_REGS:
        assert after.get(reg,0)==baseline.get(reg,0)


def test_source_explicit_boundary_caveat():
    s=SOURCE.read_text()
    assert "this experiment measures THAT break" in s.lower() or "this experiment measures THAT break" in s
    assert "FSK_BOUNDARY_ENABLE 0" in s
    assert "FSK_MAX_TX_MS 1900u" in s
    for x in ("fl_prog(","fl_erase(","cal_write("):
        assert x not in s
