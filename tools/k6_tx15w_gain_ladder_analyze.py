#!/usr/bin/env python3
"""TX-15W REG70 13-slot gain ladder, FM-deviation and phase retention.

Offline, physical SDR complex-RF IQ -> FM discriminator recovered AUDIO.
FIXED prior to physical RF: pilot source gain24 at slots 0 and 2,
heldout: 8,16,32,48,64,80, and five further gain24 references.
13x85ms continuous PA, REG71 11264 unchanged.
Fit one common audio oscillator frequency and phase ONLY from pilot
slots 0/2 (both gain24); independently score amplitude and phase for
each settled gain plateau. Uncalibrated SDR composite FM response,
not measured transmitter spectrum or independent RF carrier IQ DAC.
"""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar

from k6_tx17c_direct_frequency_analyze import extract_iq_and_rx_frequency
from k6_tx15s_rf_analyze import FS,TIME_ORIGIN

GAINS=(24,8,24,16,24,32,24,48,24,64,24,80,24)
WORD=11264
FREQ_NOMINAL=WORD/10.32444
SLOT_S=.085
TAIL_S=.100
MARGIN_S=.014
PILOT=(0,2)
HELDOUT=tuple(range(13))[3:]
MIN_AMPLITUDE_HZ=200.
MAX_CONTROL_VARIATION_FRACTION=.15
MIN_RATIO_SEPARATION=.05
MAX_CONTROL_PHASE_RMS_DEG=18.

def masks(t,onset):
    m=[]
    for i in range(len(GAINS)):
        a=onset+i*SLOT_S
        b=a+SLOT_S
        m.append((t>=a+MARGIN_S)&(t<=b-MARGIN_S))
    return m

def pilot_rate_phase(f,t,onset):
    ms=masks(t,onset)
    sel=ms[0]|ms[2]
    t_s=t[sel]-onset
    y=f[sel]
    # Each pilot window may have a slightly different receiver DC.
    idx=np.where(ms[2][sel],1.,0.)
    def fit(hz):
        V=np.stack((np.sin(2*np.pi*hz*t_s),
                    np.cos(2*np.pi*hz*t_s),
                    np.ones(len(t_s)),idx),axis=1)
        c=np.linalg.lstsq(V,y,rcond=None)[0]
        err=y-V@c
        return float(np.mean(err*err)),c
    sol=minimize_scalar(lambda h:fit(h)[0],
       bounds=(FREQ_NOMINAL-4,FREQ_NOMINAL+4),
       method="bounded",options={"xatol":.00005})
    if not sol.success:raise ValueError("frequency fit did not converge")
    hz=float(sol.x);loss,c=fit(hz)
    if abs(hz-FREQ_NOMINAL)>=4:
        raise ValueError("source frequency outside allowed range")
    if float(np.hypot(*c[:2]))<MIN_AMPLITUDE_HZ:
        raise ValueError("insufficient pilot FM deviation")
    return hz,float(np.arctan2(c[1],c[0])),float(np.hypot(*c[:2])),float(np.sqrt(loss))

def score_audio(f,t,onset):
    if len(f)!=len(t):raise ValueError("f/t must match")
    masks_=masks(t,onset)
    hz,phase0,pilot_amp,pilot_rmse=pilot_rate_phase(f,t,onset)
    slots=[]
    for i,m in enumerate(masks_):
        if m.sum()<1800:raise ValueError("missing plateau "+str(i))
        tm=t[m]-onset
        y=f[m]
        V=np.stack((np.sin(2*np.pi*hz*tm),
                    np.cos(2*np.pi*hz*tm),np.ones(len(tm))),axis=1)
        c=np.linalg.lstsq(V,y,rcond=None)[0]
        amp=float(np.hypot(c[0],c[1]))
        phi=float(np.arctan2(c[1],c[0]))
        residual=float(np.rad2deg(np.angle(np.exp(1j*(phi-phase0)))))
        slots.append(dict(
            index=i,gain=GAINS[i],
            pilot=i in PILOT,
            amplitude_hz=amp,
            amplitude_normalized_to_pilot24=amp/pilot_amp,
            phase_error_from_pilot_deg=residual,
            recovered_dc_hz=float(c[2]),
            recovered_sine_fit_rmse_hz=float(np.sqrt(np.mean((y-V@c)**2))),
            samples=int(m.sum())
        ))
    ctr=[s for s in slots if s["gain"]==24]
    ctrl_amp=np.array([s["amplitude_hz"] for s in ctr])
    control_median=float(np.median(ctrl_amp))
    ctr_spread=float((max(ctrl_amp)-min(ctrl_amp))/control_median)
    gains={g:float(np.median([s["amplitude_hz"] for s in slots
                                if s["gain"]==g])) for g in sorted(set(GAINS))}
    sorted_gains=sorted(gains)
    ratios={str(g):float(gains[g]/control_median) for g in sorted_gains}
    monotonic=all(
        gains[b]-gains[a]>=control_median*MIN_RATIO_SEPARATION
        for a,b in zip(sorted_gains,sorted_gains[1:]))
    ctr_phase=np.array([s["phase_error_from_pilot_deg"] for s in ctr[2:]])
    ctr_phase_rms=float(np.sqrt(np.mean(ctr_phase**2)))
    all_phase=np.array([s["phase_error_from_pilot_deg"]
                        for s in slots if not s["pilot"]])
    all_phase_rms=float(np.sqrt(np.mean(all_phase**2)))
    return dict(
        gain_codes=list(GAINS),
        source_tone_word=WORD,
        fit_pilot_slots=list(PILOT),
        pilot_only_fitted_hz=hz,
        pilot_only_phase_reference_deg=float(np.rad2deg(phase0)),
        pilot_gain24_amplitude_hz=pilot_amp,
        pilot_audio_fit_rmse_hz=pilot_rmse,
        plateau_ms=SLOT_S*1000,
        phase_training_uses_heldout=False,
        gain24_median_deviation_hz=control_median,
        gain24_max_min_spread_fraction=ctr_spread,
        gain_deviation_normalized_to_24=ratios,
        gain_values_monotonic_with_at_least_5pct_separation=bool(monotonic),
        repeated_gain24_amplitude_stable=bool(
            ctr_spread<=MAX_CONTROL_VARIATION_FRACTION),
        postpilot_control_phase_rms_deg=ctr_phase_rms,
        heldout_all_gain_phase_rms_deg=all_phase_rms,
        repeated_gain24_phase_stable=bool(
            ctr_phase_rms<=MAX_CONTROL_PHASE_RMS_DEG),
        qualifies_as_phase_preserving_monotonic_gain=bool(
            monotonic and ctr_spread<=MAX_CONTROL_VARIATION_FRACTION
            and ctr_phase_rms<=MAX_CONTROL_PHASE_RMS_DEG),
        gain_phase_pairs=slots,
        caution=("Relative FM-discriminator tone amplitude includes "
                 "TX/RX filtering and SDR calibration; no absolute RF carrier IQ amplitude.")
    )

def analyze_file(raw:Path):
    z,f,t,a,discovery=extract_iq_and_rx_frequency(raw)
    result=score_audio(f,t[:-1],discovery["onset_s"])
    with raw.open("rb") as fi:
        digest=hashlib.file_digest(fi,"sha256").hexdigest()
    return dict(
        study="TX15W REG70 gain ladder audio FM deviation and source oscillator phase",
        source_iq=str(raw),source_iq_sha256=digest,
        direct_rf_carrier_phase_dac_proven=False,
        new_carrier_iq_phase_trajectory_measured=False,
        read_only=True,rf_acquisition=discovery,score=result
    )

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing overwrite")
    r=analyze_file(args.iq)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(r,indent=2)+"\n")
    s=r["score"]
    print("pilotHz",round(s["pilot_only_fitted_hz"],4),
          "gain ratios", {k:round(v,3) for k,v in
              s["gain_deviation_normalized_to_24"].items()})
    print("control gain amplitude range %",
          round(s["gain24_max_min_spread_fraction"]*100,2),
          "control phase RMS",round(s["postpilot_control_phase_rms_deg"],2))
    print("qualified",s["qualifies_as_phase_preserving_monotonic_gain"])
    print("saved",args.out)

if __name__=="__main__":
    main()
