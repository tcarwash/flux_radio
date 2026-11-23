"""TX-15Y B-chip DTMF work-mode dual-tone source probe: OFFLINE emulator.

These tests only build local Cortex-M0 code and simulate register writes.
Passing them does NOT establish on-air tone2, dual-tone FM, RF phase
coherence, or hardware safety qualification. The shared K6 hardware
belongs to the queue owner; no live serial or SDR action is included.
"""
from pathlib import Path
import sys
import hashlib

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from modem import k6mod
from test_k6_i2s import START,run,need_clang

SOURCE=ROOT/"c/k6/mod/tx15y_dual_tone_probe.c"
F1=8074
F2=14702
T1=0x9800
T2=0x0098
BOTH=0x9898
LEVEL=3
HZ=433500000
DURATIONS=(120000,120000,120000,120000,80000,
           120000,120000,120000,90000)
STATES=(T1,T1,T2,BOTH,0,T2,T1,BOTH,0)


def wrap(tmp_path,*,bench=True,rf=False,fixture=True,
         watchdog=True,dtmf_setup=0,hz=HZ):
    p=tmp_path/f"tx15y-b{int(bench)}-rf{int(rf)}-dtmf{dtmf_setup}.c"
    p.write_text(
        f"#define TX15Y_BENCH_ENABLE {int(bench)}\n"
        f"#define TX15Y_RF_ENABLE {int(rf)}\n"
        f"#define TX15Y_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX15Y_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX15Y_DTMF_DETECT_SETUP {dtmf_setup}\n"
        f"#define TX15Y_HZ {hz}u\n"
        f'#include "{SOURCE}"\n')
    return p


def image(src):
    code,entry=k6mod.build(str(src))
    assert entry==0 and 0<len(code)<4096
    return code,entry


@need_clang
def test_default_module_compiles_but_has_no_bench_code():
    code,entry=image(SOURCE)
    assert len(code)<192
    assert "TX15Y_BENCH_ENABLE" in SOURCE.read_text()


@need_clang
def test_default_is_inert_even_with_valid_chip():
    tx={}
    ret,said,regs,writes=run(
        {**START,0:0x4829,0x3f:0,0x70:0},
        level=3,src=str(SOURCE),rx_hz=HZ,tx=tx)
    assert ret==-9 and said[-1].startswith("tx15y inert")
    assert tx.get("w",[])==[] and tx.get("log",[])==[]


@need_clang
@pytest.mark.parametrize("rf,setup",[
    (False,0),(False,1),(True,0),(True,1)
])
def test_exact_dualtone_state_schedule_and_full_restore(tmp_path,rf,setup):
    p=wrap(tmp_path,rf=rf,dtmf_setup=setup)
    code,_=image(p)
    before={
        **START,0:0x4829,0x3f:0,0x24:0x1205,
        0x70:0x1005,0x71:0x2222,0x72:0x3065,
    }
    tx={}
    ret,said,after,writes=run(
        before,src=str(p),level=3,rx_hz=HZ,tx=tx,
        count=120_000_000,write_us=130)
    assert ret==9, (ret,said)
    assert said[-1]==f"tx15y n=9 dtmf_setup={setup} pa={int(rf)}"
    assert tx["log"][0][:2]==("tx_on",1800)
    assert tx["log"][-1][0]=="tx_off"
    assert [x[1] for x in tx["log"] if x[0]=="tx_pa"]==(
        [0,1,0] if rf else [0,0])
    assert {r:after[r] for r in (0x3f,0x24,0x70,0x71,0x72)}=={
        r:before[r] for r in (0x3f,0x24,0x70,0x71,0x72)}
    # Only REG_3F/24/70/71/72 are modified, never FSK FIFO
    modified={reg for reg,val,time in tx["w"]}
    assert modified <= ({0x3f,0x70,0x71,0x72}|
                        ({0x24} if setup else set()))
    assert 0x59 not in modified and 0x5f not in modified
    regs=[(r,v) for r,v,t in tx["w"]]
    assert (0x71,F1) in regs and (0x72,F2) in regs
    assert regs.count((0x3f,0x0800))==1
    i_mode=regs.index((0x3f,0x0800))
    i_tone2=regs.index((0x72,F2))
    assert i_tone2>i_mode, "REG72 must not change while still in FSK mode"
    assert all(i<i_mode for i,(r,v) in enumerate(regs)
               if r==0x70 and v==T1 and i==regs.index((0x70,T1)))
    if setup:
        assert (0x24,0xc17f) in regs
        assert regs.index((0x24,0xc17f))<i_mode
    else:
        assert not any(r==0x24 for r,v in regs)
    gain_sequence=[v for reg,v,t in tx["w"] if reg==0x70]
    assert gain_sequence==[0,T1,0,*STATES[1:],0,before[0x70]]
    mode_sequence=[v for reg,v,t in tx["w"] if reg==0x3f]
    assert mode_sequence==[0x0800,0]
    assert sum(DURATIONS)+70000 <1800000
    assert all(0<=t<1_800_000 for r,v,t in tx["w"])


@need_clang
@pytest.mark.parametrize("setup",[0,1])
def test_unexpected_nonzero_modem_mode_refuses_mutation(tmp_path,setup):
    p=wrap(tmp_path,rf=True,dtmf_setup=setup)
    tx={}
    before={**START,0:0x4829,0x3f:0x2000,
            0x24:0x1205,0x70:0x1005,0x71:0x2222,0x72:0xc195}
    ret,said,after,writes=run(
        before,src=str(p),level=3,rx_hz=HZ,tx=tx)
    assert ret==-17,(ret,said)
    assert all(after[k]==before[k] for k in (0x3f,0x24,0x70,0x71,0x72))
    assert not tx.get("w"),"conflicting mode must perform no RFIC writes"
    assert tx["log"][-1][0]=="tx_off"


@need_clang
@pytest.mark.parametrize("setup",[0,1])
def test_key_abort_restores_snapshot(tmp_path,setup):
    p=wrap(tmp_path,rf=True,dtmf_setup=setup)
    tx={}
    before={**START,0:0x4829,0x3f:0,
            0x24:0x1205,0x70:0x1005,0x71:0x2222,0x72:0x3065}
    # key-down during one of the later 120-ms states
    ret,said,after,writes=run(before,src=str(p),
        level=3,rx_hz=HZ,tx=tx,keys=((350000,1150000),))
    assert ret==-4,ret
    assert tx["log"][-1][0]=="tx_off"
    assert all(after[k]==before[k] for k in (0x3f,0x24,0x70,0x71,0x72))


@need_clang
@pytest.mark.parametrize("setup",[0,1])
def test_chip_and_frequency_guards_before_tx(tmp_path,setup):
    p=wrap(tmp_path,rf=True,dtmf_setup=setup)
    for chip,hz in ((0x4819,HZ),(0x4829,144390000)):
        tx={}
        ret,said,after,writes=run(
            {**START,0:chip},src=str(p),level=3,rx_hz=hz,tx=tx)
        assert ret in (-3,-2),ret
        assert not tx.get("w") and not tx.get("log")


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_rf_requires_both_independent_acknowledgments(tmp_path,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,rf=True,
            fixture=bool(fixture),watchdog=bool(watchdog))))


@need_clang
@pytest.mark.parametrize("setup",[-1,2,3])
def test_invalid_detector_setup_rejected_at_compile(tmp_path,setup):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,dtmf_setup=setup)))


@need_clang
def test_wrong_rf_frequency_rejected_at_compile(tmp_path):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,hz=440850000)))


def test_module_source_cannot_operate_radio_without_bench_enable():
    src=SOURCE.read_text()
    assert "#define TX15Y_BENCH_ENABLE 0" in src
    assert '#if !TX15Y_BENCH_ENABLE' in src
    assert 'api->say("tx15y inert; offline test build")' in src
    assert "TX15Y_FIXTURE_ACK" in src
    assert "TX15Y_WATCHDOG_ACK" in src
