#!/usr/bin/env python3
"""TX-15S: SDR FM-audio study with predeclared DISTRIBUTED pilot references.

Offline only. A fixed 192-word 150/s waveform has four disjoint 18-word
pilots, 114 heldout tone slots, and six unscored terminal guard slots.
Timebase (global and piecewise) and frequency-dependent apparent phase
delay are learned from PILOT STRONG EDGES ONLY, never from holdout.
Scores are recovered FM discriminator AUDIO, NOT independent carrier
RF-IQ control. Uses a known eight-tone waveform (not blind decoding).
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np

from k6_tx15r_rf_analyze import (
    FS,T,BAUD,TIME_ORIGIN,ANALYSIS_END,STRONG_HZ,WINDOW,
    pilot_start as acquire_source,
    find_strong_edges as _unused,  # different source annotation here
    symbol_energies,
    summarize_rad,
)
import k6_tx15r_rf_analyze as base
from k6_tx_tone_rf_analyze import demod_raw
from k6_tx15_phase_rf_analyze import wrap_phase

N=192
PILOT_WINDOWS=((0,18),(48,66),(96,114),(156,174))
GUARD_START=186
PILOT_INDICES=tuple(i for a,b in PILOT_WINDOWS for i in range(a,b))
HOLDOUT_INDICES=tuple(i for i in range(GUARD_START)
                      if i not in PILOT_INDICES)
HEADER=Path(__file__).resolve().parents[1]/"c/k6/mod/tx8_distributed_phase_probe.h"

def region(index:int)->str:
    if not 0<=index<N:raise ValueError("invalid slot")
    if index>=GUARD_START:return "guard"
    if index in PILOT_INDICES:return "pilot"
    return "heldout"

def edge_region(index:int)->str:
    """No edge crossing a pilot/heldout boundary belongs to either."""
    if not 1<=index<N:raise ValueError("invalid edge")
    a=region(index);b=region(index-1)
    return a if a==b else "boundary"

def read_vector(header:Path=HEADER):
    source=header.read_text()
    for constraint in ("#define TX8_NS 192u",
                       "#define TX8_PILOT_SLOTS 72u",
                       "#define TX8_TAIL_GUARD 6u"):
        if constraint not in source:raise ValueError("unexpected TX15S vector safety metadata")
    def arr(name):
        match=re.search(r"static const uint(?:8|16)_t\s+"+name+
                        r"\[\w+\]\s*=\s*\{([^}]+)\}",source,re.S)
        if not match:raise ValueError("missing vector table "+name)
        return np.asarray([int(s) for s in re.findall(r"\d+",match[1])],
                          dtype=int)
    words,ids=arr("tx8_words"),arr("tx8_symbols")
    if (len(words)!=8 or len(ids)!=N or
        np.any(words!=np.array([5183,8074,11264,14702,17479,20515,23467,26007])) or
        any(x<0 or x>=8 for x in ids) or
        any(ids[i]!=0 for i in range(GUARD_START,N))):
        raise ValueError("TX15S source failed exact table/terminal guard checks")
    motif=[0,7,1,6,2,5]
    for a,b in PILOT_WINDOWS:
        if ids[a:b].tolist()!=sum(([x]*3 for x in motif),[]):
            raise ValueError("distributed pilots differ; pair offsets no longer comparable")
    return words,ids


def find_edges(f,ids,freq,start_hint,*,origin=TIME_ORIGIN):
    """Source-labelled high-contrast transition time, no use of holdout for fit.

    Uses the R analysis's filtered FM-midpoint detector but reassigns
    pilot/heldout based on *both* adjacent slots.
    """
    # The old detector assumes N=192 and uses nominal start_hint+i*T, but
    # its training label i<48 was changed here to our independent regions.
    strong=base.find_strong_edges(f,ids,freq,start_hint,origin=origin)
    for e in strong:
        e["region"]=edge_region(e["index"])
    return strong


def schedule_from_pilot(edges):
    refs=[e for e in edges if e["region"]=="pilot" and e["found"]]
    held=[e for e in edges if e["region"]=="heldout" and e["found"]]
    if len(refs)<16:raise ValueError("fewer than sixteen usable distributed pilot midpoints")
    refs_idx=np.array([e["index"] for e in refs],float)
    refs_t=np.array([e["midpoint_time_s"] for e in refs],float)
    slope,intercept=np.polyfit(refs_idx,refs_t,1)
    if not .99*T<slope<1.01*T:raise ValueError("global pilot timing outside 1%")
    global_model=intercept+np.arange(N)*slope
    knots=[]
    for a,b in PILOT_WINDOWS:
        local=[e for e in refs if a<e["index"]<b]
        if len(local)<4:
            raise ValueError(f"pilot window [{a},{b}) has only {len(local)} qualified edges")
        # Same 5 tone pairs in each window; interpolation of these
        # pilot center timestamps holds analogue edge biases nearly fixed.
        ids0=np.asarray([e["index"] for e in local],float)
        times=np.asarray([e["midpoint_time_s"] for e in local],float)
        knots.append((float(np.mean(ids0)),float(np.mean(times)),len(local)))
    x=np.array([k[0] for k in knots])
    y=np.array([k[1] for k in knots])
    if any(np.diff(x)<=0) or any(np.diff(y)<=0):raise ValueError("unordered pilot anchors")
    values=np.arange(N,dtype=float)
    piecewise=np.interp(values,x,y)
    # Predeclared: linearly extrapolate from first / last two pilot knots
    piecewise[values<x[0]]=y[0]+(values[values<x[0]]-x[0])*(
        (y[1]-y[0])/(x[1]-x[0]))
    piecewise[values>x[-1]]=y[-1]+(values[values>x[-1]]-x[-1])*(
        (y[-1]-y[-2])/(x[-1]-x[-2]))
    if np.any(np.diff(piecewise)<.99*T) or np.any(np.diff(piecewise)>1.01*T):
        raise ValueError("piecewise pilot schedule not monotonic within +/-1%")
    hids=np.asarray([e["index"] for e in held],dtype=int)
    ht=np.asarray([e["midpoint_time_s"] for e in held],float)
    if len(hids)<20:raise ValueError("fewer than 20 independent heldout timing references")
    err_global=(ht-global_model[hids])*1e6
    err_piecewise=(ht-piecewise[hids])*1e6
    err_nominal=(ht-(np.mean(refs_t-refs_idx*T)+hids*T))*1e6
    def metrics(v):
        return dict(rms_us=float(np.sqrt(np.mean(v*v))),
                    median_abs_us=float(np.median(abs(v))),
                    max_abs_us=float(max(abs(v))))
    return dict(
        pilot_only_fit=True,
        pilot_edges=len(refs),
        pilot_edge_indices=refs_idx.astype(int).tolist(),
        heldout_strong_edges=len(held),
        unscored_guard_edges=sum(e["region"]=="guard" for e in edges),
        missing_pilot_strong_edges=sum(e["region"]=="pilot" and not e["found"] for e in edges),
        linear_observed_update_rate_hz=float(1/slope),
        linear_schedule_skew_ppm_vs_sdr=float((slope/T-1)*1e6),
        pilot_knots=[dict(index=k[0],time_s=k[1],n_edges=k[2]) for k in knots],
        heldout_nominal_150_hz=metrics(err_nominal),
        heldout_global_linear=metrics(err_global),
        heldout_piecewise_linear=metrics(err_piecewise),
    ),global_model,piecewise


def fit_audio_per_slot(f,ids,freq,schedule,*,origin=TIME_ORIGIN):
    phase=[];amp=[];rmse=[]
    for i,k in enumerate(ids):
        begin=float(schedule[i])
        period=float(schedule[i+1]-begin) if i<N-1 else float(schedule[i]-schedule[i-1])
        lo=int(round((begin+.0010-origin)*FS))
        hi=int(round((begin+period-.00065-origin)*FS))
        if lo<0 or hi>len(f) or hi-lo<128:
            raise ValueError(f"audio fit {i} outside capture")
        t=np.arange(lo,hi)/FS+origin-begin
        f0=float(freq[k])
        design=np.column_stack((
            np.sin(2*np.pi*f0*t),np.cos(2*np.pi*f0*t),np.ones(len(t))))
        v=np.linalg.lstsq(design,f[lo:hi],rcond=None)[0]
        amp.append(float(np.hypot(*v[:2])))
        phase.append(float(np.arctan2(v[1],v[0])))
        rmse.append(float(np.sqrt(np.mean((f[lo:hi]-design@v)**2))))
    return np.asarray(phase),np.asarray(amp),np.asarray(rmse)


def all_phase_edges(phases,amp,rmse,ids,freq,schedule):
    edges=[]
    for i in range(1,N):
        prev=int(ids[i-1]);cur=int(ids[i])
        step=float(schedule[i]-schedule[i-1])
        jump=float(wrap_phase(phases[i]-phases[i-1]-2*np.pi*freq[prev]*step))
        edges.append(dict(
            index=i,region=edge_region(i),
            before=prev,after=cur,
            same_word=prev==cur,
            delta_audio_hz=float(freq[cur]-freq[prev]),
            apparent_audio_phase_jump_rad=jump,
            fit_qualified=bool(min(amp[i],amp[i-1])>=300 and
                               max(rmse[i],rmse[i-1])<=250)
        ))
    return edges


def pilot_delay_phase(edges):
    train=[e for e in edges if e["region"]=="pilot" and
           e["fit_qualified"] and not e["same_word"]]
    if len(train)<16:raise ValueError("not enough distributed changed-word pilot edges")
    delta=np.asarray([e["delta_audio_hz"] for e in train])
    angle=np.asarray([e["apparent_audio_phase_jump_rad"] for e in train])
    # Exactly one fitted effective transfer delay (no heldout tuning).
    grid=np.linspace(-.00025,.00025,1001)
    residual=wrap_phase(angle[:,None]-2*np.pi*delta[:,None]*grid[None,:])
    scores=np.mean(residual**2,axis=0)
    selected=int(np.argmin(scores))
    return dict(
        pilot_only=True,fit_pilot_changed_word_edges=len(train),
        effective_audio_phase_delay_s=float(grid[selected]),
        pilot_phase_residual_rms_deg=float(np.rad2deg(np.sqrt(scores[selected]))),
    )


def phase_score(edges,delay):
    same=[];changed_raw=[];changed=[]
    for e in edges:
        if e["region"]!="heldout" or not e["fit_qualified"]:continue
        if e["same_word"]:
            same.append(e["apparent_audio_phase_jump_rad"])
        else:
            changed_raw.append(e["apparent_audio_phase_jump_rad"])
            changed.append(float(wrap_phase(
                e["apparent_audio_phase_jump_rad"]-
                2*np.pi*e["delta_audio_hz"]*delay)))
    return dict(
        heldout_same_word_audio_phase=summarize_rad(same),
        heldout_changed_word_uncorrected=summarize_rad(changed_raw),
        heldout_changed_word_pilot_delay_corrected=summarize_rad(changed)
    )


def tone_score(f,ids,freq,schedule):
    errors=[];ratios=[];pilot_good=0;held_good=0
    for i,k in enumerate(ids):
        if region(i)=="guard":continue
        begin=float(schedule[i])
        period=float(schedule[i+1]-schedule[i])
        scores=symbol_energies(f,freq,begin,0,period=period)
        decided=int(np.argmax(scores));ok=decided==k
        if region(i)=="pilot":pilot_good+=int(ok)
        else:held_good+=int(ok)
        if not ok:errors.append(dict(index=i,region=region(i),
                        expected=int(k),detected=decided))
        if region(i)=="heldout":
            ratios.append(float(scores[k]/max(np.max(np.delete(scores,k)),1e-7)))
    return dict(
        pilot_correct=pilot_good,pilot_total=72,
        heldout_correct=held_good,heldout_total=114,
        unscored_guard_slots=6,
        wrong_symbols=errors,
        heldout_median_expected_vs_best_other_ratio=float(np.median(ratios)),
    )


def analyze_file(iq:Path,header:Path=HEADER):
    words,ids=read_vector(header)
    freq=words/10.32444
    f,carrier=demod_raw(iq,early_s=TIME_ORIGIN,late_s=ANALYSIS_END)
    acquired=acquire_source(f,ids,freq)
    measured=find_edges(f,ids,freq,acquired["start_hint_s"])
    clock,global_t,distributed_t=schedule_from_pilot(measured)
    out={}
    for name,schedule in (("global_linear_pilot",global_t),
                          ("distributed_piecewise_pilot",distributed_t)):
        phase,amp,rmse=fit_audio_per_slot(f,ids,freq,schedule)
        event=all_phase_edges(phase,amp,rmse,ids,freq,schedule)
        learned=pilot_delay_phase(event)
        out[name]=dict(
            tone=tone_score(f,ids,freq,schedule),
            audio_phase=phase_score(event,learned["effective_audio_phase_delay_s"]),
            pilot_delay=learned,
            phase_edges=event,
            terminal_guard_fit_rms_hz=[float(x) for x in rmse[GUARD_START:]],
            pilot_phase_fit_rms_hz=float(np.median(rmse[np.array(PILOT_INDICES)])),
            heldout_phase_fit_rms_hz=float(np.median(rmse[np.array(HOLDOUT_INDICES)])),
        )
    with iq.open("rb") as source:
        digest=hashlib.file_digest(source,"sha256").hexdigest()
    return dict(
        experiment="Flux TX-15S physically recovered audio-frequency discriminator",
        audio_phase_not_direct_rf_carrier_phase=True,
        arbitrary_rf_iq_transmitter_proven=False,
        source_iq=str(iq),source_iq_sha256=digest,
        source_header=str(header),source_header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
        offline_only=True,pilot_windows=[list(x) for x in PILOT_WINDOWS],
        pilot_symbols=72,heldout_symbols=114,unscored_guard_symbols=6,
        all_timebase_and_phase_fits_train_on_pilot_only=True,
        nominal_symbol_rate=BAUD,
        acquisition=acquired,
        pilot_clock_model=clock,
        fit_comparison=out,
        caution="Two RF-domain clock devices and analog TX/RX modulation filtering; neither source phase nor absolute RF carrier IQ EVM is independently established."
    )


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--header",type=Path,default=HEADER)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing overwrite")
    report=analyze_file(args.iq,args.header)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    model=report["pilot_clock_model"]
    print("pilot clock edges",model["pilot_edges"],
          "heldout clock checks",model["heldout_strong_edges"])
    for typ in ("heldout_global_linear","heldout_piecewise_linear"):
        print(typ,model[typ])
    for modelname,score in report["fit_comparison"].items():
        print(modelname,"tone holdout",score["tone"]["heldout_correct"],"/114",
              "changed audio-phase RMS",score["audio_phase"][
                  "heldout_changed_word_pilot_delay_corrected"]["rms_deg"],
              "same-word RMS",score["audio_phase"][
                  "heldout_same_word_audio_phase"]["rms_deg"])
    print("saved",args.out)


if __name__=="__main__":
    main()
