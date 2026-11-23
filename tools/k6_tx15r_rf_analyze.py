#!/usr/bin/env python3
"""TX-15R: long-pilot recovered-audio phase transfer from saved K6 SDR IQ.

READ ONLY. Source: saved, air-coupled FM RF IQ, NOT carrier RF phase.
192 source writes at 150/s; first 48 are public pilot only. All timing,
clock, average phase transfer and optional repeated-pair corrections must
be learned within those first 48. Remaining 144 tone decisions and phase
transitions are held out. Do not claim arbitrary coherent RF IQ.

This is a fixed waveform-specific scientific measurement, not a blind
modem decoder. TX/RX filter and RTL-SDR biases are not separately identified.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, hilbert, sosfiltfilt

import k6_tx_tone_rf_analyze as old
import k6_tx15_phase_rf_analyze as phase
import k6_tx15_transition_clock as clock

N=192
PILOT=48
BAUD=150
FS=64_000
T=1/BAUD
TIME_ORIGIN=.65
ANALYSIS_END=2.55
STRONG_HZ=450.
WINDOW=.00375
HEADER=Path(__file__).resolve().parents[1]/"c/k6/mod/tx8_long_phase_probe.h"

def read_vector(path:Path=HEADER):
    source=path.read_text()
    if not re.search(r"#define\s+TX8_NS\s+192u",source) or not re.search(
            r"#define\s+TX8_PILOT\s+48u",source):
        raise ValueError("expected fixed 192-event, 48-event pilot source")
    def arr(name):
        m=re.search(r"static const uint(?:8|16)_t\s+"+name+
                    r"\[\w+\]\s*=\s*\{([^}]+)\}",source,re.S)
        if not m: raise ValueError(f"no {name} array")
        return np.asarray([int(v) for v in re.findall(r"\d+",m[1])],
                          dtype=np.int32)
    words,ids=arr("tx8_words"),arr("tx8_symbols")
    if (len(words)!=8 or len(ids)!=N or any(words!=np.array(
        [5183,8074,11264,14702,17479,20515,23467,26007])) or
        np.any(ids<0) or np.any(ids>=8)):
        raise ValueError("unexpected long vector symbols or tones")
    return words,ids


def symbol_energies(f,frequency,first_s,index,*,period=T,origin=TIME_ORIGIN):
    begin=first_s+index*period
    l=round((begin+.0010-origin)*FS)
    r=round((begin+period-.00055-origin)*FS)
    if l<0 or r>len(f) or r-l<128:
        raise ValueError(f"tone slot {index} outside captured FM audio")
    sig=f[l:r].astype(np.float64)
    sig=sig-np.mean(sig)
    t=np.arange(l,r,dtype=float)/FS
    return np.array([2*abs(np.mean(sig*np.exp(-2j*np.pi*hz*t)))
                     for hz in frequency])


def pilot_start(f,ids,frequency,*,first=.860,last=.923,spacing=.00025):
    """Select RF audio symbol-start hint using only first 16 known pilot IDs."""
    candidates=[]
    for start in np.arange(first,last,spacing):
        hits=0;ratios=[]
        for index in range(1,16):  # omit the uncertain startup symbol
            energy=symbol_energies(f,frequency,start,index)
            expected=int(ids[index])
            hits+=int(np.argmax(energy)==expected)
            ratios.append(float(energy[expected]/
                                max(np.max(np.delete(energy,expected)),1e-9)))
        candidates.append((hits,float(np.median(ratios)),float(start)))
    if not candidates:raise ValueError("no acquisition times")
    wins,margin,start=max(candidates,key=lambda row:(row[0],row[1]))
    if wins<13:raise ValueError(f"pilot acquisition ambiguous ({wins}/15)")
    return dict(start_hint_s=start,first15_correct=wins,
                first15_median_true_vs_competitor_ratio=margin,
                timing_candidates=len(candidates),
                first_pilot_symbols_for_acquisition=16)


def find_strong_edges(f,ids,frequency,first_hint,*,origin=TIME_ORIGIN):
    sig=sosfiltfilt(butter(4,[200,3500],fs=FS,btype="bandpass",
                              output="sos"),f)
    analytic=hilbert(sig)
    instantaneous=np.angle(analytic[1:]*np.conj(analytic[:-1]))*FS/(2*np.pi)
    smoothed=uniform_filter1d(instantaneous,size=65)
    events=[]
    for i in range(1,N):
        bef=float(frequency[ids[i-1]]);aft=float(frequency[ids[i]])
        if abs(aft-bef)<STRONG_HZ:continue
        pred=first_hint+i*T
        lo=int((pred-WINDOW-origin)*FS)
        hi=int((pred+WINDOW-origin)*FS)
        if lo<0 or hi>len(smoothed):
            raise ValueError(f"strong edge {i} outside FM audio window")
        local=smoothed[lo:hi]
        candidate=np.flatnonzero(np.diff(np.sign(local-(bef+aft)/2))!=0)
        if not len(candidate):
            # No invented midpoint; record missing separately.
            events.append(dict(index=i,pilot=i<PILOT,found=False,
                               before=int(ids[i-1]),after=int(ids[i])))
            continue
        ticks=(candidate+lo)/FS+origin
        winner=int(np.argmin(abs(ticks-pred)))
        events.append(dict(index=i,pilot=i<PILOT,found=True,
                           before=int(ids[i-1]),after=int(ids[i]),
                           before_hz=bef,after_hz=aft,
                           midpoint_time_s=float(ticks[winner]),
                           nominal_time_s=float(pred)))
    return events


def fit_pilot_clock(edges):
    pilot=[e for e in edges if e["pilot"] and e["found"]]
    hold=[e for e in edges if not e["pilot"] and e["found"]]
    if len(pilot)<12:
        raise ValueError(f"pilot has only {len(pilot)} strong usable transition midpoints")
    ids=np.asarray([e["index"] for e in pilot],float)
    times=np.asarray([e["midpoint_time_s"] for e in pilot])
    # Pre-declared ordinary OLS, not optimized against the holdout.
    period,first=np.polyfit(ids,times,1)
    if not .99*T<period<1.01*T:
        raise ValueError("measured symbol interval outside qualified +/-1%")
    residual=(times-(first+ids*period))*1e6
    heldids=np.asarray([e["index"] for e in hold],float)
    heldtimes=np.asarray([e["midpoint_time_s"] for e in hold])
    held=(heldtimes-(first+heldids*period))*1e6
    nominal_start=float(np.mean(times-ids*T))
    naive=(heldtimes-(nominal_start+heldids*T))*1e6
    return dict(
        pilot_clock_fits_heldout=False,
        pilot_strong_events=len(pilot),
        pilot_event_indices=ids.astype(int).tolist(),
        heldout_strong_events=len(hold),
        pilot_start_s=float(first),
        pilot_period_s=float(period),
        pilot_observed_update_rate_hz=float(1/period),
        pilot_relative_sdr_schedule_ppm=float((period/T-1)*1e6),
        pilot_midpoint_rms_us=float(np.sqrt(np.mean(residual**2))),
        pilot_midpoint_max_abs_us=float(max(abs(residual))),
        heldout_midpoint_rms_us=float(np.sqrt(np.mean(held**2))),
        heldout_midpoint_median_abs_us=float(np.median(abs(held))),
        heldout_midpoint_max_abs_us=float(max(abs(held))),
        heldout_nominal150_midpoint_rms_us=float(np.sqrt(np.mean(naive**2))),
        heldout_nominal150_midpoint_median_abs_us=float(np.median(abs(naive))),
        missed_pilot_events=sum(e["pilot"] and not e["found"] for e in edges),
        missed_heldout_events=sum(not e["pilot"] and not e["found"] for e in edges)
    )


def slot_audio_fit(f,ids,freq,first,period,*,origin=TIME_ORIGIN):
    phase_rad=[];amp=[];rmse=[]
    for i,k in enumerate(ids):
        begin=first+i*period
        lo=int(round((begin+.0010-origin)*FS))
        hi=int(round((begin+period-.00065-origin)*FS))
        if lo<0 or hi>len(f) or hi-lo<128:
            raise ValueError(f"slot {i} outside capture")
        t=np.arange(lo,hi)/FS+origin-begin
        y=f[lo:hi]
        sine=np.column_stack((np.sin(2*np.pi*freq[k]*t),
                              np.cos(2*np.pi*freq[k]*t),np.ones(len(t))))
        c=np.linalg.lstsq(sine,y,rcond=None)[0]
        phase_rad.append(float(np.arctan2(c[1],c[0])))
        amp.append(float(np.hypot(*c[:2])))
        rmse.append(float(np.sqrt(np.mean((y-sine@c)**2))))
    return np.array(phase_rad),np.array(amp),np.array(rmse)


def phase_edges(phase_rad,amp,rmse,ids,freq,period):
    edges=[]
    for i in range(1,N):
        prev=int(ids[i-1]);cur=int(ids[i])
        delta=float(phase.wrap_phase(
            phase_rad[i]-phase_rad[i-1]-2*np.pi*freq[prev]*period))
        good=bool(min(amp[i],amp[i-1])>=300 and
                  max(rmse[i],rmse[i-1])<=250)
        edges.append(dict(index=i,region="pilot" if i<PILOT else "heldout",
                          before=prev,after=cur,changed=prev!=cur,
                          delta_audio_hz=float(freq[cur]-freq[prev]),
                          apparent_audio_phase_jump_rad=delta,
                          fit_qualified=good))
    return edges


def summarize_rad(values):
    if len(values)==0:return {"n":0}
    values=np.asarray(values,float)
    return dict(n=len(values),
                rms_deg=float(np.sqrt(np.mean(np.rad2deg(values)**2))),
                median_abs_deg=float(np.median(abs(np.rad2deg(values)))),
                circular_coherence=float(abs(np.mean(np.exp(1j*values)))))


def fit_phase_lag(edges):
    pilot=[e for e in edges if e["region"]=="pilot" and
           e["changed"] and e["fit_qualified"]]
    if len(pilot)<12:
        raise ValueError(f"need at least 12 good pilot tone transitions, have {len(pilot)}")
    df=np.asarray([e["delta_audio_hz"] for e in pilot])
    obs=np.asarray([e["apparent_audio_phase_jump_rad"] for e in pilot])
    grid=np.linspace(-.00025,.00025,1001)
    errs=phase.wrap_phase(obs[:,None]-2*np.pi*df[:,None]*grid[None,:])
    rms=np.mean(errs*errs,axis=0)
    selected=int(np.argmin(rms))
    lag=float(grid[selected])
    return dict(pilot_only=True,pilot_changes=len(pilot),
                pilot_effective_audio_lag_s=lag,
                pilot_residual_rms_deg=float(np.rad2deg(np.sqrt(rms[selected]))))


def pair_residuals(edges,lag):
    """Optional repeated-pair model. Only pair groups with >=2 pilot hits."""
    pilot=[e for e in edges if e["region"]=="pilot" and
           e["changed"] and e["fit_qualified"]]
    group={}
    for e in pilot:
        key=(e["before"],e["after"])
        residual=float(phase.wrap_phase(
            e["apparent_audio_phase_jump_rad"]-
            2*np.pi*e["delta_audio_hz"]*lag))
        group.setdefault(key,[]).append(residual)
    learned={}
    for (a,b),values in group.items():
        if len(values)>=2:
            learned[(a,b)]=float(np.angle(np.mean(np.exp(1j*np.asarray(values)))))
    return learned


def evaluate_phases(edges,lag,paired):
    raw={};clocked={};delay={};pair_corrected={}
    unseen=[];seen=[]
    for e in edges:
        if e["region"]!="heldout" or not e["fit_qualified"]:
            continue
        kind="changed" if e["changed"] else "same_word"
        raw.setdefault(kind,[]).append(e["apparent_audio_phase_jump_rad"])
        if not e["changed"]:continue
        resid=float(phase.wrap_phase(e["apparent_audio_phase_jump_rad"]-
                                  2*np.pi*e["delta_audio_hz"]*lag))
        delay.setdefault(kind,[]).append(resid)
        key=(e["before"],e["after"])
        if key in paired:
            seen.append(float(phase.wrap_phase(resid-paired[key])))
        else:
            unseen.append(resid)
    return dict(
        heldout_same_word=summarize_rad(raw.get("same_word",[])),
        heldout_changed_word_clock_only=summarize_rad(raw.get("changed",[])),
        heldout_changed_word_clock_and_one_delay=summarize_rad(delay.get("changed",[])),
        heldout_same_pair_revisit_corrected=summarize_rad(seen),
        heldout_unseen_pair_one_delay=summarize_rad(unseen),
        pair_correction_training_fits=len(paired),
    )


def score_symbols(f,ids,freq,first,period):
    detected=[];ratios=[]
    for i,k in enumerate(ids):
        energy=symbol_energies(f,freq,first,i,period=period)
        detected.append(int(np.argmax(energy)))
        ratios.append(float(energy[k]/max(np.max(np.delete(energy,k)),1e-6)))
    correct=np.equal(detected,ids)
    return dict(
        pilot_correct=int(sum(correct[:PILOT])),
        heldout_correct=int(sum(correct[PILOT:])),
        heldout_total=N-PILOT,
        heldout_errors=[
            {"index":i,"expected":int(ids[i]),"detected":int(detected[i])}
            for i in range(PILOT,N) if not correct[i]
        ],
        heldout_median_true_vs_best_other_ratio=float(np.median(ratios[PILOT:])),
        heldout_confusion=[[int(sum((ids[PILOT:]==i)&(np.array(detected[PILOT:])==j)))
                            for j in range(8)] for i in range(8)]
    )


def analyze_file(raw_iq:Path,header:Path=HEADER)->dict:
    words,ids=read_vector(header)
    freq=words/10.32444
    f,carrier=old.demod_raw(raw_iq,early_s=TIME_ORIGIN,late_s=ANALYSIS_END)
    anchor=pilot_start(f,ids,freq)
    strong=find_strong_edges(f,ids,freq,anchor["start_hint_s"])
    clockfit=fit_pilot_clock(strong)
    model=slot_audio_fit(f,ids,freq,clockfit["pilot_start_s"],
                         clockfit["pilot_period_s"])
    edge=phase_edges(*model,ids,freq,clockfit["pilot_period_s"])
    lag=fit_phase_lag(edge)
    pair=pair_residuals(edge,lag["pilot_effective_audio_lag_s"])
    audio=evaluate_phases(edge,lag["pilot_effective_audio_lag_s"],pair)
    tone=score_symbols(f,ids,freq,clockfit["pilot_start_s"],
                       clockfit["pilot_period_s"])
    deviations=[]
    for k in range(8):
        mask=(ids==k)&(np.arange(N)>=PILOT)&(model[2]<250)
        if not mask.any():
            raise ValueError("no heldout modulation/deviation data for tone index "+str(k))
        values=model[1][mask]
        deviations.append(dict(tone_index=k,audio_tone_hz=float(freq[k]),
                               qualified_heldout_symbols=int(np.sum(mask)),
                               median_rf_discriminator_deviation_hz=float(np.median(values)),
                               iqr_rf_discriminator_deviation_hz=float(
                                   np.percentile(values,75)-np.percentile(values,25))))
    with raw_iq.open("rb") as fref:
        digest=hashlib.file_digest(fref,"sha256").hexdigest()
    return dict(
        experiment="Flux TX-15R 192-event long pilot audio-tone phase transfer",
        source_iq=str(raw_iq),source_iq_sha256=digest,
        header=str(header),header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
        offline_only=True,physical_rf_transmitted_by_analysis=False,
        audio_tone_phase_not_rf_carrier_phase=True,
        independent_rf_iq_evm_not_measured=True,
        pilot_symbols=PILOT,heldout_symbols=N-PILOT,
        clock_and_phase_training_uses_heldout=False,
        receiver="uncalibrated air-coupled RTL-SDR, 1.024 MS/s raw, 64 kS/s FM discriminator",
        nominal_rate_hz=BAUD,full_schedule_s=N/BAUD,
        acquisition=anchor,clock_training_and_holdout=clockfit,
        audio_transfer_lag=lag,audio_phase_heldout=audio,
        tone_decoder=tone,
        uncalibrated_heldout_tone_deviation_response=deviations,
        learned_pilot_pair_offsets=[dict(before=a,after=b,pilot_offset_deg=float(np.rad2deg(v)))
                                      for (a,b),v in sorted(pair.items())],
        tone_amplitudes_hz=[float(x) for x in model[1]],
        tone_fit_rms_hz=[float(x) for x in model[2]],
        strong_edge_events=strong,
        phase_edge_events=edge,
        caution="Clock/phase training from pilot only; no arbitrary RF IQ, calibrated clock or distinct TX/RX filter identified."
    )


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--header",type=Path,default=HEADER)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing overwrite")
    score=analyze_file(args.iq,args.header)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(score,indent=2)+"\n")
    clock=score["clock_training_and_holdout"]
    print("pilot clock",clock["pilot_strong_events"],"events; rate",
          round(clock["pilot_observed_update_rate_hz"],3),"Hz; heldout timing MAD",
          round(clock["heldout_midpoint_median_abs_us"],1),"us")
    print("heldout tone",score["tone_decoder"]["heldout_correct"],"/",
          score["tone_decoder"]["heldout_total"])
    for key,value in score["audio_phase_heldout"].items():
        print(key,value)
    print("saved",args.out)


if __name__=="__main__":
    main()
