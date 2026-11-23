"""Cortex-M0 TX-15W gain ladder: exact serial writes and safety checks."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from modem import k6mod
from test_k6_i2s import run,START,need_clang

SOURCE=ROOT/"c/k6/mod/tx15w_gain_ladder.c"
GAINS=(24,8,24,16,24,32,24,48,24,64,24,80,24)
WORDS=tuple(0x8000 | (x<<8) for x in GAINS)

def wrap(tmp_path,*,rf=False,fixture=True,watchdog=True,hz=433500000):
    p=tmp_path/f"tx15w-{int(rf)}.c"
    p.write_text(
        "#define TX15W_BENCH_ENABLE 1\n"
        f"#define TX15W_RF_ENABLE {int(rf)}\n"
        f"#define TX15W_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX15W_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX15W_HZ {hz}u\n"
        f'#include "{SOURCE}"\n')
    return p

@need_clang
def test_inert_default():
    code,entry=k6mod.build(str(SOURCE))
    assert entry==0 and 0<len(code)<4096

@need_clang
@pytest.mark.parametrize("rf",[False,True])
def test_exact_gains_and_source_restoration(tmp_path,rf):
    src=wrap(tmp_path,rf=rf)
    code,entry=k6mod.build(str(src))
    assert entry==0 and len(code)<4096
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x70:0,0x71:0},src=str(src),
        level=3,rx_hz=433500000,tx=tx,count=180_000_000)
    assert ret==13,said
    assert said[-1].startswith("tx15w n=13 tone=11264 pa=")
    assert [v for reg,v,t in tx["w"] if reg==0x70]==[
        0,*WORDS,0,0
    ]
    assert [v for reg,v,t in tx["w"] if reg==0x71]==[11264,0]
    assert not any(reg in (0x30,0x36,0x38,0x39) for reg,v,t in tx["w"])
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==(
        [0,1,0] if rf else [0,0])
    assert tx["log"][0][:2]==("tx_on",1800)
    assert tx["log"][-1][0]=="tx_off"
    assert after[0x70]==after[0x71]==0

@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_reject_unguarded_rf(tmp_path,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,rf=True,
                  fixture=bool(fixture),watchdog=bool(watchdog))))

@need_clang
def test_reject_wrong_frequency(tmp_path):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,hz=144390000)))

@need_clang
def test_reject_wrong_chip_and_rx(tmp_path):
    tx={};src=wrap(tmp_path,rf=True)
    for regs,hz,expected in (
        ({**START,0:0x4819},433500000,-3),
        ({**START,0:0x4829},144390000,-2),
    ):
        ret,said,after,writes=run(regs,src=str(src),
                level=3,rx_hz=hz,tx=tx,count=70_000_000)
        assert ret==expected
    assert "log" not in tx

def test_timing_within_lease():
    assert 50000+80000+13*85000+100000<1800000
    assert GAINS[::2]==(24,)*7
    assert sorted(GAINS[1::2])==[8,16,32,48,64,80]
