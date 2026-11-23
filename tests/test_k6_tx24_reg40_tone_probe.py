"""TX-24 FD6818B REG40 deviation scan: compile + Cortex-M0 emulator.

Only exercises local source and Unicorn's mocked RFIC; no radio/SDR.
REG40 modulation physics, symbol update bandwidth, and FSK coexistence
remain UNPROVEN until independently qualified, lease-approved RF.
"""
from pathlib import Path
import sys
import hashlib

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from modem import k6mod
from test_k6_i2s import START,run,need_clang

SOURCE=ROOT/"c/k6/mod/tx24_reg40_tone_probe.c"
ORIGINAL40=0x3516
GAIN24=0x9800
TONE_WORD=11264
HZ=433500000
LOW8=(0x16,0x40,0x16,0x80,0x16,0x00,0x16,
      0x40,0x16,0x80,0x16,0x00,0x16)

def wrap(tmp_path,*,bench=1,rf=0,fixture=1,watchdog=1,hz=HZ):
    p=tmp_path/f"tx24-reg40-{bench}-{rf}-{fixture}-{watchdog}-{hz}.c"
    p.write_text(
        f"#define TX24_BENCH_ENABLE {bench}\n"
        f"#define TX24_RF_ENABLE {rf}\n"
        f"#define TX24_FIXTURE_ACK {fixture}\n"
        f"#define TX24_WATCHDOG_ACK {watchdog}\n"
        f"#define TX24_FREQ_HZ {hz}u\n"
        f'#include "{SOURCE}"\n')
    return p

def build(src):
    code,entry=k6mod.build(str(src))
    assert entry==0 and 0<len(code)<4096
    return code,entry

def before(**override):
    d={**START,0:0x4829,0x30:0,0x3f:0,
       0x40:ORIGINAL40,0x70:0x1005,0x71:0x3333,
       0x59:0x00f8,0x72:0xc195,0x5f:0x9876}
    return {**d,**override}

@need_clang
def test_default_binary_is_inert(tmp_path):
    code,entry=build(SOURCE)
    assert len(code)<200
    tx={}
    ret,msg,after,writes=run(before(),src=str(SOURCE),
                             level=3,rx_hz=HZ,tx=tx)
    assert ret==-9
    assert msg[-1].startswith("tx24 inert")
    assert not tx.get("log") and not tx.get("w")

@need_clang
@pytest.mark.parametrize("rf",[0,1])
def test_sweep_and_full_register_snapshot_restore(tmp_path,rf):
    src=wrap(tmp_path,rf=rf)
    code,entry=build(src)
    tx={}
    regs=before()
    ret,msg,after,writes=run(
        regs,src=str(src),level=3,rx_hz=HZ,tx=tx,
        write_us=130,count=120_000_000)
    assert ret==13,(ret,msg)
    assert msg[-1]==f"tx24 n=13 pa={rf}"
    assert tx["log"][0][:2]==("tx_on",1800)
    assert tx["log"][-1][0]=="tx_off"
    assert [x[1] for x in tx["log"] if x[0]=="tx_pa"]==(
        [0,1,0] if rf else [0,0])
    assert all(after[r]==regs[r] for r in (0x40,0x70,0x71,0x3f,0x59,0x72,0x5f))
    changes=[(r,v) for r,v,t in tx["w"]]
    assert {r for r,v in changes} <= {0x40,0x70,0x71}
    writes40=[v for r,v in changes if r==0x40]
    assert writes40 == [0x3500|v for v in LOW8]+[ORIGINAL40]
    assert all((v&0xff00)==0x3500 for v in writes40)
    assert [v for r,v in changes if r==0x71]==[TONE_WORD,regs[0x71]]
    assert [v for r,v in changes if r==0x70]==[
        0,GAIN24,0,0,regs[0x70]]
    assert 13*90000+80000+85000<1800000
    assert max(t for r,v,t in tx["w"])<1_800_000

@need_clang
def test_unexpected_reg40_refuses_with_no_modulation_writes(tmp_path):
    src=wrap(tmp_path,rf=1)
    tx={}
    regs=before()
    regs[0x40]=0x3416
    ret,msg,after,writes=run(regs,src=str(src),
        level=3,rx_hz=HZ,tx=tx)
    assert ret==-17,(ret,msg)
    assert not tx.get("w")
    assert tx["log"][-1][0]=="tx_off"
    assert all(after[r]==regs[r] for r in (0x40,0x70,0x71))

@need_clang
def test_key_abort_restores_all_originals(tmp_path):
    src=wrap(tmp_path,rf=1)
    tx={}
    regs=before()
    ret,msg,after,writes=run(
        regs,src=str(src),level=3,rx_hz=HZ,tx=tx,
        keys=((345000,1000000),),count=120_000_000)
    assert ret==-4,(ret,msg)
    assert tx["log"][-1][0]=="tx_off"
    assert all(after[r]==regs[r] for r in (0x40,0x70,0x71,0x3f,0x59,0x72))
    assert any(r==0x40 and v!=ORIGINAL40 for r,v,t in tx["w"])
    assert tx["w"][-2][0]==0x71
    assert tx["w"][-1][0]==0x70

@need_clang
@pytest.mark.parametrize("chip,hz,expected",[
    (0x4819,HZ,-3),(0x4829,144390000,-2)
])
def test_identity_frequency_guards_prevent_tx(tmp_path,chip,hz,expected):
    src=wrap(tmp_path,rf=1)
    tx={}
    regs=before()
    regs[0]=chip
    ret,msg,after,writes=run(
        regs,src=str(src),level=3,rx_hz=hz,tx=tx)
    assert ret==expected
    assert not tx.get("log") and not tx.get("w")

@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_rf_binary_denied_without_both_acknowledgments(tmp_path,fixture,watchdog):
    with pytest.raises(Exception):
        build(wrap(tmp_path,rf=1,fixture=fixture,watchdog=watchdog))

@need_clang
def test_compile_time_rejects_unplanned_center_frequency(tmp_path):
    with pytest.raises(Exception):
        build(wrap(tmp_path,hz=440850000))

@need_clang
def test_test_source_never_touches_fsk_modem_or_pa_target():
    src=SOURCE.read_text()
    assert "TX24_BENCH_ENABLE 0" in src
    assert "#if !TX24_BENCH_ENABLE" in src
    assert "0x59u" not in src and "0x5fu" not in src
    assert "0x36u" not in src
    assert "REG40 low byte" in src or "REG40" in src
