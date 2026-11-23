#!/usr/bin/env python3
"""TX-15V B-chip REG70 gate experiment: phase free-run vs freeze vs reset.

This offline receiver uses actual raw complex RF IQ to recover FM AUDIO
tone phase and envelope. It does *not* measure absolute carrier IQ phase
EVM or prove any arbitrary RF phase DAC. Source tone is never changed:
REG71=11264; only REG70 toggles enable during a single TX lease.

Design locked prior to physical RF:
  calibrated ON windows 0 and 1; ON windows 2,3,4 are HELD OUT.
  four OFF gaps 57,71,83,97 ms with 170-ms ON each.
  Compare: free-running oscillator through OFF, frozen oscillator
  during OFF, or oscillator restarted at the same phase on every ON.
  Estimate only shared tone rate and pilot phase from ON0/ON1;
  do NOT fit model selection parameters against heldout phases.
READ ONLY, no serial/SDR hardware/flash access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

from k6_tx17c_direct_frequency_analyze import extract_iq_and_rx_frequency
from k6_tx15s_rf_analyze import FS, TIME_ORIGIN

WORD=11264
NOMINAL_AUDIO_HZ=WORD/10.32444
OFF_S=(.057,.071,.083,.097)
ON_S=.170
INTRO_S=.200
END_S=.100
MARGIN_S=.013
MAX_RATE_ERR_HZ=10.0
MIN_DEVIATION_RMS_RATIO=4.0
MODEL_MAX_RMS_DEG=35.0
MODEL_MIN_OTHER_RMS_GAP_DEG=30.0

def windows(onset:float):
    on=[(onset,onset+INTRO_S)]
    off=[]
    t=onset+INTRO_S
    for gap in OFF_S:
        off.append((t,t+gap))
        t+=gap
        on.append((t,t+ON_S))
        t+=ON_S
    off.append((t,t+END_S))
    return on,off

def one_segment_fit(f,t,low,high,freq):
    margin=min(MARGIN_S,(high-low)*.20)
    mask=(t>low+margin)&(t<high-margin)
    if mask.sum()<1000:raise ValueError("too few audio phase samples")
    tt=t[mask]-t[0]
    yy=f[mask]
    v=np.stack((np.sin(2*np.pi*freq*tt),
                np.cos(2*np.pi*freq*tt),
                np.ones(len(tt))),axis=1)
    c=np.linalg.lstsq(v,yy,rcond=None)[0]
    amp=float(np.hypot(c[0],c[1]))
    phi=float(np.arctan2(c[1],c[0]))
    rmse=float(np.sqrt(np.mean((yy-v@c)**2)))
    return dict(amplitude_hz=amp,global_phase_rad=phi,
                sinusoid_fit_rms_hz=rmse,dc_frequency_hz=float(c[2]),
                n_samples=int(mask.sum()))

def estimate_pilot_rate(f,t,on):
    lo=NOMINAL_AUDIO_HZ-MAX_RATE_ERR_HZ
    hi=NOMINAL_AUDIO_HZ+MAX_RATE_ERR_HZ
    def score(hz):
        rms=[one_segment_fit(f,t,a,b,hz) for a,b in on[:2]]
        return sum(r["sinusoid_fit_rms_hz"]**2 * r["n_samples"]
                   for r in rms)
    solved=minimize_scalar(score,bounds=(lo,hi),
                           method="bounded",
                           options={"xatol":.00005})
    if not solved.success:raise ValueError("pilot tone-rate fit failed")
    freq=float(solved.x)
    # Bound the estimate; no retuning the radio or fitting on heldout.
    if not lo<freq<hi:raise ValueError("pilot tone-rate outside allowed bounds")
    return freq

def circular_delta(x):
    return float(np.angle(np.exp(1j*x)))

def rms_deg(xs):
    q=np.rad2deg(np.asarray(xs,float))
    return float(np.sqrt(np.mean(q*q)))

def score_audio_phase(f,t,onset):
    on,off=windows(onset)
    # Actual complex-RF FM discriminator audio: no raw SDR PLL re-tune.
    hz=estimate_pilot_rate(f,t,on)
    estimates=[one_segment_fit(f,t,a,b,hz) for a,b in on]
    off_results=[]
    for a,b in off:
        lo=a+MARGIN_S
        hi=b-MARGIN_S
        mask=(t>lo)&(t<hi)
        if mask.sum()<500:raise ValueError("OFF plateau too short")
        v=f[mask]
        off_results.append(dict(
            audio_rms_ac_hz=float(np.std(v)),
            rf_dc_hz=float(np.median(v)),
            n_samples=int(mask.sum())))
    on_amplitude=float(np.median([x["amplitude_hz"]
                                  for x in estimates]))
    off_rms=float(np.median([x["audio_rms_ac_hz"]
                              for x in off_results]))
    signal_qualified=on_amplitude>off_rms*MIN_DEVIATION_RMS_RATIO
    if not signal_qualified:
        raise ValueError("REG70 gating amplitude ON/OFF not qualified")
    phi1=estimates[1]["global_phase_rad"]
    begin1=on[1][0]
    outputs={}
    for model in ("free_running","frozen_during_off","reset_on_enable"):
        errors=[]
        predicted=[]
        for k in range(2,5):
            if model=="free_running":
                phi=phi1
            elif model=="frozen_during_off":
                phi=phi1-2*np.pi*hz*sum(OFF_S[1:k])
            else:
                phi=phi1-2*np.pi*hz*(on[k][0]-begin1)
            predicted.append(float(np.rad2deg(circular_delta(phi))))
            errors.append(circular_delta(estimates[k]["global_phase_rad"]-phi))
        outputs[model]=dict(
            heldout_starting_phases_deg=[float(v) for v in predicted],
            residuals_heldout_deg=[float(np.rad2deg(e)) for e in errors],
            heldout_phase_rms_deg=rms_deg(errors),
            heldout_phase_median_abs_deg=float(np.median(
                abs(np.rad2deg(np.asarray(errors))))),
            calibration_uses_on0_frequency_and_on1_phase_only=True,
            heldout_on_windows=[2,3,4],
        )
    ranking=sorted(outputs,key=lambda k:outputs[k]["heldout_phase_rms_deg"])
    best=outputs[ranking[0]]["heldout_phase_rms_deg"]
    second=outputs[ranking[1]]["heldout_phase_rms_deg"]
    qualified=bool(best<=MODEL_MAX_RMS_DEG and
                   second-best>=MODEL_MIN_OTHER_RMS_GAP_DEG)
    return dict(
        audio_tone_word=WORD,
        nominal_audio_frequency_hz=NOMINAL_AUDIO_HZ,
        pilot_only_fitted_audio_frequency_hz=hz,
        on0_and_on1_only_for_calibration=True,
        pilot_initial_global_tone_phase_deg=float(
            np.rad2deg(estimates[0]["global_phase_rad"])),
        pilot_after_first_gate_global_tone_phase_deg=float(
            np.rad2deg(phi1)),
        on_regions=[dict(start_s=float(a),end_s=float(b),**values)
                    for (a,b),values in zip(on,estimates)],
        off_regions=[dict(start_s=float(a),end_s=float(b),**values)
                     for (a,b),values in zip(off,off_results)],
        on_median_audio_deviation_hz=on_amplitude,
        off_median_audio_ac_rms_hz=off_rms,
        audio_on_off_envelope_verified=signal_qualified,
        models=outputs,
        best_model_exploratory_label=ranking[0],
        phase_model_qualified=qualified,
        next_best_rms_gap_deg=float(second-best),
        confidence_note=("The model ranking uses heldout ON2–ON4 phase. "
            "No feedback or exact shared clock; it is not a calibrated "
            "absolute RF-phase control input."),
    )

def analyze_file(raw:Path):
    z,f,t,amp,discovery=extract_iq_and_rx_frequency(raw)
    # Complex-RF sample positions are 64k/s. f has one fewer sample.
    result=score_audio_phase(f,t[:-1],discovery["onset_s"])
    with raw.open("rb") as fp:
        digest=hashlib.file_digest(fp,"sha256").hexdigest()
    return dict(
        study="Flux TX15V BK4829 REG70 phase gating physical RF bench",
        raw_rf_iq_source=str(raw),
        raw_rf_iq_sha256=digest,
        physical_tone_phase_measured_from_fm_discriminator=True,
        independent_carrier_iq_phase_dac_proven=False,
        read_only=True,rf_capture=discovery,
        gate_phase=result,
        limitation=("Frequency discriminator audio phase is not direct "
                    "RF-carrier phase control; pilot phase-model"
                    " hypotheses must be tested across independent runs.")
    )

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing overwrite")
    result=analyze_file(args.iq)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+"\n")
    s=result["gate_phase"]
    print("Pilot tone rate",round(s["pilot_only_fitted_audio_frequency_hz"],4),
          "Hz; audio ON/OFF RMS",round(s["on_median_audio_deviation_hz"],1),
          round(s["off_median_audio_ac_rms_hz"],1))
    print("Heldout phase model scores",{
        k:round(v["heldout_phase_rms_deg"],2)
        for k,v in s["models"].items()})
    print("qualified",s["phase_model_qualified"],
          "best",s["best_model_exploratory_label"])
    print("saved",args.out)

if __name__=="__main__":
    main()
