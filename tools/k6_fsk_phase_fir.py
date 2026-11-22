#!/usr/bin/env python3
"""Fit a native-FSK phase-transition model on known TX chips, score heldout.

Reconstructing complex RF phase from a commanded binary chip stream requires
accounting for finite modulation/receiver bandwidth. Fit only the 320 pilot
chips + first 256 PDM-training chips and predict remaining chip-boundary phase.

This is offline system identification on received I/Q, not a transmitter,
general PSK waveform proof, or calibrated measurement of the bare RFIC.
An adjacent future-chip feature is available because the commanded vector
is already known. It does NOT indicate a physically noncausal transmitter.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

from k6_fsk_pdm_chip_decode import (
    RAW_FS, IQ_FS, validate_manifest, _rf_to_baseband,
)

TRAIN_END = 320+16*16  # 576 known chips; the final 448 chip labels held out
VALID_INDICES = np.arange(3,1021,dtype=np.int32)
TRAIN = VALID_INDICES[VALID_INDICES<TRAIN_END]
HELDOUT = VALID_INDICES[VALID_INDICES>=TRAIN_END]
MODELS = {
    "single_binary": (0,),
    "causal_three_tap": (0,-1,-2),
    "adjacent_three_tap": (-1,0,1),
    "adjacent_seven_tap": (-3,-2,-1,0,1,2,3),
}


def design(bits: np.ndarray, indices: np.ndarray, lags: tuple[int,...]) -> np.ndarray:
    """Known commanded bits are inputs, NOT measurements or decoded truth."""
    bit=np.asarray(bits)
    if (bit.ndim!=1 or len(bit)!=1024 or
        not np.issubdtype(bit.dtype,np.integer) or
        np.any((bit!=0)&(bit!=1))):
        raise ValueError("invalid command chip sequence")
    if not len(indices) or np.min(indices)+min(lags)<0 or np.max(indices)+max(lags)>=len(bit):
        raise ValueError("phase model requires chip neighbors inside capture")
    s=2*bit.astype(float)-1
    return np.column_stack([np.ones(len(indices)),
                            *(s[indices+lag] for lag in lags)])


def fit_increments(phase_increments_rad: np.ndarray,
                   command_bits: np.ndarray) -> dict:
    dphi=np.asarray(phase_increments_rad)
    if (dphi.ndim!=1 or len(dphi)!=1024 or
        not np.isfinite(dphi).all() or
        float(np.std(dphi[TRAIN]))<.03):
        raise ValueError("expected 1024 finite RF phase increments")
    results={}
    for name,lags in MODELS.items():
        xx=design(command_bits,TRAIN,lags)
        if np.linalg.matrix_rank(xx)<xx.shape[1]:
            raise ValueError("phase training does not excite all model taps")
        coefs=np.linalg.lstsq(xx,dphi[TRAIN],rcond=None)[0]
        pred_train=xx@coefs
        pred_holdout=design(command_bits,HELDOUT,lags)@coefs
        err=dphi[HELDOUT]-pred_holdout
        # The predicted and measured phase are initially aligned at the
        # beginning of the heldout span. After that, no heldout CFO/phase
        # refitting, so the coherence punishes accumulating model error.
        residual_phase=np.cumsum(err)
        scores={
            "tap_offsets":list(lags),
            "trained_intercept_rad_per_chip":round(float(coefs[0]),7),
            "trained_taps_rad_per_chip":[round(float(c),7) for c in coefs[1:]],
            "training_chip_increment_rmse_rad":round(float(
                np.sqrt(np.mean((dphi[TRAIN]-pred_train)**2))),6),
            "heldout_chip_increment_rmse_rad":round(float(
                np.sqrt(np.mean(err**2))),6),
            "heldout_accumulated_phase_coherence":round(
                float(abs(np.mean(np.exp(1j*residual_phase)))),6),
            "heldout_increment_mean_bias_hz":round(
                float(np.mean(err)*4800/(2*np.pi)),4),
            "fitted_static_deviation_hz_from_tap_sum":round(
                float(coefs[1:].sum()*4800/(2*np.pi)),3),
            "coefficient_fit_uses_only_training":True,
            "phase_anchor_at_heldout_start_only":True,
        }
        results[name]=scores
    return {
        "training_chip_increments":int(len(TRAIN)),
        "heldout_chip_increments":int(len(HELDOUT)),
        "training_chip_range":[int(TRAIN[0]),int(TRAIN[-1])],
        "heldout_chip_range":[int(HELDOUT[0]),int(HELDOUT[-1])],
        "models":results,
        "score_type":"chip-boundary accumulated phase coherence; NOT continuous-IQ coherence",
        "all_model_inputs_are_known_commanded_bits":True,
        "no_heldout_measurements_used_in_coefficient_fit":True,
    }


def analyze_capture(raw: np.ndarray, manifest: dict,
                    acquired: dict, *, cutoff_hz=10000.) -> dict:
    bits,_,_=validate_manifest(manifest)
    if (not acquired.get("known_pilot_acquired") or
        acquired.get("native_heldout_bits")!=448 or
        acquired.get("native_baud_estimate")!=4800 or
        not 1000<=cutoff_hz<=30000):
        raise ValueError("pilot acquisition or filter is not suitable")
    y,start=_rf_to_baseband(raw)
    # Zero-phase offline IQ filtering means the effective response
    # includes both the transmitter and the receiver signal chain.
    sos=butter(4,cutoff_hz,fs=IQ_FS,output="sos")
    y=sosfiltfilt(sos,y)
    phase=np.unwrap(np.angle(y))
    bit0=float(acquired["first_fifo_bit_time_s"])
    clock=float(acquired["native_baud_estimate"])
    positions=(bit0-start)*IQ_FS+np.arange(1025,dtype=float)*IQ_FS/clock
    if positions[0]<0 or positions[-1]>len(phase)-1:
        raise ValueError("known payload outside receive I/Q")
    chip_phase=np.interp(positions,np.arange(len(phase)),phase)
    dphi=np.diff(chip_phase)
    report=fit_increments(dphi,bits)
    report["first_fifo_bit_time_s"]=bit0
    report["receiver_iq_filter_hz"]=cutoff_hz
    report["receiver_filter_order"]=4
    report["zero_phase_offline_filter"]=True
    report["chip_clock_hz"]=clock
    report["rf_was_transmitted_by_analyzer"]=False
    report["limits"]=[
        "This is receiver-specific phase reconstruction, not intrinsic RFIC identification.",
        "Known commanded chips are predictor inputs, even during heldout scoring.",
        "The look-ahead taps reflect offline receive processing and complete known TX vector.",
        "Do not equate chip-boundary phase coherence with continuous RF I/Q EVM.",
        "Receiver oscillator drift and imperfect start timing can lower long-term coherence."
    ]
    return report


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",required=True,type=Path)
    p.add_argument("--chip-report",required=True,type=Path,
                   help="training-only native chip decoder JSON")
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--iq-lowpass-hz",type=float,default=10000.)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite existing phase report")
    try:
        manifest=json.loads(args.manifest.read_text())
        rows=json.loads(args.chip_report.read_text())["cases"]
        results=[]
        for row in rows:
            path=Path(row["iq_path"])
            with path.open("rb") as inp:
                sha=hashlib.file_digest(inp,"sha256").hexdigest()
            if sha!=row["iq_npy_sha256"]:
                raise ValueError(f"capture changed after acquisition: {path}")
            iq=np.load(path,mmap_mode="r",allow_pickle=False)
            result=analyze_capture(iq,manifest,row,cutoff_hz=args.iq_lowpass_hz)
            result["iq_path"]=str(path)
            result["iq_npy_sha256"]=sha
            results.append(result)
        report={
            "project":"Flux",
            "study":"FSK-PDM heldout physical phase-response system identification",
            "offline_only":True,
            "source_chip_report":str(args.chip_report),
            "iq_filter_hz":args.iq_lowpass_hz,
            "cases":results,
        }
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2)+"\n")
    except (OSError,KeyError,TypeError,ValueError) as exc:
        p.error(str(exc))
    for row in results:
        models=row["models"]
        print(Path(row["iq_path"]).name,
              "single",models["single_binary"]["heldout_accumulated_phase_coherence"],
              "adjacent3",models["adjacent_three_tap"]["heldout_accumulated_phase_coherence"],
              "adjacent7",models["adjacent_seven_tap"]["heldout_accumulated_phase_coherence"])
    print("Saved offline analysis:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
