#!/usr/bin/env python3
"""TX-15S real complex-RF phase prediction from known REG_71 FM tone program.

Offline, no radio/SDR access. Separately reconstructs 64-kS/s complex
RF IQ from archived 1.024-MS/s RTL-SDR samples. Only the FOUR
predeclared pilot windows supply model parameters: linear RF carrier
frequency offset, RF constant phase, two FM oscillator quadrature
components, and a six-tone RX-conditioned deviation-vs-tone response.

Then predicts *unaltered* complex RF phase samples in the 114 heldout
tone slots and compares to an unmodulated carrier baseline. This does
measure a real RF-IQ phase trajectory, but not independently commandable
arbitrary RF-IQ modulation, absolute phase, calibrated RF output EVM,
or removal of SDR/RX bias. No phase fits on heldout samples.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import resample_poly

from k6_tx_tone_rf_analyze import FS_RAW,FS_PROC,SHIFT_HZ
from k6_tx15s_rf_analyze import (
    read_vector,HEADER,N,FS,T,GUARD_START,
    PILOT_INDICES,region,
    acquire_source,find_edges,schedule_from_pilot,fit_audio_per_slot,
    TIME_ORIGIN,ANALYSIS_END
)

def complex_downmix(raw_path:Path,*,early=TIME_ORIGIN,late=ANALYSIS_END):
    data=np.load(raw_path,mmap_mode="r",allow_pickle=False)
    if data.ndim!=1 or data.dtype!=np.uint8 or data.size%2:
        raise ValueError("raw SDR IQ must be interleaved uint8")
    lo=int(round(early*FS_RAW));hi=int(round(late*FS_RAW))
    if hi>data.size//2:raise ValueError("recording too short")
    dat=data[2*lo:2*hi]
    c=(dat[::2].astype(np.float32)-127.5) + 1j*(
        dat[1::2].astype(np.float32)-127.5)
    c*=np.exp(2j*np.pi*SHIFT_HZ*np.arange(len(c))/FS_RAW)
    iq=resample_poly(c,1,FS_RAW//FS_PROC)
    f=np.angle(iq[1:]*np.conj(iq[:-1]))*FS/(2*np.pi)
    f=uniform_filter1d(f,size=8)
    return iq,f


def pilot_tone_response(f,ids,freq,schedule):
    phases,amp,rmse=fit_audio_per_slot(f,ids,freq,schedule)
    known={}
    train=np.asarray(PILOT_INDICES)
    for k in sorted(set(ids[train])):
        indices=train[ids[train]==k]
        values=[float(amp[i]) for i in indices if rmse[i]<250 and amp[i]>300]
        if len(values)<5:raise ValueError(f"too few clean pilot amplitudes for tone {k}")
        known[int(k)]=float(np.median(values))
    ix=sorted(known)
    values=np.interp(freq,freq[ix],[known[k] for k in ix])
    if not np.all(values>300):raise ValueError("invalid pilot deviation model")
    return values,dict(
        pilot_only=True,observed_tone_indices=ix,
        interpolated_tone_indices=sorted(set(range(8))-set(ix)),
        fitted_deviations_hz_per_audio_tone=[float(v) for v in values],
        pilot_slot_fit_rms_hz=float(np.median(rmse[train])))


def continuous_audio_quadratures(time,schedule,ids,freq,deviation):
    """Two integrated RF-phase basis vectors of a continuous-phase FM tone.

    theta is radio's hypothetical internal oscillator-phase trajectory
    given only exact tone words, programmer slot schedule and an
    arbitrary initial oscillator phase. No received symbol phases.
    """
    if np.any(np.diff(schedule)<=0):
        raise ValueError("invalid scheduled symbol times")
    step=np.diff(schedule)
    slot=np.searchsorted(schedule,time,side="right")-1
    slot=np.clip(slot,0,N-1)
    prefix=np.r_[0,np.cumsum(2*np.pi*freq[ids[:-1]]*step)]
    theta=prefix[slot]+2*np.pi*freq[ids[slot]]*(time-schedule[slot])
    a=deviation[ids[slot]]
    gate=(time>=schedule[0])&(time<schedule[N-1]+T)
    cc=np.cumsum(np.where(gate,a*np.cos(theta),0))*2*np.pi/FS
    ss=np.cumsum(np.where(gate,a*np.sin(theta),0))*2*np.pi/FS
    return cc,ss


def training_and_holdout_masks(time,schedule,carrier_mag=None,*,stride=8):
    """Skip all pilot/heldout boundary and guard samples before fitting."""
    which=np.searchsorted(schedule,time,side="right")-1
    which=np.clip(which,0,N-1)
    valid=np.asarray([region(int(i)) for i in which],dtype=object)
    begin=schedule[which]
    stop=np.r_[schedule[1:],schedule[-1]+T][which]
    inside=(time>begin+.001)&(time<stop-.00065)
    if carrier_mag is not None:
        # Fixed (not holdout-optimized) magnitude check.
        inside&=np.asarray(carrier_mag)>1.5
    inside&=np.arange(len(time))%stride==0
    return inside&(valid=="pilot"),inside&(valid=="heldout")


def linear_fit_predict(obs,time,bases,train,test,*,carrier_poly_degree=1):
    """A fair null (no modulation) or a pilot-frozen FM phase model.

    obs is UNWRAPPED complex-RF phase. The residual is scored as a
    *circular* complex phase difference, not an ordinary unwrapped RMS
    that can confuse phase cycles.
    """
    if sum(train)<250 or sum(test)<250:raise ValueError("insufficient clean RF samples")
    if carrier_poly_degree not in (1,2):
        raise ValueError("only predeclared linear/quadratic RF LO frequency drift")
    dtime=time-time[train].mean()
    cols=[np.ones(len(time)),dtime]
    if carrier_poly_degree==2:
        cols.append(dtime*dtime)
    cols.extend(bases)
    X=np.stack(cols,axis=1)
    coefs=np.linalg.lstsq(X[train],obs[train],rcond=None)[0]
    predicted=X@coefs
    def metrics(mask,alternative_prediction=None):
        pred=predicted if alternative_prediction is None else alternative_prediction
        residual=np.angle(np.exp(1j*(obs[mask]-pred[mask])))
        return dict(
            n_samples=int(mask.sum()),
            phase_rms_deg=float(np.sqrt(np.mean(np.rad2deg(residual)**2))),
            median_abs_phase_deg=float(np.median(abs(np.rad2deg(residual)))),
            circular_coherence=float(abs(np.mean(np.exp(1j*residual)))),
            circular_bias_deg=float(np.rad2deg(np.angle(np.mean(np.exp(1j*residual)))))
        )
    # Explicit counterfactual: invert *only* the learned FM portion
    # after freezing every pilot coefficient. The pilot is never fit to
    # this wrong-sign hypothesis and the heldout is never fitted at all.
    inverted=None
    if bases:
        fm=coefs[-len(bases):]@X[:,-len(bases):].T
        inverted=predicted-2*fm
    return dict(
        coefficients=[float(v) for v in coefs],
        fitted_pilot_only=True,
        estimated_rf_cfo_hz=float(coefs[1]/(2*np.pi)),
        rf_cfo_drift_hz_per_s=float(coefs[2]/np.pi) if carrier_poly_degree==2 else 0.,
        carrier_poly_degree=carrier_poly_degree,
        fm_quadrature_norm=float(np.hypot(*coefs[-2:])) if bases else None,
        pilot=metrics(train),
        heldout=metrics(test),
        inverted_modulation_heldout_diagnostic=metrics(test,inverted) if inverted is not None else None,
    )


def analyze_file(iq_path:Path,header:Path=HEADER):
    words,ids=read_vector(header)
    freq=words/10.32444
    iq,f=complex_downmix(iq_path)
    time=TIME_ORIGIN+np.arange(len(iq))/FS
    anchor=acquire_source(f,ids,freq)
    edges=find_edges(f,ids,freq,anchor["start_hint_s"])
    clock,global_t,piece_t=schedule_from_pilot(edges)
    observed_phase=np.unwrap(np.angle(iq))
    results={}
    for name,schedule in (
        ("global_linear",global_t),
        ("distributed_piecewise",piece_t)
    ):
        deviation,cal=pilot_tone_response(f,ids,freq,schedule)
        c,s=continuous_audio_quadratures(time,schedule,ids,freq,deviation)
        training,heldout=training_and_holdout_masks(time,schedule,abs(iq))
        no_fm=linear_fit_predict(
            observed_phase,time,[],training,heldout,carrier_poly_degree=2)
        linear_fm=linear_fit_predict(
            observed_phase,time,[c,s],training,heldout,carrier_poly_degree=1)
        quadratic_fm=linear_fit_predict(
            observed_phase,time,[c,s],training,heldout,carrier_poly_degree=2)
        results[name]=dict(
            pilot_response=cal,
            rf_iq_carrier_only_null_quadratic_cfo=no_fm,
            rf_iq_programmed_fm_linear_cfo_negative_control=linear_fm,
            rf_iq_programmed_continuous_fm_quadratic_cfo=quadratic_fm,
            model_hypothesis="single uninterrupted REG71 audio oscillator phase with pilot-derived deviation, RF phase, RF CFO and first-order RF CFO drift",
            heldout_complex_coherence_improvement=float(
                quadratic_fm["heldout"]["circular_coherence"]-
                no_fm["heldout"]["circular_coherence"]),
        )
    with iq_path.open("rb") as fp:
        digest=hashlib.file_digest(fp,"sha256").hexdigest()
    return dict(
        study="TX-15S predicted physical RF carrier phase from programmed continuous-phase FM",
        offline_only=True,
        actual_rf_iq_phase_measurement=True,
        independently_commandable_arbitrary_rf_iq=False,
        pilot_only_training=True,
        synthetic_modulation_assumption="continuous REG71 AUDIO phase across changes",
        carrier_iq_receiver_conditioned=True,
        source_iq=str(iq_path),source_iq_sha256=digest,
        vector_source_header=str(header),
        vector_header_sha256=hashlib.sha256(header.read_bytes()).hexdigest(),
        total_word_events=N,pilot_words=72,heldout_words=114,guard_words=6,
        pilot_windows=[[a,b] for a,b in
            ((0,18),(48,66),(96,114),(156,174))],
        source_acquisition=anchor,pilot_clock_training=clock,
        model_comparison=results,
        model_selection_on_first_two_captures_is_exploratory=True,
        inverted_fm_diagnostic_added_after_three_captures=True,
        frozen_quadratic_rf_cfo_model_for_independent_future_run=True,
        limitations=[
            "RF phase predictions fit 4 parameters on disjoint pilot IQ samples; heldout never tunes this model.",
            "RF-LO carrier offset and phase are nuisance calibration, not direct transmitter phase control.",
            "FM deviation amplitudes are uncalibrated SDR-conditioned and fitted only on six pilot tones.",
            "FM audio oscillator continuity is a hypothesis, not a proven internal chip implementation.",
            "Synthesis of this particular 8-tone FM modulation is not general I/Q or arbitrary QAM/OFDM."
        ],
    )


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--header",type=Path,default=HEADER)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing overwrite")
    report=analyze_file(args.iq,args.header)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    for key,entry in report["model_comparison"].items():
        null=entry["rf_iq_carrier_only_null_quadratic_cfo"]["heldout"]
        model=entry["rf_iq_programmed_continuous_fm_quadratic_cfo"]["heldout"]
        print(key,"RF IQ heldout baseline coherence",round(null["circular_coherence"],4),
              "FM predicted coherence",round(model["circular_coherence"],4),
              "RF IQ phase RMS",round(model["phase_rms_deg"],2),
              "FM scale",round(entry["rf_iq_programmed_continuous_fm_quadratic_cfo"]["fm_quadrature_norm"],3))
    print("saved",args.out)

if __name__=="__main__":
    main()
