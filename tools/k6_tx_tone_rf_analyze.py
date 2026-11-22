#!/usr/bin/env python3
"""Offline RF test of the K6 programmable tone-oscillator TX entry point.

The 64 programmed tones are at 450..2550 Hz, 300-Hz spacing,
150 symbol/s in the attended RF capture. Each symbol modulates the
FM carrier through the chip's tone oscillator (REG_70/71). It is NOT
direct eight-level constant-frequency RF FSK and not arbitrary I/Q.

The first 16 expected symbols are used ONLY for timing acquisition.
The remaining 48 are entirely held out for tone decision evaluation.
No device, serial, SDR driver, firmware writing, PTT or flash calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly

FS_RAW=1_024_000
FS_PROC=64_000
SHIFT_HZ=280_500.0
SYMBOL_BAUD=150
TONES=np.arange(8,dtype=int)*300+450
TRAIN_SYMBOLS=16
N_SYMBOLS=64


def symbol_truth(header: Path) -> list[int]:
    src=header.read_text()
    match=re.search(r"tx8_symbols\[TX8_NS\]\s*=\s*\{([^}]+)\}",src,re.S)
    if not match:
        raise ValueError("no TX8 symbol table in header")
    s=[int(z) for z in re.findall(r"\d+",match.group(1))]
    if len(s)!=N_SYMBOLS or any(not 0<=x<8 for x in s):
        raise ValueError("TX8 truth length or alphabet mismatch")
    return s


def demod_raw(path: Path, *, early_s=.8, late_s=1.4) -> tuple[np.ndarray,np.ndarray]:
    data=np.load(path,mmap_mode="r",allow_pickle=False)
    if data.ndim!=1 or data.dtype!=np.uint8 or data.size%2:
        raise ValueError("want interleaved U8 complex SDR IQ")
    if data.size<2*int(late_s*FS_RAW):
        raise ValueError("capture too short")
    low,high=round(early_s*FS_RAW),round(late_s*FS_RAW)
    r=data[2*low:2*high]
    x=(r[::2].astype(np.float32)-127.5)+1j*(r[1::2].astype(np.float32)-127.5)
    x*=np.exp(2j*np.pi*SHIFT_HZ*np.arange(len(x))/FS_RAW)
    dec=resample_poly(x,1,FS_RAW//FS_PROC)
    f=np.angle(dec[1:]*np.conj(dec[:-1]))*FS_PROC/(2*np.pi)
    f=uniform_filter1d(f,size=8)
    return f,np.abs(dec)


def classify_symbol(f:np.ndarray, time_origin_s:float,
                    start_s:float, *, baud=SYMBOL_BAUD,
                    fs=FS_PROC) -> tuple[np.ndarray,int]:
    """Evaluate all tone frequencies in the interior of one time slot."""
    left=int((start_s+.0010-time_origin_s)*fs)
    right=int((start_s+1/baud-.00055-time_origin_s)*fs)
    if left<0 or right>len(f) or right-left<128:
        raise ValueError("symbol window outside recording")
    sig=f[left:right].astype(np.float64)
    sig-=np.mean(sig)
    times=np.arange(left,right,dtype=np.float64)/fs
    energies=np.array([2*abs(np.mean(sig*np.exp(-2j*np.pi*hz*times)))
                       for hz in TONES])
    return energies,int(np.argmax(energies))


def evaluate(f:np.ndarray, truth:list[int], t0:float, *,
             time_origin_s=.8,train=TRAIN_SYMBOLS,
             baud=SYMBOL_BAUD)->dict:
    if len(truth)!=N_SYMBOLS or train<8 or train>=N_SYMBOLS:
        raise ValueError("invalid reference symbol sequence")
    decision=[]
    ratios=[]
    true_tone_magnitude=[]
    all_scores=[]
    for index,expected in enumerate(truth):
        energy,pred=classify_symbol(f,time_origin_s,t0+index/baud,
                                    baud=baud)
        decision.append(pred)
        best_other=float(max(np.delete(energy,expected)))
        true_tone_magnitude.append(round(float(energy[expected]),3))
        ratios.append(float(energy[expected]/max(best_other,1e-7)))
        all_scores.append(energy)
    hits=[int(a==b) for a,b in zip(decision,truth)]
    return dict(training_errors=train-sum(hits[:train]),
                training_symbols=train,
                heldout_errors=(N_SYMBOLS-train)-sum(hits[train:]),
                heldout_symbols=N_SYMBOLS-train,
                predictions=decision,
                training_correct=hits[:train],
                heldout_correct=hits[train:],
                true_tone_amplitude_hz_relative=true_tone_magnitude,
                training_median_true_vs_best_other_ratio=float(
                    np.median(ratios[:train])),
                heldout_median_true_vs_best_other_ratio=float(
                    np.median(ratios[train:])),
                true_tone_vs_best_other_ratio=ratios,
                confusion_heldout=[
                    [sum(1 for i in range(train,N_SYMBOLS)
                         if truth[i]==a and decision[i]==b)
                     for b in range(8)] for a in range(8)],
                timing_s=t0)


def fit_training_only(f:np.ndarray, truth:list[int], *,
                      time_origin_s=.8, start=.877,
                      end=.914, spacing=.00015)->dict:
    # Candidate selection must not inspect a held-out symbol.
    candidates=[]
    for t0 in np.arange(start,end,spacing):
        predicted=[]
        margins=[]
        for i in range(TRAIN_SYMBOLS):
            scores,winner=classify_symbol(
                f,time_origin_s,t0+i/SYMBOL_BAUD)
            predicted.append(winner)
            true=int(truth[i])
            second=max(np.delete(scores,true))
            margins.append(float(scores[true]/max(second,1e-6)))
        accuracy=sum(int(x==y) for x,y in zip(predicted,truth[:TRAIN_SYMBOLS]))
        ratio=float(np.median(margins[1:]))
        candidates.append((accuracy,ratio,float(t0)))
    _,ratio,t0=max(candidates,key=lambda c:(c[0],c[1]))
    result=evaluate(f,truth,t0,time_origin_s=time_origin_s)
    result["training_selection"]={
        "candidate_count":len(candidates),
        "selected_on_first_symbols_only":TRAIN_SYMBOLS,
        "search_window_s":[start,end],
        "grid_step_s":spacing,
        "best_training_correct":TRAIN_SYMBOLS-result["training_errors"],
        "selection_median_margin_ratio":ratio}
    return result


def analyze_capture(iq_path:Path,header_path:Path)->dict:
    f,amp=demod_raw(iq_path)
    truth=symbol_truth(header_path)
    result=fit_training_only(f,truth)
    with iq_path.open("rb") as fd:
        digest=hashlib.file_digest(fd,"sha256").hexdigest()
    return {
        "study":"Flux independent RFIC tone-oscillator FM TX",
        "capture_path":str(iq_path),
        "capture_sha256":digest,
        "ground_truth_header":str(header_path),
        "tone_hz":TONES.tolist(),
        "programmed_symbol_baud":SYMBOL_BAUD,
        "notes":[
            "The chip's tone oscillator sends audio FM modulation; not an RF 8-FSK plateau or general I/Q transmitter.",
            "Only first 16 symbols used for timing and threshold-free tone selection. No heldout decisions fed back into timing.",
            "First/early symbols can be truncated by RF PA startup.",
            "No absolute RF amplitude/power or modulation deviation calibration.",
            "The FM discriminator is receiver-conditioned and the analysis uses known alphabet of eight audio tones."
        ],
        "offline_only":True,
        "rf_tx_initiated":False,
        "bandwidth_or_emissions_approval":False,
        "carrier_amplitude_adc_median_in_analysis_window":round(
            float(np.median(amp)),4),
        "result":result}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--header",type=Path,default=Path("c/k6/mod/tx8_probe.h"))
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite existing analysis")
    try:
        result=analyze_capture(args.iq,args.header)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2)+"\n")
    except (OSError,ValueError,TypeError) as e:
        p.error(str(e))
    r=result["result"]
    print("timing train",r["training_symbols"]-r["training_errors"],
          "/",r["training_symbols"],
          "heldout",r["heldout_symbols"]-r["heldout_errors"],
          "/",r["heldout_symbols"])
    print("heldout median tone-score ratio:",round(
        r["heldout_median_true_vs_best_other_ratio"],3))
    print("Saved",args.out)


if __name__=="__main__":
    main()
