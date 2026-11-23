#!/usr/bin/env python3
"""TX-15T CONTROL vs STEER: commanded transient audio-FM pulse into retained RF phase.

Scientific claim being tested:
  A: identical original TX15S late symbols:   [4,0,7,0] *3 each.
  B: change only two 3-slot blocks, at 177..182: [4,1,2,0].
  Both have identical 72 distributed pilot slots and 6 shutdown guards.
  At 183 the tone rejoins 0 and at 186..190 it remains 0.
  A vs B predicted after-pulse audio oscillator phase mismatch is ~4-6 deg.

Train one complex-RF phase + CFO + CFO slope + continuous-FM source model
from all FOUR *identical* pilot windows in the capture, then freeze the
same parameters for BOTH counterfactual programs. Also fit ONE local
residual CFO slope solely from the LAST SIX pilot symbols [168,174),
before the phase-steer events; apply that identical extrapolation to
both predictions, with zero effect before [168]. This pilot-only local
drift calibration was added after inspecting older TX15S A-controls,
but is FROZEN before new TX15T captures. Score independent
heldout steering at 177..185 and the early clean *guard* at 187..190,
with symbol 191 excluded because TX is dekeying. The retained phase
step is referred to late *pilot* slots 168..173 from the same recording;
no cross-transmission absolute phase comparison.

Predict pulse-specific RF phase rotation from B conditioned on pilot
phase. Three earlier TX15S recordings gave predicted negative rotations
of about -69 to -144 degrees, but the phase SIGN can reverse if source
oscillator phase/timebase changes. The locked test predicts both sign
and magnitude from the current emission's PILOT, and requires that
target to be large and agree with measured RF IQ. All thresholds
and the model are frozen BEFORE first TX15T physical RF.

This is real RECEIVED complex RF carrier phase, not proof of arbitrary
RF-IQ, an independent RF phase DAC or calibrated carrier phase.
READ ONLY: opens only exact source headers, U8 .npy, output JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import numpy as np

from k6_tx15s_rf_analyze import (
    read_vector,N,PILOT_INDICES,HOLDOUT_INDICES,GUARD_START,
    acquire_source,find_edges,schedule_from_pilot,
    TIME_ORIGIN,FS,T
)
from k6_tx15s_carrier_iq import (
    complex_downmix,pilot_tone_response,continuous_audio_quadratures,
    training_and_holdout_masks
)

ROOT=Path(__file__).resolve().parents[1]
CONTROL_HEADER=ROOT/"c/k6/mod/tx8_distributed_phase_probe.h"
STEER_HEADER=ROOT/"c/k6/mod/tx8_steer_phase_probe.h"

# Everything below is selected before the first real TX15T RF capture.
PILOT_PHASE_ANCHOR=tuple(range(168,174))
STEER_ACTIVE=tuple(range(177,186))
GUARD_CONFIRM=tuple(range(187,191))  # never the dekey-contaminated 191
PRIMARY_TIMEBASE="distributed_piecewise"
CARRIER_POLY_DEGREE=2
STRIDE=8
DECISION=dict(
    b_min_abs_predicted_guard_step_deg=35.0,
    b_min_abs_measured_guard_step_deg=25.0,
    b_max_abs_phase_step_prediction_error_deg=45.0,
    a_max_abs_control_guard_step_deg=40.0,
    min_correct_model_coherence_advantage_on_steer_active=0.10,
    min_clean_guard_samples=120,
)

def source_variants(control=CONTROL_HEADER,steer=STEER_HEADER):
    words_a,ids_a=read_vector(control)
    words_b,ids_b=read_vector(steer)
    if not np.array_equal(words_a,words_b):
        raise ValueError("control and steering variant have different tone words")
    diff=np.flatnonzero(ids_a!=ids_b).tolist()
    if diff!=list(range(177,183)):
        raise ValueError(f"TX15T steering controls changed outside locked 177-182: {diff}")
    if ids_a[174:192].tolist()!=[
        4,4,4,0,0,0,7,7,7,0,0,0,0,0,0,0,0,0]:
        raise ValueError("control tail no longer matches frozen plan")
    if ids_b[174:192].tolist()!=[
        4,4,4,1,1,1,2,2,2,0,0,0,0,0,0,0,0,0]:
        raise ValueError("steering tail no longer matches frozen plan")
    if not np.array_equal(ids_a[:174],ids_b[:174]):
        raise ValueError("pilot and pre-steer source blocks must match")
    return words_a,ids_a,ids_b


def circular_mean_rad(v):
    vals=np.asarray(v,dtype=float)
    if not len(vals):
        raise ValueError("empty circular mean")
    return float(np.angle(np.mean(np.exp(1j*vals))))


def angle_delta_deg(a,b=0.):
    return float(np.rad2deg(np.angle(np.exp(1j*(a-b)))))


def metrics(residual):
    v=np.asarray(residual,dtype=float)
    if not len(v):raise ValueError("no scored complex-RF phase samples")
    return dict(
        n_samples=len(v),
        phase_rms_deg=float(np.sqrt(np.mean(np.rad2deg(v)**2))),
        phase_median_abs_deg=float(np.median(abs(np.rad2deg(v)))),
        circular_coherence=float(abs(np.mean(np.exp(1j*v)))),
        circular_mean_deg=float(np.rad2deg(circular_mean_rad(v))),
    )


def class_mask(time,schedule,slots,iq_magnitude):
    """Known, predeclared symbol positions; never data-adaptive phase labels."""
    idx=np.searchsorted(schedule,time,side="right")-1
    idx=np.clip(idx,0,N-1)
    begin=schedule[idx]
    stop=np.r_[schedule[1:],schedule[-1]+T][idx]
    return (
        np.isin(idx,np.asarray(slots,dtype=int))&
        (time>begin+.001)&(time<stop-.00065)&
        (iq_magnitude>1.5)&
        (np.arange(len(time))%STRIDE==0)
    )


def fit_control_and_counterfactual(obs,time,iq_magnitude,schedule,
                                   ids_a,ids_b,freq,deviation):
    ca,sa=continuous_audio_quadratures(time,schedule,ids_a,freq,deviation)
    cb,sb=continuous_audio_quadratures(time,schedule,ids_b,freq,deviation)
    train,all_heldout=training_and_holdout_masks(
        time,schedule,iq_magnitude,stride=STRIDE)
    if train.sum()<2200 or all_heldout.sum()<3400:
        raise ValueError("insufficient training or heldout RF IQ")
    # Exactly identical programmed FM before the pulse; model fitting
    # cannot reveal the actual variant from these pilot observations.
    if not np.array_equal(ca[train],cb[train]) or not np.array_equal(
            sa[train],sb[train]):
        raise ValueError("hypotheses differ on pilot samples: blind label leakage")
    dt=time-time[train].mean()
    X=np.stack([np.ones(len(time)),dt,dt*dt,ca,sa],axis=1)
    coefficients=np.linalg.lstsq(X[train],obs[train],rcond=None)[0]
    nuisance=X[:,:3]@coefficients[:3]
    pa=nuisance+coefficients[3]*ca+coefficients[4]*sa
    pb=nuisance+coefficients[3]*cb+coefficients[4]*sb
    if not np.allclose(pa[train],pb[train],rtol=0,atol=1e-10):
        raise ValueError("cannot use the same pilot-trained coefficients")
    # The global quadratic CFO approximation leaves residual late RF
    # drift: old, *independent TX15S control* captures measured A-only
    # guard apparent shifts -1.5/-88.3/-40.0 deg. One pilot-only local
    # residual-slope fitted on the six immediately preceding pilot words
    # suppresses that false phase jump without using any steer/guard
    # sample. The LOCAL calibration is common to both hypotheses.
    late_mask=class_mask(time,schedule,PILOT_PHASE_ANCHOR,iq_magnitude)
    if late_mask.sum()<180:
        raise ValueError("too few late pilot IQ samples for local RF-CFO")
    late_x=time[late_mask]-float(np.mean(time[late_mask]))
    late_y=obs[late_mask]-pa[late_mask]
    local=np.linalg.lstsq(
        np.stack([np.ones(len(late_x)),late_x],axis=1),
        late_y,rcond=None)[0]
    local_slope=float(local[1])
    local_correction=local_slope*np.maximum(0.,time-schedule[PILOT_PHASE_ANCHOR[0]])
    pa=pa+local_correction
    pb=pb+local_correction
    masks={
        "late_pilot_anchor":late_mask,
        "steer_active":class_mask(time,schedule,STEER_ACTIVE,iq_magnitude),
        "clean_guard":class_mask(time,schedule,GUARD_CONFIRM,iq_magnitude),
        "whole_heldout":all_heldout,
    }
    for name,mask in masks.items():
        if mask.sum()<DECISION["min_clean_guard_samples"]:
            raise ValueError("not enough clean samples in "+name)

    # Residual to a program the radio *would have* transmitted if variant A.
    # Subtract last pilot anchor so no receiver or startup absolute phase is
    # compared between separate real emissions.
    reference=circular_mean_rad(
        np.angle(np.exp(1j*(obs[masks["late_pilot_anchor"]]-
                             pa[masks["late_pilot_anchor"]]))))
    observed_guard=circular_mean_rad(
        np.angle(np.exp(1j*(obs[masks["clean_guard"]]-
                             pa[masks["clean_guard"]]))))
    measured_step=angle_delta_deg(observed_guard,reference)
    # This predicted B-versus-A phase step uses pilot-only coefficients.
    # Its sign may depend on unknown radio oscillator initial phase;
    # those unknowns are estimated from *pilot*, not heldout.
    predicted_step=circular_mean_rad(
        np.angle(np.exp(1j*(pb[masks["clean_guard"]]-
                             pa[masks["clean_guard"]]))))
    predicted_step_deg=float(np.rad2deg(predicted_step))
    result=dict(
        pilot_phase_model_parameters=[float(v) for v in coefficients],
        pilot_rf_cfo_hz=float(coefficients[1]/(2*np.pi)),
        pilot_cfo_drift_hz_per_s=float(coefficients[2]/np.pi),
        pilot_fm_quadrature_gain=float(np.hypot(*coefficients[3:])),
        pilot_coefficient_identity_for_both_hypotheses=True,
        pilot_only_last_six_symbol_local_cfo_slope_hz=float(local_slope/(2*np.pi)),
        local_cfo_extrapolation_applies_only_from_slot_168=True,
        local_late_pilot_linear_residual_fit_rms_deg=float(
            np.rad2deg(np.sqrt(np.mean((
                late_y-(local[0]+local[1]*late_x))**2)))),
        samples_in_windows={k:int(mask.sum()) for k,mask in masks.items()},
        measured_guard_rf_phase_step_from_control_deg=measured_step,
        pilot_predicted_steer_minus_control_guard_phase_deg=predicted_step_deg,
        correct_b_step_error_deg=angle_delta_deg(
            np.deg2rad(measured_step),predicted_step),
        effective_steer_phase_window_not_closed_loop=True,
        rf_phase_model_scores={
            name:{
                "control_A":metrics(np.angle(np.exp(1j*(obs[mask]-pa[mask])))),
                "pulse_B":metrics(np.angle(np.exp(1j*(obs[mask]-pb[mask])))),
            } for name,mask in masks.items()
        },
    )
    return result


def evaluate_decisions(report,variant):
    main=report["primary_prediction"]
    m=main["rf_phase_model_scores"]["steer_active"]
    if variant==0:
        correct=m["control_A"]["circular_coherence"]
        wrong=m["pulse_B"]["circular_coherence"]
        guard_step_good=abs(main["measured_guard_rf_phase_step_from_control_deg"]) <= DECISION[
            "a_max_abs_control_guard_step_deg"]
    else:
        correct=m["pulse_B"]["circular_coherence"]
        wrong=m["control_A"]["circular_coherence"]
        predicted=main["pilot_predicted_steer_minus_control_guard_phase_deg"]
        measured=main["measured_guard_rf_phase_step_from_control_deg"]
        guard_step_good=(
            abs(predicted)>=DECISION["b_min_abs_predicted_guard_step_deg"] and
            abs(measured)>=DECISION["b_min_abs_measured_guard_step_deg"] and
            predicted*measured>0 and
            abs(main["correct_b_step_error_deg"])<=DECISION[
                "b_max_abs_phase_step_prediction_error_deg"])
    return dict(
        variant=variant,
        model_advantage_on_steer_active=float(correct-wrong),
        correct_program_coherence=float(correct),
        counterfactual_program_coherence=float(wrong),
        correct_source_beats_wrong_by_threshold=bool(
            correct-wrong >= DECISION[
                "min_correct_model_coherence_advantage_on_steer_active"]),
        guard_rf_phase_step_matches_predeclared_test=bool(guard_step_good),
        predeclared_combined_test_pass=bool(
            correct-wrong >= DECISION[
                "min_correct_model_coherence_advantage_on_steer_active"] and
            guard_step_good),
        limitations="Threshold is an engineering qualification for this fixed constrained FM pulse, not arbitrary IQ EVM."
    )


def analyze_file(iq_path:Path,variant:int,control=CONTROL_HEADER,steer=STEER_HEADER):
    if variant not in (0,1):raise ValueError("variant must be 0 or 1")
    words,ids_a,ids_b=source_variants(control,steer)
    ids_current=ids_b if variant==1 else ids_a
    freq=words/10.32444
    z,f=complex_downmix(iq_path)
    time=TIME_ORIGIN+np.arange(len(z))/FS
    anchor=acquire_source(f,ids_current,freq)
    edge=find_edges(f,ids_current,freq,anchor["start_hint_s"])
    clock,global_t,piece_t=schedule_from_pilot(edge)
    observed=np.unwrap(np.angle(z))
    responses={}
    for name,schedule in (("global_linear",global_t),
                          ("distributed_piecewise",piece_t)):
        dev,cal=pilot_tone_response(f,ids_a,freq,schedule)
        response=fit_control_and_counterfactual(
            observed,time,abs(z),schedule,ids_a,ids_b,freq,dev)
        response["pilot_tone_response"]=cal
        responses[name]=response
    with iq_path.open("rb") as fp:
        sha=hashlib.file_digest(fp,"sha256").hexdigest()
    report=dict(
        study="TX-15T constrained RF phase-steering by source-selected RFIC FM tone pulse",
        predeclared_variant=variant,
        source_iq=str(iq_path),source_iq_sha256=sha,
        variant_program_headers={
            "control_A":str(control),"pulse_B":str(steer)},
        header_sha256={
            "control_A":hashlib.sha256(control.read_bytes()).hexdigest(),
            "pulse_B":hashlib.sha256(steer.read_bytes()).hexdigest()},
        rf_iq_directly_observed=True,
        arbitrary_rf_iq_transmission_demonstrated=False,
        tx_phase_DAC_demonstrated=False,
        receiver_phase_reference_only=True,
        offline_only=True,
        waveform_diff_symbols=list(range(177,183)),
        pilot_slots_identical_before_174=True,
        same_oscillator_word_at_183_and_guard=True,
        guard_confirmation_slots=list(GUARD_CONFIRM),
        terminal_dekey_slot_191_unscored=True,
        pilot_only_fitted_parameters=True,
        late_pilot_slope_model_selected_using_prior_A_controls=True,
        primary_timebase=PRIMARY_TIMEBASE,
        fixed_qualification_thresholds=DECISION,
        pulse_design_used_prior_three_tx15s_captures=True,
        control_absolute_phase_not_comparable_between_independent_emissions=True,
        source_acquisition=anchor,
        pilot_clock=clock,
        models=responses,
        primary_prediction=responses[PRIMARY_TIMEBASE],
    )
    report["qualification"]=evaluate_decisions(report,variant)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--variant",type=int,choices=(0,1),required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing overwrite")
    result=analyze_file(args.iq,args.variant)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+"\n")
    score=result["primary_prediction"];qual=result["qualification"]
    print("variant",args.variant,"pilot-target phase",round(score[
        "pilot_predicted_steer_minus_control_guard_phase_deg"],2),
        "actual A-relative phase",round(score[
        "measured_guard_rf_phase_step_from_control_deg"],2))
    print("steering correct vs wrong RF IQ coherence",
          round(qual["correct_program_coherence"],4),
          round(qual["counterfactual_program_coherence"],4))
    print("threshold test",qual["predeclared_combined_test_pass"],
          "step error",round(score["correct_b_step_error_deg"],2),
          "degrees; saved",args.out)


if __name__=="__main__":
    main()
