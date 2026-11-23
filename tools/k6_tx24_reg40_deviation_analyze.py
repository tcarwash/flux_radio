#!/usr/bin/env python3
"""TX-24 REG40 tone-FM deviation pilot-only, heldout SDR analyzer.

Source schedule: REG70 gain24 and REG71 11264 remain unchanged while
REG40 low8 follows the source's 13 fixed 90-ms intervals. First and
third intervals are PILOT reference; later 0x16 reference repeats,
0x00 / 0x40 / 0x80 contrasts are HELDOUT. All fitting is offline.

This estimates received FM discriminator AUDIO amplitude (frequency
deviation), not actual RF power or an arbitrary RF I/Q DAC. Only a
separate prospectively locked conducted-RF run can establish silicon
response and register update bandwidth. No device access here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize_scalar

from k6_tx17c_direct_frequency_analyze import extract_iq_and_rx_frequency

WORD=11264
NOMINAL_HZ=WORD/10.32444
LOW8=(0x16,0x40,0x16,0x80,0x16,0x00,0x16,
      0x40,0x16,0x80,0x16,0x00,0x16)
PILOT=(0,2)
HELDOUT=tuple(i for i in range(len(LOW8)) if i not in PILOT)
STEP_SECONDS=.090
MARGIN_SECONDS=.019
MIN_PILOT_FM_DEVIATION_HZ=300.
MAX_REFERENCE_VARIATION=.15
MIN_ADJACENT_SCALE_SEPARATION=.035
MIN_CANDIDATE_SIGNAL_TO_NOISE=4.
MAX_HELDOUT_REFERENCE_PHASE_RMS_DEG=18.

def masks(t,onset):
    return [
        (t>onset+i*STEP_SECONDS+MARGIN_SECONDS)&
        (t<onset+(i+1)*STEP_SECONDS-MARGIN_SECONDS)
        for i in range(len(LOW8))
    ]

def _regression(y,tt,hz,extra_dc=None):
    v=np.stack((np.sin(2*np.pi*hz*tt),
                np.cos(2*np.pi*hz*tt),
                np.ones(len(tt))),axis=1)
    if extra_dc is not None:
        v=np.column_stack((v,extra_dc))
    b=np.linalg.lstsq(v,y,rcond=None)[0]
    mse=float(np.mean((y-v@b)**2))
    return mse,b

def pilot_fit(y,t,onset):
    ms=masks(t,onset)
    a,b=(ms[i] for i in PILOT)
    if a.sum()<350 or b.sum()<350:
        raise ValueError("missing T1 pilot windows")
    selected=a|b
    yy=y[selected]
    tt=t[selected]-onset
    # Separate constant receiver CFO baseline in each pilot to
    # avoid a DC step masquerading as amplitude/phase change.
    dc2=b[selected].astype(float)
    def trial(hz):
        return _regression(yy,tt,hz,dc2)
    # Two separated pilot windows can produce side lobes in the
    # frequency objective. Use a fixed deterministic coarse grid,
    # then refine its best local basin rather than trusting a global
    # golden-section search across a multimodal ±4 Hz interval.
    grid=np.linspace(NOMINAL_HZ-4,NOMINAL_HZ+4,81)
    best=float(grid[int(np.argmin([trial(x)[0] for x in grid]))])
    result=minimize_scalar(
        lambda hz:trial(hz)[0],
        bounds=(max(NOMINAL_HZ-4,best-.12),
                min(NOMINAL_HZ+4,best+.12)),
        method="bounded",options={"xatol":.00002})
    if not result.success or abs(result.x-NOMINAL_HZ)>=4:
        raise ValueError("nominal source rate out of pilot bounds")
    mse,beta=trial(float(result.x))
    amp=float(np.hypot(beta[0],beta[1]))
    if amp<MIN_PILOT_FM_DEVIATION_HZ:
        raise ValueError("insufficient pilot deviation")
    return dict(frequency_hz=float(result.x),
                oscillator_phase_rad=float(np.arctan2(beta[1],beta[0])),
                pilot_amplitude_hz=amp,
                rmse_hz=float(np.sqrt(mse)))

def score_audio(y,t,onset):
    if len(y)!=len(t):
        raise ValueError("discriminator and timestamp lengths differ")
    ms=masks(t,onset)
    pilot=pilot_fit(y,t,onset)
    hz=pilot["frequency_hz"]
    ref=pilot["pilot_amplitude_hz"]
    slots=[]
    for i,word in enumerate(LOW8):
        m=ms[i]
        if m.sum()<350:
            raise ValueError(f"not enough stable samples for slot {i}")
        tt=t[m]-onset
        yy=y[m]
        mse,beta=_regression(yy,tt,hz)
        amp=float(np.hypot(beta[0],beta[1]))
        phi=float(np.arctan2(beta[1],beta[0]))
        phase_error=float(np.rad2deg(np.angle(
            np.exp(1j*(phi-pilot["oscillator_phase_rad"])))))
        slots.append(dict(index=i,
            reg40_word=0x3500|word,
            low_byte=word,
            pilot=i in PILOT,
            amplitude_hz=amp,
            amplitude_normalized_to_pilot=amp/ref,
            phase_error_from_pilot_deg=phase_error,
            residual_rms_hz=float(np.sqrt(mse)),
            receiver_dc_frequency_hz=float(beta[2]),
            settled_samples=int(m.sum())))
    control=[s for s in slots if s["low_byte"]==0x16]
    ref_abs=np.array([s["amplitude_hz"] for s in control])
    reference_spread=float((max(ref_abs)-min(ref_abs))/
                           np.median(ref_abs))
    refs_heldout=[s for s in control if not s["pilot"]]
    phase_rms=float(np.sqrt(np.mean(
        [s["phase_error_from_pilot_deg"]**2 for s in refs_heldout])))
    amplitude_by_code={
        str(code):float(np.median([s["amplitude_hz"] for s in slots
                                    if s["low_byte"]==code]))
        for code in sorted(set(LOW8))}
    ordered=sorted(set(LOW8))
    ratios={k:v/ref for k,v in amplitude_by_code.items()}
    monotonic=all(
        amplitude_by_code[str(b)]-amplitude_by_code[str(a)]
        >= MIN_ADJACENT_SCALE_SEPARATION*ref
        for a,b in zip(ordered,ordered[1:])
    )
    candidate=[s for s in slots if s["low_byte"]!=0x16]
    significant=all(
        s["amplitude_hz"]/max(s["residual_rms_hz"],1e-9)
        >= MIN_CANDIDATE_SIGNAL_TO_NOISE for s in candidate)
    stable_reference=reference_spread<=MAX_REFERENCE_VARIATION
    stable_phase=phase_rms<=MAX_HELDOUT_REFERENCE_PHASE_RMS_DEG
    repeat_40=[s["amplitude_hz"]/ref for s in slots
               if s["low_byte"]==0x40]
    repeat_80=[s["amplitude_hz"]/ref for s in slots
               if s["low_byte"]==0x80]
    repeat_00=[s["amplitude_hz"]/ref for s in slots
               if s["low_byte"]==0x00]
    paired_stable=all(abs(a[0]-a[1])/max(np.mean(a),1e-9)<=.15
                      for a in (repeat_00,repeat_40,repeat_80))
    # This is a strict candidate-selection screen, not a certification
    # of B-chip RF capability or a regulatory spectral mask.
    conditions=dict(
        deviation_amplitude_monotonic=bool(monotonic),
        repeated_baseline_stable=bool(stable_reference),
        repeated_baseline_phase_stable=bool(stable_phase),
        repeated_nonbaseline_levels_stable=bool(paired_stable),
        settled_candidates_snr_sufficient=bool(significant))
    return dict(
        study="TX24 REG40 low8 isolated tone FM deviation",
        pilot_only_frequency_phase_and_amplitude_fit=pilot,
        trained_on_indices=list(PILOT),
        heldout_indices=list(HELDOUT),
        frozen_reg40_low8=list(LOW8),
        no_heldout_used_for_parameter_fit=True,
        source_oscillator_word=WORD,
        source_tone_nominal_hz=NOMINAL_HZ,
        fixed_reg70_gain=24,
        dwell_s=STEP_SECONDS,
        settle_margin_s=MARGIN_SECONDS,
        rf_power_or_carrier_iq_dac_not_proven=True,
        reference_spread_fraction=reference_spread,
        baseline_reference_heldout_phase_rms_deg=phase_rms,
        ratios_by_lowbyte=ratios,
        paired_repeat_ratios=dict(
            low0=repeat_00,low40=repeat_40,low80=repeat_80),
        thresholds=dict(
            min_adjacent_separation_fraction=MIN_ADJACENT_SCALE_SEPARATION,
            max_reference_variation_fraction=MAX_REFERENCE_VARIATION,
            min_candidate_snr=MIN_CANDIDATE_SIGNAL_TO_NOISE,
            max_reference_phase_rms_deg=MAX_HELDOUT_REFERENCE_PHASE_RMS_DEG,
            max_pair_variation_fraction=.15),
        conditions=conditions,
        candidate_deviation_actuator_qualified=all(conditions.values()),
        slots=slots,
        warning=("No REG40 B-chip physical response is established by "
                 "synthetic discriminator examples; requires independent "
                 "conducted-RF complex IQ. REG40 gain controls the RF FM "
                 "frequency deviation, NOT the RF transmitter amplitude.")
    )

def analyze_file(raw:Path):
    z,y,t,magnitude,discovery=extract_iq_and_rx_frequency(raw)
    r=score_audio(y,t[:-1],discovery["onset_s"])
    with raw.open("rb") as fd:
        sha=hashlib.file_digest(fd,"sha256").hexdigest()
    return dict(source_iq=str(raw),source_sha256=sha,
                analysis_only=True,rf_hardware_access=False,
                physical_Bchip_evidence_unverified=True,
                rf_onset=discovery,score=r)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite existing evidence")
    r=analyze_file(args.iq)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(r,indent=2)+"\n")
    print("TX24 qualified candidate",r["score"]["candidate_deviation_actuator_qualified"])
    print("ratios",r["score"]["ratios_by_lowbyte"])
    print("saved",args.out)

if __name__=="__main__":
    main()
