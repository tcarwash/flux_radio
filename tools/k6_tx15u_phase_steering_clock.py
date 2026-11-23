#!/usr/bin/env python3
"""TX-15U: prospective PILOT-ONLY audio-oscillator rate correction for TX15T A/B.

The already-frozen TX15T phase-steer source firmware is UNCHANGED.
The new analysis was developed using the FOUR EXISTING TX15T captures;
its scores on those four files are post-hoc design evidence only.
An independent validation requires a NEW control and a NEW pulse
recording after this file and its thresholds are committed.

TRAINING:
  1. Exactly the TX15T four distributed pilot windows determine
     symbol timing via existing high-contrast source transitions.
  2. In pilot-only FM discriminator samples, fit one effective common
     additive AUDIO oscillator rate offset (Hz). The per-tone phase
     intercepts are nuisance parameters and canceled by within-tone
     centering of unwrapped pilot phase residuals.
  3. Correct the integrated source oscillator phase using freq+offset
     and retain the original TX15T pilot-only complex RF IQ model,
     late-pilot local CFO correction, source-vs-counterfactual scoring
     and predeclared engineering thresholds.
  4. NEVER fit oscillator frequency to heldout samples or guard,
     never modify the unchanged control/pulse RF register tables.

Effective rate is relative to a free-running, uncalibrated SDR clock
and receiver/oscillator phase transfer. It is NOT a physical direct
measurement of the FD6818B crystal, MCU timebase, or calibrated IQ EVM.
READ ONLY (no radio, serial, SDR device, compilation or SPI).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from k6_tx15t_rf_steering import (
    source_variants,fit_control_and_counterfactual,evaluate_decisions,
    CONTROL_HEADER,STEER_HEADER,DECISION,PRIMARY_TIMEBASE
)
from k6_tx15s_rf_analyze import (
    PILOT_INDICES,TIME_ORIGIN,FS,acquire_source,
    find_edges,schedule_from_pilot,fit_audio_per_slot,
)
from k6_tx15s_carrier_iq import complex_downmix,pilot_tone_response

FREQUENCY_CORRECTION_LIMIT_HZ=0.60
MIN_PER_TONE_PILOT_SLOTS=8
MAX_PILOT_PHASE_RESIDUAL_RMS_DEG=30.0


def estimate_pilot_effective_audio_rate(f,ids,freq,schedule):
    """Per-tone fixed-effect frequency-offset OLS, pilot-only.

    Repeated known motif [0,7,1,6,2,5] provides three or four
    independent phase observations per tone across the TX burst.
    Treat any static frequency-dependent TX/RX audio phase bias
    as a nuisance intercept for each source tone, not drift.
    Unwrap residuals only *within a single repeated pilot tone*
    so unknown same-tone phase transfer offsets cancel.
    """
    phase,amp,rmse=fit_audio_per_slot(f,ids,freq,schedule)
    nominal=np.r_[0.,np.cumsum(2*np.pi*freq[ids[:-1]]*np.diff(schedule))]
    used=[int(i) for i in PILOT_INDICES
          if i>=3 and rmse[i]<250 and amp[i]>300]
    group={}
    rows=[]
    tone_intercepts={}
    for tone in sorted(set(int(ids[i]) for i in used)):
        idx=np.asarray([i for i in used if ids[i]==tone],dtype=int)
        if len(idx)<MIN_PER_TONE_PILOT_SLOTS:
            raise ValueError(f"tone {tone} had insufficient qualified pilot")
        err=np.unwrap(np.angle(np.exp(1j*(phase[idx]-nominal[idx]))))
        t=np.asarray(schedule[idx],float)
        centered_t=t-np.mean(t)
        centered_err=err-np.mean(err)
        group[tone]=(idx,t,err)
        for j in range(len(idx)):
            rows.append((float(centered_t[j]),float(centered_err[j]),tone))
    if sorted(group)!=[0,1,2,5,6,7]:
        raise ValueError("expected exactly six fixed pilot tone groups")
    data=np.asarray([[r[0],r[1]] for r in rows])
    x,y=data[:,0],data[:,1]
    if float(x@x)<.4:
        raise ValueError("pilot time span too narrow for oscillator-rate fit")
    slope_rad_s=float((x@y)/(x@x))
    offset=float(slope_rad_s/(2*np.pi))
    residual=np.rad2deg(y-slope_rad_s*x)
    rms=float(np.sqrt(np.mean(residual**2)))
    if abs(offset)>FREQUENCY_CORRECTION_LIMIT_HZ:
        raise ValueError("pilot audio-rate correction out of qualified bounds")
    if rms>MAX_PILOT_PHASE_RESIDUAL_RMS_DEG:
        raise ValueError("pilot audio-phase fixed-effect transfer too noisy")
    return dict(
        pilot_only=True,
        fitted_tone_nuisance_intercepts=True,
        source_tone_groups=[int(k) for k in sorted(group)],
        qualified_pilot_slots=len(rows),
        measured_effective_common_audio_rate_correction_hz=offset,
        residual_pilot_phase_rms_deg=rms,
        independent_tone_group_counts={str(k):len(v[0]) for k,v in group.items()},
        no_heldout_or_guard_audio_phase_used=True,
        true_radio_xtal_rate_or_recovered_rf_iq_evm_measured=False,
    )


def analyze_file(iq:Path,variant:int):
    if variant not in (0,1):raise ValueError("invalid predeclared variant")
    words,ids_a,ids_b=source_variants()
    ids=ids_b if variant else ids_a
    freq=words/10.32444
    z,f=complex_downmix(iq)
    time=TIME_ORIGIN+np.arange(len(z))/FS
    acquisition=acquire_source(f,ids,freq)
    edge=find_edges(f,ids,freq,acquisition["start_hint_s"])
    clock,linear,piece=schedule_from_pilot(edge)
    obs=np.unwrap(np.angle(z))
    models={}
    for name,schedule in (
        ("global_linear",linear),
        ("distributed_piecewise",piece),
    ):
        calibration=estimate_pilot_effective_audio_rate(f,ids_a,freq,schedule)
        deviation,response=pilot_tone_response(f,ids_a,freq,schedule)
        corrected_freq=freq+calibration[
            "measured_effective_common_audio_rate_correction_hz"]
        predicted=fit_control_and_counterfactual(
            obs,time,abs(z),schedule,
            ids_a,ids_b,corrected_freq,deviation)
        predicted["pilot_audio_oscillator_rate_correction"]=calibration
        predicted["pilot_deviation_frequency_response"]=response
        models[name]=predicted
    with iq.open("rb") as fp:
        digest=hashlib.file_digest(fp,"sha256").hexdigest()
    report=dict(
        study="TX-15U pilot-only effective audio rate transfer for intentional RF-phase pulse",
        source_iq=str(iq),source_iq_sha256=digest,
        variant=variant,
        unchanged_tx15t_source_programs=True,
        audio_rate_correction_selected_after_first_four_tx15t_captures=True,
        first_four_tx15t_captures_are_exploratory_not_independent_validation=True,
        prospective_qualification_requires_fresh_archived_capture=True,
        raw_rf_iq_observed=True,
        arbitrary_rf_iq_transmission_demonstrated=False,
        absolute_phase_command_without_live_feedback_demonstrated=False,
        pilot_only_timebase_rate_and_complex_rf_parameters=True,
        pilot_windows=[[0,18],[48,66],[96,114],[156,174]],
        no_heldout_training=True,
        control_both_variants_have_identical_pilots=True,
        predeclared_primary_timebase=PRIMARY_TIMEBASE,
        locked_tx15t_qualification_thresholds=DECISION,
        pilot_acquisition=acquisition,
        timing_model=clock,
        models=models,
        primary_prediction=models[PRIMARY_TIMEBASE],
        source_headers=dict(A=str(CONTROL_HEADER),B=str(STEER_HEADER)),
        header_sha256={
            "A":hashlib.sha256(CONTROL_HEADER.read_bytes()).hexdigest(),
            "B":hashlib.sha256(STEER_HEADER.read_bytes()).hexdigest(),
        },
        limitation="One common effective audio frequency correction and unknown receiver transfer. No independent analog RF IQ phase DAC.",
    )
    report["qualification"]=evaluate_decisions(report,variant)
    return report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--variant",required=True,type=int,choices=(0,1))
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing to overwrite evidence")
    report=analyze_file(args.iq,args.variant)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    a=report["primary_prediction"]
    score=report["qualification"]
    eps=a["pilot_audio_oscillator_rate_correction"][
        "measured_effective_common_audio_rate_correction_hz"]
    print("variant",args.variant,"pilot audio-rate correction",round(eps,5),"Hz")
    print("predicted RF phase step",round(a[
        "pilot_predicted_steer_minus_control_guard_phase_deg"],2),
        "actual",round(a["measured_guard_rf_phase_step_from_control_deg"],2),
        "deg")
    print("correct vs wrong RF phase coherence",
          round(score["correct_program_coherence"],4),
          round(score["counterfactual_program_coherence"],4),
          "locked test pass",score["predeclared_combined_test_pass"])
    print("saved",args.out)


if __name__=="__main__":
    main()
