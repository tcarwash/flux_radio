#!/usr/bin/env python3
"""Flux TX-15P saved-RF phase-edge scoring (READ-ONLY).

Interprets observed *RF frequency discriminator audio*, not raw RF phase.
TIMING IS SELECTED USING FIRST 16 SYMBOLS ONLY. Per-slot sinusoid
amplitude/phase fits are measurements; held-out data never alter timing.
This deliberately separates same-register-word writes from changed-word
updates, which have much greater filtering/oscillator uncertainty.
No radio, SDR or serial I/O.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import k6_tx_tone_rf_analyze as tone
from k6_tx15_phase_vector import NEW_HEADER, parse_header, WORD_PER_HZ

RAW_FS = 1_024_000
PROC_FS = tone.FS_PROC
TONE_BAUD = 150
PILOT = 16
SYMBOLS = 64


def wrap_phase(theta):
    """Radial angle in [-pi, pi]."""
    return np.angle(np.exp(1j * theta))


def fit_phases(f, symbols, tones, *, first_s, time_origin_s, fs=PROC_FS,
               baud=TONE_BAUD, left_guard_s=.0010, right_guard_s=.00065,
               limit_symbols=SYMBOLS, symbol_step_s=None):
    """Independent least-squares sine+cosine+DC for every prescribed slot."""
    step=1/baud if symbol_step_s is None else float(symbol_step_s)
    if (len(symbols)!=SYMBOLS or len(tones)!=8 or fs<=0 or baud<=0 or
        not 1<=limit_symbols<=SYMBOLS or
        not np.isfinite(first_s) or not np.isfinite(f).all() or
        not np.isfinite(step) or not .99/baud<=step<=1.01/baud or
        not 0<=left_guard_s<step-right_guard_s):
        raise ValueError("invalid phase-observation inputs")
    phases,amplitudes,offsets,rmses=[],[],[],[]
    for i,k in enumerate(symbols[:limit_symbols]):
        expected=float(tones[int(k)])
        t0=first_s+i*step
        lo=int(round((t0+left_guard_s-time_origin_s)*fs))
        hi=int(round((t0+step-right_guard_s-time_origin_s)*fs))
        if lo<0 or hi>len(f) or hi-lo<fs//1000:
            raise ValueError(f"slot {i} outside RF discriminator window")
        time=np.arange(lo,hi,dtype=float)/fs+time_origin_s-t0
        observed=f[lo:hi]
        x=np.column_stack((np.sin(2*np.pi*expected*time),
                           np.cos(2*np.pi*expected*time),
                           np.ones(len(time))))
        c=np.linalg.lstsq(x,observed,rcond=None)[0]
        ph=np.arctan2(c[1],c[0])
        amp=float(np.hypot(c[0],c[1]))
        rmse=float(np.sqrt(np.mean((observed-x@c)**2)))
        phases.append(float(ph))
        amplitudes.append(amp)
        offsets.append(float(c[2]))
        rmses.append(rmse)
    return dict(phases_rad=np.array(phases),
                amplitude_hz=np.array(amplitudes),
                offset_hz=np.array(offsets),
                fit_rmse_hz=np.array(rmses))


def summarize_edges(phases,symbols,tones,amplitudes,rmses,*,pilot=PILOT,baud=TONE_BAUD,
                    min_amplitude=300.,max_rmse=250.,symbol_step_s=None):
    if any(len(x)!=SYMBOLS for x in (phases,symbols,amplitudes,rmses)):
        raise ValueError("edge vectors have inconsistent lengths")
    if not (8<=pilot<SYMBOLS) or baud<=0:
        raise ValueError("invalid edge partition")
    step=1/baud if symbol_step_s is None else float(symbol_step_s)
    if not np.isfinite(step) or not .99/baud<=step<=1.01/baud:
        raise ValueError("invalid measured symbol interval")
    edges=[]
    for i in range(1,SYMBOLS):
        prev=int(symbols[i-1]);nxt=int(symbols[i])
        qualified=bool(min(amplitudes[i-1],amplitudes[i])>=min_amplitude and
                       max(rmses[i-1],rmses[i])<=max_rmse)
        delta=float(wrap_phase(phases[i]-phases[i-1]-
                               2*np.pi*float(tones[prev])*step))
        reset_delta=float(wrap_phase(-2*np.pi*float(tones[prev])*step))
        edges.append({
            "symbol_index_after_write":i,
            "region":"pilot" if i<pilot else "heldout",
            "same_word_write":prev==nxt,
            "before_tone_index":prev,"after_tone_index":nxt,
            "fit_qualified":qualified,
            "observed_apparent_phase_jump_deg":float(np.rad2deg(delta)),
            "same_word_reset_every_write_expected_jump_deg":
                float(np.rad2deg(reset_delta)),
            "fit_rmse_hz_before":float(rmses[i-1]),
            "fit_rmse_hz_after":float(rmses[i]),
        })
    def agg(e):
        if not e:
            return dict(n=0)
        got=np.array([x["observed_apparent_phase_jump_deg"] for x in e])
        rms=float(np.sqrt(np.mean(got*got)))
        return dict(n=len(e),
                    median_abs_apparent_phase_jump_deg=float(np.median(abs(got))),
                    rms_apparent_phase_jump_deg=rms,
                    max_abs_apparent_phase_jump_deg=float(max(abs(got))),
                    zero_jump_coherence=float(abs(np.mean(
                        np.exp(1j*np.deg2rad(got))))))
    partition={}
    for region in ("pilot","heldout"):
        for same in (True,False):
            group=[e for e in edges if e["region"]==region
                   and e["same_word_write"]==same and e["fit_qualified"]]
            partition[f"{region}_{'same_word' if same else 'changed_word'}"]=agg(group)
    # A held-out SAME-WORD comparison requires no arbitrary cross-tone phase
    # correction: frequency is unchanged through the register write.
    same=[e for e in edges if e["region"]=="heldout"
          and e["same_word_write"] and e["fit_qualified"]]
    if same:
        got=np.deg2rad([e["observed_apparent_phase_jump_deg"] for e in same])
        reset=np.deg2rad([e["same_word_reset_every_write_expected_jump_deg"] for e in same])
        partition["heldout_same_word_reset_every_write_hypothesis"]={
            "n":len(same),
            "phase_residual_rms_deg":float(np.rad2deg(
                np.sqrt(np.mean(wrap_phase(got-reset)**2)))),
            "phase_residual_median_abs_deg":float(np.median(abs(
                np.rad2deg(wrap_phase(got-reset))))),
            "compare_with_continuous_rms_deg":
                partition["heldout_same_word"]["rms_apparent_phase_jump_deg"]
        }
    return dict(edges=edges,summary=partition)


def analyze(iq_path:Path,header:Path=NEW_HEADER,*,early=.65,late=1.55,
            training_first=.855,training_last=.925)->dict:
    words,symbols=parse_header(header)
    tones=words.astype(float)/WORD_PER_HZ
    # Override the older RF tone classifier's frequency *alphabet*,
    # not its recorded IQ or its training-only timing policy.
    old= tone.TONES
    try:
        tone.TONES=tones
        f,env=tone.demod_raw(iq_path,early_s=early,late_s=late)
        fit=tone.fit_training_only(f,symbols.tolist(),
                                   time_origin_s=early,
                                   start=training_first,
                                   end=training_last,spacing=.00015)
    finally:
        tone.TONES=old
    # Tone-ID decoding has a broad timing maximum, because it excludes
    # symbol edges. Refine timing *using PILOT DATA ONLY* by fitting
    # near the pilot's known register changes. Tight guards give the
    # phase test a timestamp without ever inspecting held-out symbols.
    coarse=float(fit["timing_s"])
    candidates=[]
    for candidate in np.arange(coarse-.0014,coarse+.001425,.00005):
        pilot_positions=fit_phases(
            f,symbols,tones,first_s=float(candidate),time_origin_s=early,
            left_guard_s=.0001,right_guard_s=.0001,limit_symbols=PILOT)
        score=float(np.mean(pilot_positions["fit_rmse_hz"][2:PILOT]**2))
        candidates.append((score,float(candidate)))
    if not candidates:
        raise ValueError("pilot-only timing refinement unavailable")
    pilot_fit_score,phase_timing=min(candidates)
    positions=fit_phases(f,symbols,tones,first_s=phase_timing,
                         time_origin_s=early)
    result=summarize_edges(positions["phases_rad"],symbols,tones,
                           positions["amplitude_hz"],
                           positions["fit_rmse_hz"])
    with iq_path.open("rb") as fd:
        digest=hashlib.file_digest(fd,"sha256").hexdigest()
    return {
        "experiment":"Flux TX-15P phase edges from archived physical IQ",
        "rf_transmitted_by_analyzer":False,
        "target_hardware_or_source_authenticated_by_analyzer":False,
        "iq_file":str(iq_path),"iq_sha256":digest,
        "source_header":str(header),
        "source_header_sha256":hashlib.sha256(header.read_bytes()).hexdigest(),
        "sdr_raw_sample_rate_hz":RAW_FS,
        "rf_frequency_discriminator_rate_hz":PROC_FS,
        "phase_domain":"single-tone FM audio in SDR complex frequency discriminator; NOT carrier RF phase",
        "uncalibrated_sdr":True,
        "acquisition":{
            "pilot_symbols":PILOT,"heldout_symbols":48,
            "symbol_rate":TONE_BAUD,
            "training_only_timing_s":fit["timing_s"],
            "pilot_only_refined_phase_timing_s":phase_timing,
            "pilot_phase_timing_candidates":len(candidates),
            "pilot_phase_timing_fit_mse_hz2":pilot_fit_score,
            "phase_timing_uses_heldout_data":False,
            "training_correct_tones":16-fit["training_errors"],
            "heldout_correct_tones":48-fit["heldout_errors"],
            "training_timing_search":fit["training_selection"]
        },
        "relative_audio_deviation_median_hz":float(np.median(
            positions["amplitude_hz"][PILOT:])),
        "per_tone_amplitude_hz":positions["amplitude_hz"].tolist(),
        "per_tone_fit_rmse_hz":positions["fit_rmse_hz"].tolist(),
        **result,
        "interpretation":(
            "Small same-word jump proves receiver-conditioned phase tracks "
            "across repeated register writes without restarting the same "
            "tone. Changed-word apparent jumps include analog filter "
            "group delay, timing/clock mismatch, possible oscillator "
            "phase behavior and receiver response; NOT direct RF phase EVM."
        )
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--header",type=Path,default=NEW_HEADER)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("will not overwrite a prior physical IQ analysis")
    r=analyze(args.iq,args.header)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(r,indent=2)+"\n")
    print("training",r["acquisition"]["training_correct_tones"],"/16",
          "heldout",r["acquisition"]["heldout_correct_tones"],"/48")
    for k,v in r["summary"].items():
        print(k,v)
    print("saved",args.out)


if __name__=="__main__":
    main()
