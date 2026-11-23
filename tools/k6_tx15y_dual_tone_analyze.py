#!/usr/bin/env python3
"""TX-15Y: pilot-only spectral test for REG72 dual-tone FM, OFFLINE ONLY.

The nine source intervals test ordinary tone1, DTMF tone1, DTMF tone2,
BOTH tones and OFF. Pilot slots 0/1 determine one oscillator frequency
and reference FM deviation, while all dual-tone decisions are HELD OUT.
REG72 means native-FSK baud outside DTMF mode: never touch FIFO work.
FM discriminator values are RECEIVED frequency deviation, NOT RF power
or an arbitrary carrier-IQ waveform.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.optimize import minimize_scalar
from k6_tx17c_direct_frequency_analyze import extract_iq_and_rx_frequency

F1_WORD,F2_WORD=8074,14702
F1_HZ=F1_WORD/10.32444
F2_HZ=F2_WORD/10.32444
DURATIONS=(.120,.120,.120,.120,.080,.120,.120,.120,.090)
STATES=("BEFORE_MODE_T1","DTMF_T1","DTMF_T2","DTMF_BOTH",
        "OFF","DTMF_T2_AGAIN","DTMF_T1_AGAIN","DTMF_BOTH_AGAIN","GUARD_OFF")
PILOT=(0,1)
HELDOUT=(2,3,4,5,6,7,8)
MARGIN=.018
MIN_PILOT_HZ=300.
MIN_T2_REL=.20
MIN_BOTH_REL=.20
MAX_LEAK=.20
MAX_OFF=.20
MAX_T2_REPEAT_CHANGE=.25

def windows(onset):
    marks=[]
    for name,duration in zip(STATES,DURATIONS):
        marks.append((onset,onset+duration,name))
        onset+=duration
    return marks

def mask(t,a,b):
    return (t>a+MARGIN)&(t<b-MARGIN)

def one_fit(y,t,f1,f2):
    if len(y)<350 or len(y)!=len(t):
        raise ValueError("missing settled audio window")
    u=t-t[0]
    X=np.stack((np.sin(2*np.pi*f1*u),np.cos(2*np.pi*f1*u),
                np.sin(2*np.pi*f2*u),np.cos(2*np.pi*f2*u),
                np.ones(len(u))),axis=1)
    beta=np.linalg.lstsq(X,y,rcond=None)[0]
    return dict(tone1_hz_deviation=float(np.hypot(*beta[:2])),
                tone2_hz_deviation=float(np.hypot(*beta[2:4])),
                dc_hz=float(beta[4]),
                residual_rms_hz=float(np.sqrt(np.mean((y-X@beta)**2))),
                samples=len(y))

def pilot_frequency(f,t,onset):
    w=windows(onset)
    masks=[mask(t,*w[i][:2]) for i in PILOT]
    if any(m.sum()<500 for m in masks):
        raise ValueError("missing pilot 0 or 1")
    # Each pilot has its own sine/cosine/DC; DTMF mode transition
    # may change phase and DC. Only common tone1 frequency is fitted.
    def cost(hz):
        total=0.
        for m in masks:
            y=f[m];u=t[m]-t[m][0]
            X=np.stack((np.sin(2*np.pi*hz*u),
                        np.cos(2*np.pi*hz*u),np.ones(len(u))),axis=1)
            b=np.linalg.lstsq(X,y,rcond=None)[0]
            total+=np.mean((y-X@b)**2)
        return float(total)
    fit=minimize_scalar(cost,
        bounds=(F1_HZ-3,F1_HZ+3),method="bounded",
        options={"xatol":.0001})
    if not fit.success or abs(fit.x-F1_HZ)>=3:
        raise ValueError("pilot rate outside nominal ±3 Hz")
    return float(fit.x)

def score_audio(f,t,onset):
    if len(f)!=len(t):
        raise ValueError("audio/timestamp mismatch")
    f1=pilot_frequency(f,t,onset)
    f2=F2_HZ*(f1/F1_HZ)
    parts=[]
    for i,(a,b,name) in enumerate(windows(onset)):
        m=mask(t,a,b)
        fit=one_fit(f[m],t[m],f1,f2)
        fit.update(index=i,state=name,pilot=i in PILOT)
        parts.append(fit)
    ref=.5*(parts[0]["tone1_hz_deviation"]+
            parts[1]["tone1_hz_deviation"])
    if min(parts[i]["tone1_hz_deviation"] for i in PILOT)<MIN_PILOT_HZ:
        raise ValueError("no qualifying tone1 pilot reference in BOTH pilot arms")
    ratio=lambda i,k:parts[i][f"tone{k}_hz_deviation"]/ref
    t2=[ratio(i,2) for i in (2,5)]
    conditions=dict(
        tone2_heldout_present=min(t2)>=MIN_T2_REL,
        tone1_leak_absent_in_tone2=max(ratio(2,1),ratio(5,1))<=MAX_LEAK,
        dual_tone_two_lines_both_segments=min(
            ratio(3,1),ratio(3,2),ratio(7,1),ratio(7,2))>=MIN_BOTH_REL,
        tone1_replay_present_and_tone2_absent=(
            ratio(6,1)>=.6 and ratio(6,2)<=MAX_LEAK),
        both_tones_off_in_guards=max(
            ratio(4,1),ratio(4,2),ratio(8,1),ratio(8,2))<=MAX_OFF,
        tone2_replay_stable=abs(t2[0]-t2[1])/
            max(.5*(t2[0]+t2[1]),1e-9)<=MAX_T2_REPEAT_CHANGE,
        no_tone2_in_first_two_pilot_controls=max(
            ratio(0,2),ratio(1,2))<=MAX_LEAK,
    )
    return dict(
        source="TX15Y REG3F DTMF+REG70/71/72",
        pilot_only_training=True,
        no_heldout_used_to_fit_clock_or_gain=True,
        rf_carrier_iq_or_arbitrary_modulation_proven=False,
        nominal_frequencies_hz=[F1_HZ,F2_HZ],
        derived_from_pilot_frequencies_hz=[f1,f2],
        segment_durations_s=list(DURATIONS),
        pilot_indices=list(PILOT),
        heldout_indices=list(HELDOUT),
        fixed_settle_margin_s=MARGIN,
        pilot_fm_deviation_reference_hz=ref,
        state_normalized_amplitudes={
            str(i):[ratio(i,1),ratio(i,2)] for i in range(9)},
        predeclared_thresholds=dict(
            minimum_pilot_hz=MIN_PILOT_HZ,
            minimum_tone2_fraction=MIN_T2_REL,
            minimum_both_fraction=MIN_BOTH_REL,
            maximum_tone_leak_fraction=MAX_LEAK,
            maximum_off_fraction=MAX_OFF,
            maximum_tone2_repeat_relative_change=MAX_T2_REPEAT_CHANGE),
        conditions=conditions,
        candidate_dual_tone_qualified=all(conditions.values()),
        slots=parts,
        limitations=[
            "Synth and read-only tests do not establish actual REG72 B-chip RF behavior",
            "Received FM discriminator is not a calibrated transmitter RF power or IQ DAC",
            "Never modify REG72 while native-FSK FIFO is active",
            "Source-mode changes may reconfigure RX/FSK functionality; restore all registers"
        ],
    )

def analyze_file(path):
    iq,disc,t,mag,acquisition=extract_iq_and_rx_frequency(path)
    result=score_audio(disc,t[:-1],acquisition["onset_s"])
    with path.open("rb") as fp:
        sha=hashlib.file_digest(fp,"sha256").hexdigest()
    return dict(study="TX15Y second DTMF tone oscillator physical discriminators",
                source=str(path),source_sha256=sha,
                source_physical_variant_unverified=True,
                analysis_only=True,radio_touched=False,rf=acquisition,score=result)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iq",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    if args.out.exists():
        ap.error("refusing evidence overwrite")
    r=analyze_file(args.iq)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(r,indent=2)+"\n")
    print("candidate dual-tone qualified",r["score"]["candidate_dual_tone_qualified"])
    print("heldout",r["score"]["conditions"])
    print("saved",args.out)

if __name__=="__main__":
    main()
