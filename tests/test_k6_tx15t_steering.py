"""TX-15T designed controlled B-chip RF phase pulse, hard-frozen qualifications."""
from pathlib import Path
import sys
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests"),str(ROOT/"tools")]

from modem import k6mod
from test_k6_i2s import run,k6_clock,START,need_clang
from k6_tx15t_rf_steering import (
    CONTROL_HEADER,STEER_HEADER,N,source_variants,analyze_file,
    fit_control_and_counterfactual,evaluate_decisions,DECISION
)
from k6_tx15s_carrier_iq import continuous_audio_quadratures
from k6_tx15s_rf_analyze import FS,TIME_ORIGIN,ANALYSIS_END

SOURCE=ROOT/"c/k6/mod/tx8probe.c"


def wrapper(tmp_path,variant=0,*,rf=False,fixture=True,watchdog=True):
    p=tmp_path/f"phase-{variant}-rf-{rf}.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_PHASE_VECTOR_V5 1\n"
        f"#define TX8_STEER_VARIANT {variant}\n"
        "#define TX8_SYMBOL_RATE 150u\n"
        f"#define TX8_RF_ENABLE {int(rf)}\n"
        f"#define TX8_RF_FIXTURE_ACK {int(fixture)}\n"
        f"#define TX8_RF_WATCHDOG_ACK {int(watchdog)}\n"
        "#define TX8_HZ 433500000u\n"
        "#define TX8_GAIN 24u\n"
        f'#include "{SOURCE}"\n')
    return p


def test_steering_data_identical_pilot_differs_exactly_six_writes():
    words,a,b=source_variants()
    assert len(a)==len(b)==N==192
    assert len(words)==8
    assert np.array_equal(a[:177],b[:177])
    assert np.array_equal(a[183:],b[183:])
    assert a[174:186].reshape(4,3)[:,0].tolist()==[4,0,7,0]
    assert b[174:186].reshape(4,3)[:,0].tolist()==[4,1,2,0]
    assert np.all(a[186:]==0) and np.all(b[186:]==0)


def test_endpoint_audio_oscillator_rejoin_near_multiple_of_two_pi():
    words,a,b=source_variants()
    f=words/10.32444
    # 3 source slots of each changed word, drift-relative update rates
    # measured on the *prior* 3 TX15S captures. Tolerance is deliberately
    # loose to keep waveform qualification meaningful without SDR lock.
    for baud in (149.65,149.66,149.68):
        delta_cycles=(3/baud)*((f[1]-f[0])+(f[2]-f[7]))
        delta_deg=float(np.rad2deg(np.angle(np.exp(2j*np.pi*delta_cycles))))
        assert -9<delta_deg<0


def synthetic_phase(variant,*,initial_phase=None,noise=.022):
    words,a,b=source_variants()
    freq=words/10.32444
    sched=.886+np.arange(N)/149.664
    t=TIME_ORIGIN+np.arange(int(round((ANALYSIS_END-TIME_ORIGIN)*FS)))/FS
    deviations=np.interp(freq,[freq[0],freq[-1]],[1230,1001])
    ca,sa=continuous_audio_quadratures(t,sched,a,freq,deviations)
    cb,sb=continuous_audio_quadratures(t,sched,b,freq,deviations)
    x=t-1.51
    if initial_phase is None:
        # Engineer a precise -90 degree test source for this synthetic
        # rate. Unknown phase is a hard limitation on live open-loop
        # steering; this test only checks pilot-conditional prediction.
        slot=np.searchsorted(sched,t,side="right")-1
        mask=np.isin(slot,[187,188,189,190])
        d=np.array([(cb[mask]-ca[mask]).mean(),
                    (sb[mask]-sa[mask]).mean()])
        norm=np.linalg.norm(d)
        assert norm>np.pi/2
        initial_phase=float(np.arctan2(d[1],d[0])+
                            np.arccos(-np.pi/2/norm))
    q=np.array([np.cos(initial_phase),np.sin(initial_phase)])
    raw=0.6+2*np.pi*11*x+2*np.pi*(-9.2)*x*x/2
    raw+=(q[0]*(ca if variant==0 else cb)+q[1]*(sa if variant==0 else sb))
    raw+=np.random.default_rng(313).normal(0,noise,len(t))
    return raw,t,sched,a,b,freq,deviations


@pytest.mark.parametrize("variant",[0,1])
def test_synthetic_actual_rf_phase_steering_is_selected_by_six_tone_writes(variant):
    obs,t,sch,a,b,freq,dev=synthetic_phase(variant)
    score=fit_control_and_counterfactual(
        obs,t,np.full(len(t),4.),sch,a,b,freq,dev)
    q=evaluate_decisions({"primary_prediction":score},variant)
    assert q["predeclared_combined_test_pass"],(q,score[
        "pilot_predicted_steer_minus_control_guard_phase_deg"],
        score["measured_guard_rf_phase_step_from_control_deg"])
    assert q["model_advantage_on_steer_active"]>.1
    assert q["correct_program_coherence"]>.997
    if variant==0:
        assert abs(score["measured_guard_rf_phase_step_from_control_deg"])<2
    else:
        assert score["measured_guard_rf_phase_step_from_control_deg"] < -30
        assert abs(score["correct_b_step_error_deg"])<3


def test_synthetic_weak_phase_pulse_is_marked_inconclusive():
    obs,t,sch,a,b,freq,dev=synthetic_phase(1,initial_phase=1.18)
    score=fit_control_and_counterfactual(
        obs,t,np.full(len(t),4.),sch,a,b,freq,dev)
    decision=evaluate_decisions({"primary_prediction":score},1)
    assert not decision["guard_rf_phase_step_matches_predeclared_test"]
    assert not decision["predeclared_combined_test_pass"]


def test_synthetic_incorrect_variant_is_not_scored_as_correct():
    obs,t,sch,a,b,freq,dev=synthetic_phase(1)
    score=fit_control_and_counterfactual(
        obs,t,np.full(len(t),4.),sch,a,b,freq,dev)
    bad=evaluate_decisions({"primary_prediction":score},0)
    assert not bad["predeclared_combined_test_pass"]
    assert bad["model_advantage_on_steer_active"]<0


def test_pilot_leakage_is_rejected_if_source_modified_before_steering():
    words,a,b=source_variants()
    bad=b.copy()
    bad[168]=3
    obs,t,sch,aa,bb,freq,dev=synthetic_phase(1)
    with pytest.raises(ValueError,match="pilot"):
        fit_control_and_counterfactual(
            obs,t,np.full(len(t),4.),sch,a,bad,freq,dev)


def test_changes_after_steering_are_excluded_from_calibration():
    obs,t,sch,a,b,freq,dev=synthetic_phase(1)
    base=fit_control_and_counterfactual(
        obs,t,np.full(len(t),4.),sch,a,b,freq,dev)
    t2=obs.copy()
    window=(t>=sch[177])&(t<sch[191])
    t2[window]+=1.3
    other=fit_control_and_counterfactual(
        t2,t,np.full(len(t),4.),sch,a,b,freq,dev)
    assert base["pilot_phase_model_parameters"]==other[
        "pilot_phase_model_parameters"]
    assert other["measured_guard_rf_phase_step_from_control_deg"]!=base[
        "measured_guard_rf_phase_step_from_control_deg"]


@need_clang
@pytest.mark.parametrize("variant,rf",[(0,False),(0,True),(1,False),(1,True)])
def test_emulated_192_write_module_and_pa_timers_restore(tmp_path,variant,rf):
    p=wrapper(tmp_path,variant,rf=rf)
    code,entry=k6mod.build(str(p))
    assert entry==0 and len(code)<4096
    mmio,rcc,timers=k6_clock()
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x70:0,0x71:0},src=str(p),
        level=3,rx_hz=433500000,tx=tx,mmio=mmio,
        write_us=180,count=240_000_000)
    assert ret==192 and said[-1].startswith("tx8t"+("A" if variant==0 else "B")+" n=192 ")
    assert "late=0" in said[-1]
    words,a,b=source_variants()
    ids=a if variant==0 else b
    assert [v for reg,v,t in tx["w"] if reg==0x71 and v]==[
        int(words[k]) for k in ids]
    assert tx["log"][0][:2]==("tx_on",1800 if rf else 4000)
    assert tx["log"][-1][0]=="tx_off"
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==(
        [0,1,0] if rf else [0]*193)
    assert after[0x70]==after[0x71]==0
    assert not timers.get(0,0)&1 and rcc[0x18]==0


@need_clang
@pytest.mark.parametrize("variant,fixture,watchdog",[
    (0,False,False),(0,False,True),(0,True,False),
    (1,False,False),(1,False,True),(1,True,False)
])
def test_physical_rf_needs_both_acknowledgments(tmp_path,variant,fixture,watchdog):
    with pytest.raises(Exception):
        k6mod.build(str(wrapper(tmp_path,variant,rf=True,
                                  fixture=fixture,watchdog=watchdog)))


@need_clang
@pytest.mark.parametrize("variant",[2,255])
def test_unknown_steering_variant_rejected_by_compiler(tmp_path,variant):
    with pytest.raises(Exception):
        k6mod.build(str(wrapper(tmp_path,variant,rf=False)))


def test_no_radio_commands_in_offline_steering_analyzer():
    txt=(ROOT/"tools/k6_tx15t_rf_steering.py").read_text()
    for unsafe in ("import serial","RtlCapture(","open_port(",
                   "run_module(","tx_on(", "mutate_library("):
        assert unsafe not in txt
    assert "arbitrary_rf_iq_transmission_demonstrated=False" in txt


@pytest.mark.parametrize("variant",[0,1])
def test_physical_tx15t_when_saved(variant):
    p=Path.home()/(
        "flux-bench/tx-injection-20261122/tone-oscillator/"
        f"tx15t-phase-steer-{variant}-run01.npy")
    if not p.exists():pytest.skip("predeclared TX15T physical capture not yet available")
    report=analyze_file(p,variant)
    q=report["qualification"]
    assert report["pilot_only_fitted_parameters"]
    assert report["arbitrary_rf_iq_transmission_demonstrated"] is False
    assert report["waveform_diff_symbols"]==list(range(177,183))
    assert q["predeclared_combined_test_pass"],q
