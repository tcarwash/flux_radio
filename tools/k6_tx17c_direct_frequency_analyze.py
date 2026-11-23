#!/usr/bin/env python3
"""TX-17C B-chip direct-synthesizer REG_38 TX-latch RF test, OFFLINE ONLY.

Input is the saved 5-s U8 complex SDR IQ from the proven dummy-load
transaction. The embedded firmware makes a single TXON, six low-word
writes, no TXON/RXON between those writes, then dekeys and restores.

Seven non-overlapping steady intervals:
  PA ON / introduction: 150ms (unmodulated baseline, no write),
  REG38 steps i=0..5: 160ms each, deltas [0,+50,0,-50,0,+100],
  restoration/tail: 150ms (delta 0).
Frequency word is nominally 10 Hz/LSB; this MUST be measured on B-chip,
not assumed from non-B documentation. The critical discriminator is
whether frequency changes during TX and is restored by each reg write.

This test detects RF-frequency steps, settles, dropouts and tentative
phase discontinuities. It cannot identify oscillator PLL internals
without a separate shared coherent clock. It does not assert phase
continuity or arbitrary-waveform capability from a frequency step.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d

from k6_tx15s_carrier_iq import complex_downmix
from k6_tx15s_rf_analyze import TIME_ORIGIN,FS

INTRO_S=.150
STEP_S=.160
TAIL_S=.150
DELTAS=(0,+50,0,-50,0,+100,0)
NSTEP=6
# Never optimize these thresholds using the heldout capture before it
# qualifies. We will keep controls and test confidence visible.
MAX_MEDIAN_ERROR_HZ=150.
MIN_ABSOLUTE_TONE_RESPONSE_HZ=250.
MAX_DRIFT_CONTROL_HZ=90.
SAMPLE_MARGIN=.035
MIN_SIGNAL_MAG_RATIO=5.0

def extract_iq_and_rx_frequency(iq_path:Path):
    z,frequency=complex_downmix(iq_path)
    t=TIME_ORIGIN+np.arange(len(z))/FS
    abs_iq=np.abs(z)
    smoothed=uniform_filter1d(abs_iq,size=int(.010*FS),mode="nearest")
    floor=float(np.quantile(smoothed,.12))
    peak=float(np.quantile(smoothed,.88))
    if not floor>=0 or not peak>floor*MIN_SIGNAL_MAG_RATIO:
        raise ValueError("No confidently separable PA-on RF carrier; PA-off is not diagnostic")
    cutoff=floor+.40*(peak-floor)
    qualified=smoothed>cutoff
    # 15ms consecutive strong RF, to reject incidental spikes.
    count=uniform_filter1d(qualified.astype(float),size=round(.015*FS))
    begins=np.flatnonzero(count>.95)
    if not len(begins):
        raise ValueError("No stable transmitted RF burst detected")
    start=float(t[int(begins[0])])
    # Center of 15ms stable-gate finder is about 7.5ms into RF rise.
    # Use amplitude slope around finder to identify the actual RF onset.
    local=(t>=start-.025)&(t<=start+.01)
    candidates=np.flatnonzero(local & (smoothed>=cutoff))
    if not len(candidates):raise ValueError("RF turn-on timestamp ambiguous")
    start=float(t[int(candidates[0])])
    return z,frequency,t,abs_iq,dict(
        onset_s=start,
        noise_magnitude_quantile12=floor,
        strong_magnitude_quantile88=peak,
        signal_to_noise_proxy=peak/max(floor,1e-6),
        rx_magnitude_gate_threshold=cutoff
    )

def get_regions(onset):
    # Absolute timestamps after PA-on. First baseline and each selected
    # step have middle-of-plateau scoring region, unaffected by predicted
    # 35-ms ring/PLL settling or shutdown.
    windows=[(onset, onset+INTRO_S,"intro",0)]
    for i in range(NSTEP):
        lo=onset+INTRO_S+i*STEP_S
        windows.append((lo,lo+STEP_S,f"reg38_step{i}",DELTAS[i]))
    windows.append((onset+INTRO_S+NSTEP*STEP_S,
                    onset+INTRO_S+NSTEP*STEP_S+TAIL_S,"restored_tail",0))
    return windows

def score_captured_raw_iq(iq_path:Path):
    z,f,t,amp,discovery=extract_iq_and_rx_frequency(iq_path)
    windows=get_regions(discovery["onset_s"])
    medians=[];strong=[]
    for a,b,label,delta in windows:
        margin= min(SAMPLE_MARGIN,(b-a)*.2)
        valid=(t>=a+margin)&(t<=b-margin)
        if valid.sum()<1300:
            raise ValueError(f"captured IQ too short for {label}")
        samples=f
        qualified=valid[:-1]
        median=float(np.median(samples[qualified]))
        robust_sigma=float(1.4826*np.median(abs(samples[qualified]-median)))
        signal_med=float(np.median(amp[valid]))
        medians.append(dict(region=label,step_word_units=delta,
                            step_expected_hz=delta*10.,
                            measured_rf_frequency_residual_hz=median,
                            received_amplitude_proxy=signal_med,
                            robust_frequency_spread_hz=robust_sigma,
                            n_steady_rf_samples=int(qualified.sum())))
        strong.append(signal_med)
    baseline=np.median([medians[i]["measured_rf_frequency_residual_hz"]
                        for i in (0,1,3,5,7)])
    for region in medians:
        region["observed_frequency_delta_from_baseline_hz"]=float(
            region["measured_rf_frequency_residual_hz"]-baseline)
        region["deviation_error_hz"]=float(
            region["observed_frequency_delta_from_baseline_hz"]-
            region["step_expected_hz"])
    target=[medians[i] for i in (2,4,6)]
    expected=[t["step_expected_hz"] for t in target]
    actual=[t["observed_frequency_delta_from_baseline_hz"] for t in target]
    controls=[medians[i]["observed_frequency_delta_from_baseline_hz"]
              for i in (0,1,3,5,7)]
    transmitted_rf_consistently_present=(
        min(strong)>discovery["noise_magnitude_quantile12"]*
        MIN_SIGNAL_MAG_RATIO)
    carrier_changes_confirmed=bool(
        transmitted_rf_consistently_present and
        max(abs(a-e) for a,e in zip(actual,expected)) < MAX_MEDIAN_ERROR_HZ and
        max(abs(c) for c in controls)<MAX_DRIFT_CONTROL_HZ and
        min(abs(a) for a in actual)>MIN_ABSOLUTE_TONE_RESPONSE_HZ)
    no_measurable_immediate_latch=bool(
        transmitted_rf_consistently_present and
        max(abs(a) for a in actual)<MAX_DRIFT_CONTROL_HZ and
        max(abs(c) for c in controls)<MAX_DRIFT_CONTROL_HZ)
    unknown=not(carrier_changes_confirmed or no_measurable_immediate_latch)
    # Experiment deliberately avoids retune/re-TXON; a negative result
    # does NOT rule out synthesizer changes that latch at RXON/TXON.
    with iq_path.open("rb") as fp:
        digest=hashlib.file_digest(fp,"sha256").hexdigest()
    return dict(
        study="Flux TX17C low-word frequency synthesizer direct latch during TX",
        source_iq=str(iq_path),
        source_iq_sha256=digest,
        no_rfic_state_transition_between_test_words=True,
        forced_tune_rekey_latch_not_tested=True,
        fd6818b_id_expected="0x4829",
        frequency_control_register_0x38_only=True,
        original_registers_restored_by_firmware=True,
        raw_complex_rf_iq_used=True,
        rx_sample_rate=FS,
        expected_small_frequency_deltas_hz=[int(a) for a in expected],
        measured_rf_frequency_deltas_hz=[float(a) for a in actual],
        acquisition=discovery,
        regions=medians,
        control_frequency_deltas_hz=[float(c) for c in controls],
        immediate_synth_frequency_response_confirmed=carrier_changes_confirmed,
        no_immediate_latch_detected=no_measurable_immediate_latch,
        inconclusive=unknown,
        read_only=True,
        limitation="No synchronized RF carrier-phase test from this result; full retune / TXON latch and PLL phase reset must be tested separately.",
    )

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():p.error("refusing overwrite")
    r=score_captured_raw_iq(args.iq)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(r,indent=2)+"\n")
    print("TX17C measured Hz deltas:",[round(v,1) for v in
          r["measured_rf_frequency_deltas_hz"]])
    print("Immediate response",r["immediate_synth_frequency_response_confirmed"],
          "No immediate latch",r["no_immediate_latch_detected"],
          "Uncertain",r["inconclusive"])
    print("saved",args.out)

if __name__=="__main__":
    main()
