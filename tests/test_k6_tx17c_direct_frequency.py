"""TX-17C independent direct synthesizer low-word latch probe.

All tests use Cortex M0 emulator; no actual RF / radio. The module is inert
without explicit bench ACKs and restores exact prior synthesizer words.
"""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from modem import k6mod
from test_k6_i2s import run,k6_clock,START,need_clang

SOURCE=ROOT/"c/k6/mod/tx17c_direct_frequency_latch.c"
LO=0x77f0
HI=0x0295
STEP_LO=[LO,LO+50,LO,LO-50,LO,LO+100,LO,LO]

def wrapper(tmp_path,*,rf=False,fixture=True,watchdog=True,hz=433500000):
    p=tmp_path/f"tx17c-{int(rf)}.c"
    p.write_text(
        "#define TX17_BENCH_ENABLE 1\n"
        f"#define TX17_RF_ENABLE {int(rf)}\n"
        f"#define TX17_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX17_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX17_HZ {hz}u\n"
        f'#include "{SOURCE}"\n')
    return p


@need_clang
def test_default_compiles_inert_without_rf_or_bench():
    code,entry=k6mod.build(str(SOURCE))
    assert entry==0 and 0<len(code)<4096


@need_clang
@pytest.mark.parametrize("rf",[False,True])
def test_exact_synth_tune_and_restore(tmp_path,rf):
    src=wrapper(tmp_path,rf=rf)
    code,entry=k6mod.build(str(src))
    assert entry==0 and 0<len(code)<4096
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x38:LO,0x39:HI,0x70:0,0x71:0},
        src=str(src),level=3,rx_hz=433500000,tx=tx,
        count=160_000_000)
    assert ret==6,said
    assert said[-1].startswith("tx17c n=6 base=30704 hi=661 pa="),said
    assert [v for reg,v,t in tx["w"] if reg==0x38]==STEP_LO
    assert not any(reg==0x30 for reg,v,t in tx["w"])
    assert not any(reg==0x39 and v!=HI for reg,v,t in tx["w"])
    assert tx["log"][0][:2]==("tx_on",1800)
    assert tx["log"][-1][0]=="tx_off"
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==(
        [0,1,0] if rf else [0,0])
    assert all(after[k]==v for k,v in ((0x38,LO),(0x39,HI),(0x70,0),(0x71,0)))


@need_clang
@pytest.mark.parametrize("rf,fixture,watchdog",[
    (True,False,False),(True,False,True),(True,True,False)
])
def test_rf_requires_all_gates(tmp_path,rf,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrapper(tmp_path,rf=rf,
                                 fixture=fixture,watchdog=watchdog)))


@need_clang
def test_forbidden_frequency_cannot_compile(tmp_path):
    with pytest.raises(Exception):
        k6mod.build(str(wrapper(tmp_path,hz=440850000)))


@need_clang
@pytest.mark.parametrize("lo,hi",[(0,0),(0x77f0,0x0294),(0x9000,0x0295)])
def test_unsafe_or_unexpected_pll_snapshot_rejected_and_restored(tmp_path,lo,hi):
    src=wrapper(tmp_path,rf=True)
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x38:lo,0x39:hi,0x70:0,0x71:0},
        src=str(src),level=3,rx_hz=433500000,tx=tx,
        count=80_000_000)
    assert ret==-10
    assert not any(v==1 for e,v,*rest in tx["log"] if e=="tx_pa")
    assert after[0x38]==lo and after[0x39]==hi
    assert tx["log"][-1][0]=="tx_off"


@need_clang
def test_radio_not_in_lab_frequency_does_not_transmit(tmp_path):
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x38:LO,0x39:HI},
        src=str(wrapper(tmp_path,rf=True)),level=3,rx_hz=144390000,
        tx=tx,count=80_000_000)
    assert ret==-2
    assert "log" not in tx or not any(e[0]=="tx_on" for e in tx["log"])
