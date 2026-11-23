#!/usr/bin/env python3
"""Compare TWO independently captured TX-15R RF phase runs (offline only).

Inputs: frozen, per-capture TX-15R JSON produced without heldout timing
or phase training. This comparison intentionally *examines heldout errors*
afterward: its cross-run correlation / drift interpretation is exploratory,
NOT a predeclared generalization score or transmitter control command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def _rms(values):
    v=np.asarray(values,dtype=float)
    return float(np.sqrt(np.mean(v*v))) if len(v) else float("nan")


def _phase_run(r):
    lag=float(r["audio_transfer_lag"]["pilot_effective_audio_lag_s"])
    events={}
    for e in r["phase_edge_events"]:
        if (e["region"]=="heldout" and e["changed"] and
            e["fit_qualified"]):
            v=e["apparent_audio_phase_jump_rad"]-2*np.pi*e["delta_audio_hz"]*lag
            residual=float(np.rad2deg(np.angle(np.exp(1j*v))))
            events[int(e["index"])]=dict(
                pair=(int(e["before"]),int(e["after"])),
                audio_phase_residual_deg=residual)
    return events


def compare_runs(a:dict,b:dict)->dict:
    if (a.get("experiment")!=b.get("experiment") or
        a.get("header_sha256")!=b.get("header_sha256") or
        a.get("source_iq_sha256")==b.get("source_iq_sha256") or
        a.get("pilot_symbols")!=48 or b.get("pilot_symbols")!=48 or
        a.get("heldout_symbols")!=144 or b.get("heldout_symbols")!=144 or
        not a.get("clock_and_phase_training_uses_heldout") is False or
        not b.get("clock_and_phase_training_uses_heldout") is False):
        raise ValueError("must compare two distinct frozen TX-15R captures")
    ea,eb=_phase_run(a),_phase_run(b)
    common=sorted(set(ea)&set(eb))
    if len(common)<40:
        raise ValueError("too few common independent heldout phase events")
    for index in common:
        if ea[index]["pair"]!=eb[index]["pair"]:
            raise ValueError("tone pair labels differ between captures")
    ya=np.array([ea[i]["audio_phase_residual_deg"] for i in common])
    yb=np.array([eb[i]["audio_phase_residual_deg"] for i in common])
    dif=np.rad2deg(np.angle(np.exp(1j*np.deg2rad(ya-yb))))
    corr=float(np.corrcoef(ya,yb)[0,1])
    amplitude=[]
    da=a["uncalibrated_heldout_tone_deviation_response"]
    db=b["uncalibrated_heldout_tone_deviation_response"]
    if len(da)!=8 or len(db)!=8:
        raise ValueError("eight source tone amplitudes required")
    for x,y in zip(da,db):
        if x["tone_index"]!=y["tone_index"]:
            raise ValueError("source tone index mismatch")
        amplitude.append({
            "tone_index":x["tone_index"],
            "audio_tone_hz":x["audio_tone_hz"],
            "run1_deviation_hz":x["median_rf_discriminator_deviation_hz"],
            "run2_deviation_hz":y["median_rf_discriminator_deviation_hz"],
            "between_run_difference_hz":round(
                y["median_rf_discriminator_deviation_hz"]-
                x["median_rf_discriminator_deviation_hz"],3)
        })
    def summary(r):
        c=r["clock_training_and_holdout"];p=r["audio_phase_heldout"]
        return {
            "iq_sha256":r["source_iq_sha256"],
            "heldout_tones_correct":r["tone_decoder"]["heldout_correct"],
            "heldout_tones_total":144,
            "last_slot_error":r["tone_decoder"].get("heldout_errors",[]),
            "terminal_slot_audio_sine_fit_rmse_hz":r["tone_fit_rms_hz"][-1],
            "pilot_clock_edges":c["pilot_strong_events"],
            "received_slot_rate_per_s":c["pilot_observed_update_rate_hz"],
            "heldout_transition_midpoint_median_abs_error_us":
                 c["heldout_midpoint_median_abs_us"],
            "heldout_same_word_phase_rms_deg":p["heldout_same_word"]["rms_deg"],
            "heldout_changed_word_phase_rms_after_pilot_delay_deg":
                 p["heldout_changed_word_clock_and_one_delay"]["rms_deg"],
        }
    return {
        "study":"Flux TX-15R cross-run reproducibility of received FM audio tone phase",
        "read_only":True,
        "crossrun_comparison_is_exploratory":True,
        "same_run_heldout_not_used_to_fit_training_model":True,
        "rf_carrier_phase_or_iq_evm_measured":False,
        "calibrated_rf_power_or_emission_mask_measured":False,
        "run1":summary(a),"run2":summary(b),
        "common_heldout_tone_change_transitions":len(common),
        "audio_phase_residual_interrun_pearson_correlation":corr,
        "audio_phase_residual_interrun_median_abs_difference_deg":
             float(np.median(abs(dif))),
        "audio_phase_residual_interrun_rms_difference_deg":_rms(dif),
        "run1_heldout_changed_audio_phase_rms_deg":_rms(ya),
        "run2_heldout_changed_audio_phase_rms_deg":_rms(yb),
        "per_audio_tone_deviation_response":amplitude,
        "first_to_last_tone_deviation_ratio_run1":
             float(da[-1]["median_rf_discriminator_deviation_hz"]/
                   da[0]["median_rf_discriminator_deviation_hz"]),
        "first_to_last_tone_deviation_ratio_run2":
             float(db[-1]["median_rf_discriminator_deviation_hz"]/
                   db[0]["median_rf_discriminator_deviation_hz"]),
        "time_trend_and_pair_effects_are_not_causally_separated":True,
        "terminal_last_slot_is_badly_conditioned_in_both_captures":bool(
            a["tone_fit_rms_hz"][-1]>1000 and b["tone_fit_rms_hz"][-1]>1000),
        "notes":[
            "Two separate 192-tone emissions, not repeated fits to the same IQ.",
            "Per-run pilot clock and phase-delay fits frozen before heldout scores.",
            "Cross-run correlation and pair/group comparisons are exploratory because heldout residuals were inspected to formulate the hypothesis.",
            "Recovering audio tone phase from FM RF does NOT measure absolute RF carrier phase.",
            "Both final slot sinusoids have major RF shutdown/settling contamination; do not silently remove their tone errors from the 144-slot count.",
            "Deviations are Hz relative to an uncalibrated air-coupled RTL-SDR receiver and cannot separately identify transmitter vs receiver filtering.",
        ]
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--a",required=True,type=Path)
    p.add_argument("--b",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing overwrite")
    a=json.loads(args.a.read_text());b=json.loads(args.b.read_text())
    score=compare_runs(a,b)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(score,indent=2)+"\n")
    print("runs:",score["run1"]["heldout_tones_correct"],"/144;",
          score["run2"]["heldout_tones_correct"],"/144")
    print("residual Pearson correlation",
          round(score["audio_phase_residual_interrun_pearson_correlation"],3),
          "median absolute difference",
          round(score["audio_phase_residual_interrun_median_abs_difference_deg"],2),"deg")
    print("audio-FM deviation high-tone / low-tone ratio:",
          round(score["first_to_last_tone_deviation_ratio_run1"],3),
          round(score["first_to_last_tone_deviation_ratio_run2"],3))
    print("saved",args.out)


if __name__=="__main__":
    main()
