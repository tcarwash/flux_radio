"""TX-15V REG70 oscillator gate/phase-memory guarded source tests."""
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from modem import k6mod
from test_k6_i2s import START,run,need_clang

SOURCE=ROOT/"c/k6/mod/tx15v_gate_phase.c"
WORD=11264
ON=0x9800
GAPS_US=(57_000,71_000,83_000,97_000)

def wrap(tmp_path,*,rf=False,fixture=True,watchdog=True,hz=433500000):
    p=tmp_path/f"tx15v-{int(rf)}.c"
    p.write_text(
        "#define TX15V_BENCH_ENABLE 1\n"
        f"#define TX15V_RF_ENABLE {int(rf)}\n"
        f"#define TX15V_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX15V_WATCHDOG_ACK {int(watchdog)}\n"
        f"#define TX15V_HZ {hz}u\n"
        f'#include "{SOURCE}"\n'
    )
    return p

@need_clang
def test_default_source_compiles_inert():
    code,entry=k6mod.build(str(SOURCE))
    assert entry==0 and 0<len(code)<4096

@need_clang
@pytest.mark.parametrize("rf",[False,True])
def test_exact_gating_and_full_module_restore(tmp_path,rf):
    src=wrap(tmp_path,rf=rf)
    code,entry=k6mod.build(str(src))
    assert entry==0 and 0<len(code)<4096
    tx={}
    regs={**START,0:0x4829,0x70:0,0x71:0}
    ret,said,after,writes=run(
        regs,src=str(src),level=3,rx_hz=433500000,
        tx=tx,count=180_000_000
    )
    assert ret==9,said
    assert said[-1].startswith("tx15v n=9 tone=11264 pa="),said
    assert tx["log"][0][:2]==("tx_on",1800)
    assert tx["log"][-1][0]=="tx_off"
    assert [v for reg,v,t in tx["w"] if reg==0x70]==(
        [0,ON]+[v for _ in GAPS_US for v in (0,ON)]+[0,0])
    assert [v for reg,v,t in tx["w"] if reg==0x71]==[WORD,0]
    assert not any(reg in (0x30,0x38,0x39,0x36) for reg,v,t in tx["w"])
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==(
        [0,1,0] if rf else [0,0])
    assert after[0x70]==0 and after[0x71]==0

@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_rf_needs_both_acknowledgments(tmp_path,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,rf=True,fixture=bool(fixture),
                             watchdog=bool(watchdog))))

@need_clang
def test_wrong_rf_center_rejected_by_compiler(tmp_path):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,hz=144390000)))

@need_clang
def test_unexpected_chip_and_initial_rx_rejected(tmp_path):
    src=wrap(tmp_path,rf=True)
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4819},src=str(src),level=3,
        rx_hz=433500000,tx=tx,count=40_000_000)
    assert ret==-3 and "log" not in tx
    ret,said,after,writes=run(
        {**START,0:0x4829},src=str(src),level=3,
        rx_hz=144390000,tx=tx,count=40_000_000)
    assert ret==-2 and "log" not in tx

def test_four_gates_and_recovery_fit_in_watchdog_lease():
    duration=(50_000+80_000+200_000+sum(GAPS_US)
              +4*170_000+100_000)
    assert duration<1_800_000
    assert len(set(GAPS_US))==4
    # Each off duration has distinctly noninteger audio cycles at 1091Hz.
    phases=[(11264/10.32444)*gap/1_000_000%1 for gap in GAPS_US]
    assert len({round(x,2) for x in phases})>=3
