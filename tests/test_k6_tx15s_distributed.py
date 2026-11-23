"""TX-15S: fixed distributed pilot, strict heldout and hardware-safe M0 simulation."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
sys.path.insert(0,str(ROOT/"tools"))

from modem import k6mod
from test_k6_i2s import run,k6_clock,START,need_clang
from k6_tx15s_rf_analyze import (
    N,PILOT_WINDOWS,PILOT_INDICES,HOLDOUT_INDICES,GUARD_START,BAUD,
    region,edge_region,read_vector,schedule_from_pilot,find_edges,
    HEADER
)
SOURCE=ROOT/"c/k6/mod/tx8probe.c"


def wrap(tmp_path,*,rf=False,fixture=True,watchdog=True,baud=150):
    p=tmp_path/"tx15s-code.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_PHASE_VECTOR_V4 1\n"
        f"#define TX8_SYMBOL_RATE {baud}u\n"
        f"#define TX8_RF_ENABLE {int(rf)}\n"
        f"#define TX8_RF_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX8_RF_WATCHDOG_ACK {int(watchdog)}\n"
        "#define TX8_HZ 433500000u\n"
        "#define TX8_GAIN 24u\n"
        f'#include "{SOURCE}"\n'
    )
    return p


def test_source_predeclared_pilot_holdout_and_guard():
    words,ids=read_vector()
    assert len(ids)==N==192 and len(words)==8
    assert len(PILOT_INDICES)==72 and len(HOLDOUT_INDICES)==114
    assert GUARD_START==186
    assert len(set(PILOT_INDICES)&set(HOLDOUT_INDICES))==0
    assert len(set(PILOT_INDICES)&set(range(GUARD_START,N)))==0
    assert all(region(i)=="guard" for i in range(186,192))
    assert all(region(i)=="pilot" for i in PILOT_INDICES)
    assert all(region(i)=="heldout" for i in HOLDOUT_INDICES)
    assert all(ids[i]==0 for i in range(186,192))
    for begin,end in PILOT_WINDOWS:
        assert all(edge_region(i)=="pilot" for i in range(begin+1,end))
        assert edge_region(begin)=="boundary" if begin else True
    assert {int(ids[i]) for i in HOLDOUT_INDICES}==set(range(8))
    assert N/BAUD+.100+.050<1.8
    # Same 5 strong frequency transitions per window, 20 pilots total.
    assert sum(
        edge_region(i)=="pilot" and
        abs((words[ids[i]]-words[ids[i-1]])/10.32444)>450
        for i in range(1,N))==20


def fake_edges(*,quadratic=0.00045,seed=81):
    words,ids=read_vector()
    rng=np.random.default_rng(seed)
    known=[]
    for i in range(1,N):
        if abs((words[ids[i]]-words[ids[i-1]])/10.32444)<450:continue
        pair_shift=(int(ids[i])+int(ids[i-1]))*1.1e-6
        obs=.894+i*(1/149.67)+quadratic*(i/N)**2+pair_shift+rng.normal(0,3.5e-6)
        known.append({
            "index":i,"region":edge_region(i),"found":True,
            "before":int(ids[i-1]),"after":int(ids[i]),
            "midpoint_time_s":float(obs)
        })
    return known


def test_piecewise_clock_is_pilot_frozen_and_improves_nonlinear_drift():
    e=fake_edges()
    model,lin,piece=schedule_from_pilot(e)
    assert model["pilot_only_fit"]
    assert model["pilot_edges"]==20
    assert model["heldout_strong_edges"]>=24
    assert len(model["pilot_knots"])==4
    assert len(piece)==N and np.all(np.diff(piece)>0)
    assert model["heldout_piecewise_linear"]["rms_us"] < model[
        "heldout_global_linear"]["rms_us"]*.8


def test_no_holdout_leakage_in_clock_estimate():
    e=fake_edges()
    model,lin,piece=schedule_from_pilot(e)
    changed=[dict(x) for x in e]
    for x in changed:
        if x["region"]=="heldout":x["midpoint_time_s"]+=.0017
    model2,lin2,piece2=schedule_from_pilot(changed)
    np.testing.assert_array_equal(lin,lin2)
    np.testing.assert_array_equal(piece,piece2)
    assert model["pilot_knots"]==model2["pilot_knots"]
    assert model2["heldout_piecewise_linear"]["rms_us"]>model[
        "heldout_piecewise_linear"]["rms_us"]+500


def test_no_non_pilot_edge_is_used_for_clock_training():
    e=fake_edges()
    for x in e:
        if x["region"]=="pilot":x["found"]=False
    with pytest.raises(ValueError,match="pilot"):
        schedule_from_pilot(e)


def test_guard_and_boundary_excluded_from_scoring():
    assert sum(edge_region(i)=="pilot" for i in range(1,N))==68
    assert sum(edge_region(i)=="heldout" for i in range(1,N))==110
    assert sum(edge_region(i)=="guard" for i in range(1,N))==5
    assert sum(edge_region(i)=="boundary" for i in range(1,N))==8
    assert sum(region(i)=="heldout" for i in range(N))==114


@need_clang
@pytest.mark.parametrize("rf",[False,True])
def test_192_events_emulator_pa_and_timer_restore(tmp_path,rf):
    p=wrap(tmp_path,rf=rf)
    code,entry=k6mod.build(str(p))
    assert len(code)<4096 and entry==0
    mmio,rcc,timers=k6_clock()
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x70:0,0x71:0},src=str(p),
        level=3,rx_hz=433500000,tx=tx,mmio=mmio,write_us=180,
        count=240_000_000
    )
    assert ret==N,said
    assert said[-1].startswith("tx8s n=192 ")
    assert "late=0" in said[-1]
    words,ids=read_vector()
    assert [v for reg,v,t in tx["w"] if reg==0x71 and v]==[int(words[k]) for k in ids]
    assert tx["log"][0][:2]==("tx_on",1800 if rf else 4000)
    assert tx["log"][-1][0]=="tx_off"
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==(
        [0,1,0] if rf else [0]*(N+1))
    assert after[0x70]==after[0x71]==0
    assert not timers.get(0,0)&1
    assert rcc[0x18]==0


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(False,False),(False,True),(True,False)])
def test_tx_gates_mandatory(tmp_path,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,rf=True,fixture=fixture,watchdog=watchdog)))


@need_clang
def test_wrong_baud_or_other_vector_rejected(tmp_path):
    with pytest.raises(Exception):
        k6mod.build(str(wrap(tmp_path,baud=300)))
    p=wrap(tmp_path)
    p.write_text("#define TX8_PHASE_VECTOR_V3 1\n"+p.read_text())
    with pytest.raises(Exception):
        k6mod.build(str(p))


def test_source_is_read_only_and_no_radio_commands():
    t=(ROOT/"tools/k6_tx15s_rf_analyze.py").read_text()
    for unsafe in ("open_port(","run_module(","mutate_library(","RtlCapture(","tx_on("):
        assert unsafe not in t
    assert "all_timebase_and_phase_fits_train_on_pilot_only" in t
    assert "audio_phase_not_direct_rf_carrier_phase" in t
