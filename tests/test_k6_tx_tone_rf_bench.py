"""Guarded 150-symbol/s RF-on tone oscillator bench, Unicorn only."""
from pathlib import Path
import sys
import pytest
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from modem import k6mod
from test_k6_i2s import run,k6_clock,START,need_clang
from test_k6_tx8probe import _vector

SOURCE=ROOT/"c/k6/mod/tx8probe.c"


@pytest.fixture
def rf_wrapper(tmp_path):
    p=tmp_path/"tx8rf.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_RF_ENABLE 1\n"
        "#define TX8_RF_FIXTURE_ACK 1\n"
        "#define TX8_RF_WATCHDOG_ACK 1\n"
        "#define TX8_HZ 433500000u\n"
        "#define TX8_GAIN 24u\n"
        "#define TX8_SYMBOL_RATE 150u\n"
        f'#include "{SOURCE}"\n'
    )
    return p


@need_clang
def test_150baud_64_tone_changes_one_rf_keyed_burst(rf_wrapper):
    code,entry=k6mod.build(str(rf_wrapper))
    assert entry==0 and len(code)<4096
    mmio,rcc,timers=k6_clock()
    tx={}
    regs={**START,0:0x4829,0x70:0x0000,0x71:0x0000}
    rc,said,after,w=run(
        regs,src=str(rf_wrapper),level=3,rx_hz=433500000,
        tx=tx,mmio=mmio,write_us=180,count=130_000_000)
    assert rc==64,(rc,said)
    assert said[-1].startswith("tx8 n=64 ")
    assert "late=0" in said[-1]
    assert tx["log"][0][:2]==("tx_on",1800)
    assert [e[0] for e in tx["log"]]==[
        "tx_on","tx_pa","tx_pa","tx_pa","tx_off"]
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==[0,1,0]
    words,symbols=_vector()
    tx_tones=[(v,us) for reg,v,us in tx["w"] if reg==0x71 and v]
    assert len(tx_tones)==64
    assert [v for v,_ in tx_tones]==[words[s] for s in symbols]
    elapsed=np.diff([t for v,t in tx_tones])
    assert abs(np.mean(elapsed)-1e6/150)<15
    assert elapsed[1:].min()>6500 and elapsed[1:].max()<6850
    assert elapsed[0]<7000  # initial PA-on/clock-start overhead
    assert rcc[0x18]==0 and not timers.get(0,0)&1
    assert after[0x70]==0 and after[0x71]==0


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_rf_requires_both_compile_flags(tmp_path,fixture,watchdog):
    p=tmp_path/"unsafe.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_RF_ENABLE 1\n"
        f"#define TX8_RF_FIXTURE_ACK {fixture}\n"
        f"#define TX8_RF_WATCHDOG_ACK {watchdog}\n"
        "#define TX8_HZ 433500000u\n"
        f'#include "{SOURCE}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_rf_wrong_frequency_compile_refused(tmp_path):
    p=tmp_path/"bad_freq.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_RF_ENABLE 1\n"
        "#define TX8_RF_FIXTURE_ACK 1\n"
        "#define TX8_RF_WATCHDOG_ACK 1\n"
        "#define TX8_HZ 146560000u\n"
        f'#include "{SOURCE}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_rf_state_guard_refuses_without_tx(rf_wrapper):
    mmio,rcc,timers=k6_clock()
    tx={}
    regs={**START,0:0x4829,0x67:400}
    ret,said,after,w=run(
        regs,src=str(rf_wrapper),level=3,rx_hz=433500000,
        tx=tx,mmio=mmio,write_us=180,count=130_000_000)
    assert ret==-7
    assert not tx.get("log") and not tx.get("w")


@need_clang
def test_rf_module_abort_unkeys_and_restores_timer(rf_wrapper):
    mmio,rcc,timers=k6_clock()
    tx={}
    ret,said,after,w=run(
        {**START,0:0x4829,0x70:0x0000,0x71:0x0000},
        src=str(rf_wrapper),level=3,rx_hz=433500000,
        tx=tx,mmio=mmio,write_us=180,count=130_000_000,
        keys=((200000,1300000),))
    assert ret==-4,(ret,said)
    assert tx["log"][-1][0]=="tx_off"
    assert not timers.get(0,0)&1
    assert after[0x70]==0 and after[0x71]==0
