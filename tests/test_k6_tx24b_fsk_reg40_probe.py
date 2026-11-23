"""TX24B per-FSK-frame REG40 compatibility: 0 hardware, Cortex-M0 emulation."""
from pathlib import Path
import sys
import hashlib

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from modem import k6mod
from test_k6_i2s import need_clang
from test_k6_fsklab import run_m0,saved_regs

SOURCE=ROOT/"c/k6/mod/tx24b_fsk_reg40_probe.c"
LEVELS=(0x3516,0x3540,0x3516,0x3580,0x3516,0x3500,0x3540,0x3580)
GUARD=0xa55a
KNOWN=tuple(0xffff if ((k//4)&1) else 0 for k in range(32))+(GUARD,)
SNAPSHOT=(0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d,0x40)


def wrap(tmp_path,rf=0,fixture=1,watchdog=1):
    p=tmp_path/f"tx24b-rf{rf}-f{fixture}-w{watchdog}.c"
    p.write_text(
        "#define TX24B_BENCH_ENABLE 1\n"
        f"#define TX24B_RF_ENABLE {rf}\n"
        f"#define TX24B_FIXTURE_ACK {fixture}\n"
        f"#define TX24B_WATCHDOG_ACK {watchdog}\n"
        f'#include "{SOURCE}"\n')
    return p


class Model:
    """Finite-FSK FIFO/status only: NOT a real FSK/REG40 RF transfer model."""
    def __init__(self,complete_frames=8,latency_us=149000):
        self.complete_frames=complete_frames
        self.latency_us=latency_us
        self.enabled=[]
        self.fifos=[]
        self.current=[]
        self.started=None
        self.events=[]
        self.deviation=None
        self.ranges=[]
    def key(self,name,us):
        self.events.append((name,None,us))
        return 0
    def write(self,reg,value,us):
        self.events.append((reg,value,us))
        if reg==0x40:self.deviation=value
        if reg==0x59 and value==0x80f8:
            self.current=[]
            self.started=None
        if reg==0x5f:
            self.current.append(value)
        if reg==0x59 and value==0x08f8:
            self.started=us
            self.fifos.append(tuple(self.current))
            self.enabled.append(us)
            self.ranges.append(self.deviation)
    def read(self,reg,us):
        if reg==0x0c:
            return int(self.started is not None and
                len(self.enabled)<=self.complete_frames and
                us-self.started>=self.latency_us)
        return None


@need_clang
def test_inert_build_never_keys_or_writes():
    code,entry=k6mod.build(str(SOURCE))
    assert len(code)<200 and entry==0
    tx={}
    ret,msg,regs,writes=run_m0(SOURCE,tx=tx)
    assert ret==-9 and msg[-1].startswith("tx24b inert")
    assert not tx.get("log") and not tx.get("w")


@need_clang
@pytest.mark.parametrize("rf",[0,1])
def test_eight_33word_fsk_frames_and_deviation_states_restore(tmp_path,rf):
    src=wrap(tmp_path,rf)
    code,entry=k6mod.build(str(src))
    assert 0<len(code)<4096 and entry==0
    m=Model()
    before=saved_regs()
    before[0x40]=0x3516
    tx={}
    ret,msg,after,w=run_m0(src,tx=tx,fsk=m,registers=before)
    assert ret==8*33,(ret,msg)
    assert len(m.fifos)==8
    assert all(x==KNOWN for x in m.fifos)
    assert tuple(m.ranges)==LEVELS
    assert len(m.enabled)==8
    assert [k[0] for k in tx["log"]].count("tx_on")==1
    assert [k[0] for k in tx["log"]].count("tx_off")==1
    assert [k[1] for k in tx["log"] if k[0]=="tx_pa"]==(
        [0,1,0] if rf else [0,0])
    assert tx["log"][0][1]==1900
    assert tx["log"][-1][0]=="tx_off"
    assert tuple(after[r] for r in SNAPSHOT)==tuple(before[r] for r in SNAPSHOT)
    assert {reg for reg,value in w}<={*SNAPSHOT,0x02,0x5f}
    assert [val for reg,val in w if reg==0x70]==[0x0095,before[0x70]]
    assert [val for reg,val in w if reg==0x72]==[0xc194,before[0x72]]
    assert [val for reg,val in w if reg==0x40]==list(LEVELS)+[0x3516]
    assert len([1 for r,v in w if r==0x5f])==8*33
    assert all(k[1]!=0x5f for k in m.events if isinstance(k[1],int))
    # No REG40 low-byte adjustment between 08F8 TX-enable and
    # corresponding next 00F8 TX-stop frame boundary.
    transmit=False
    for e in m.events:
        if e[0]!=0x59 and e[0]==0x40:
            assert not transmit
        if e[0]==0x59 and e[1]==0x08f8:transmit=True
        if e[0]==0x59 and e[1]==0x00f8:transmit=False


@need_clang
def test_fsk_timeout_does_not_key_or_leave_foreign_regs(tmp_path):
    src=wrap(tmp_path,rf=1)
    m=Model(complete_frames=3)
    before=saved_regs()
    tx={}
    ret,msg,after,w=run_m0(src,tx=tx,fsk=m,registers=before)
    assert ret==-15,(ret,msg)
    assert len(m.fifos)==4
    assert tx["log"][-1][0]=="tx_off"
    assert all(after[k]==before[k] for k in SNAPSHOT)


@need_clang
@pytest.mark.parametrize("broken_reg,expected",[
    ({"rx_hz":144390000},-2),
    ({"0x40":0x3510},-8),
    ({"0x3f":0x8000},-8),
    ({"0x00":0x4819},-3),
])
def test_unexpected_radio_state_is_refused(tmp_path,broken_reg,expected):
    src=wrap(tmp_path,rf=1)
    before=saved_regs()
    rx=broken_reg.get("rx_hz",433500000)
    for k,v in broken_reg.items():
        if k.startswith("0x"):before[int(k,16)]=v
    tx={}
    ret,msg,after,w=run_m0(src,tx=tx,fsk=Model(),registers=before,rx_hz=rx)
    assert ret==expected,(ret,msg)
    assert not any(r==0x5f for r,v in w)


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(1,0),(0,1)])
def test_armed_rf_compilation_requires_both_guards(tmp_path,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,rf=1,fixture=fixture,watchdog=watchdog)))


def test_source_is_finite_bounded_and_separate_from_tx26():
    source=SOURCE.read_text()
    assert "TX24B_BENCH_ENABLE 0" in source
    assert "TX24B_MAX_TX_MS 1900u" in source
    assert "TX24B_FRAME_COUNT 8u" in source
    assert "TX24B_WORDS 33u" in source
    assert "REG40 DURING a frame" in source
    assert "0xa55au" in source
    assert "fd_read(TX24B_R_FIFO)" not in source
    assert "cal_write(" not in source and "fl_prog(" not in source
    assert "REG70 or REG72 after source FSK initialization" in source
