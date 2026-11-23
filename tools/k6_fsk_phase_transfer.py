#!/usr/bin/env python3
"""OFFLINE leave-one-capture-out FD6818B binary-FSK phase-response study.

Fit frequency/phase response tap coefficients ONLY on known pilot/training
chips of OTHER archived K6 RF recordings. On the target recording, calibrate
one constant per-chip frequency offset from its own pilot and score its
HELD-OUT RF phase increments. Never use target holdout to tune a model.

This tests repeatability of the *joint transmitter + receiver chain*.
The same FSK bits are used across recordings, so this is NOT independent
payload generalization, a true RFIC transfer-function measurement, or an
arbitrary RF waveform / TX engine. No radio, SDR control, flashing or TX.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfiltfilt

from k6_fsk_pdm_chip_decode import RAW_FS, IQ_FS, validate_manifest, _rf_to_baseband
from k6_fsk_phase_fir import TRAIN, HELDOUT, MODELS, design

FILTER_HZ=10000.


def phase_increments(raw: np.ndarray, acquisition: dict, *, cutoff=FILTER_HZ) -> np.ndarray:
    if (not acquisition.get("known_pilot_acquired") or
            not acquisition.get("training_only_acquisition") or
            acquisition.get("native_baud_estimate")!=4800 or
            not 1000<=cutoff<=30000):
        raise ValueError("requires established pilot-only native FSK clock")
    y,start_s=_rf_to_baseband(raw)
    y=sosfiltfilt(butter(4,cutoff,fs=IQ_FS,output="sos"),y)
    phase=np.unwrap(np.angle(y))
    bit0=float(acquisition["first_fifo_bit_time_s"])
    times=(bit0-start_s)*IQ_FS+np.arange(1025)*IQ_FS/4800
    if times[0]<0 or times[-1]>=len(phase)-1:
        raise ValueError("payload outside archived RF")
    sampled=np.interp(times,np.arange(len(phase)),phase)
    increment=np.diff(sampled)
    if not np.isfinite(increment).all() or np.std(increment[TRAIN])<.03:
        raise ValueError("inadequate training phase variance")
    return increment


def cross_validate(bits: np.ndarray, measured: list[np.ndarray]) -> dict:
    bits=np.asarray(bits,dtype=np.uint8)
    if bits.shape!=(1024,) or np.any(bits>1) or not 2<=len(measured)<=20:
        raise ValueError("need a 1024-bit payload and >=2 independent runs")
    if any(np.asarray(row).shape!=(1024,) or
           not np.isfinite(row).all() for row in measured):
        raise ValueError("invalid measured phase vectors")
    result=[]
    for target_ix,target in enumerate(measured):
        models={}
        for name,lags in MODELS.items():
            xtrain=design(bits,TRAIN,lags)
            xhold=design(bits,HELDOUT,lags)
            # Intercept differs with per-run carrier/clock offset.
            # Pool only *demeaned* pilot increments of other captures.
            xx=xtrain[:,1:]
            xx0=xx-xx.mean(axis=0)
            xdonor=np.vstack([xx0 for j in range(len(measured)) if j!=target_ix])
            ydonor=np.concatenate([
                np.asarray(row)[TRAIN]-np.asarray(row)[TRAIN].mean()
                for j,row in enumerate(measured) if j!=target_ix
            ])
            slopes=np.linalg.lstsq(xdonor,ydonor,rcond=None)[0]
            intercept=float(np.mean(np.asarray(target)[TRAIN]-xx@slopes))
            predicted=xhold[:,1:]@slopes+intercept
            residual=np.asarray(target)[HELDOUT]-predicted
            cumulative=np.cumsum(residual)
            model={
                "tap_offsets": list(lags),
                "trained_other_captures":len(measured)-1,
                "target_pilot_only_intercept_rad_per_chip":intercept,
                "donor_pilot_only_taps_rad_per_chip":[float(v) for v in slopes],
                "target_pilot_increment_rmse_rad":float(np.sqrt(np.mean(
                    (np.asarray(target)[TRAIN]-(xtrain[:,1:]@slopes+intercept))**2))),
                "target_heldout_increment_rmse_rad":float(np.sqrt(np.mean(residual**2))),
                "target_heldout_phase_accumulation_coherence":
                    float(abs(np.mean(np.exp(1j*cumulative)))),
                "target_heldout_mean_residual_hz":
                    float(np.mean(residual)*4800/(2*np.pi)),
                "heldout_chip_increments":len(HELDOUT),
                "heldout_not_used_for_training_or_model_selection":True,
                "target_pilot_used_only_for_constant_offset":True,
            }
            models[name]=model
        result.append({"target_case_index":target_ix,
                       "donor_case_indices":[i for i in range(len(measured))
                                             if i!=target_ix],
                       "models":models})
    return {
        "experiment":"FSK-25 leave-one-recording-out phase response transfer",
        "offline_only":True,
        "physical_tx_initiated":False,
        "input_transmitted_bitstream_identical_across_all_cases":True,
        "source_receiver_response_separated":False,
        "heldout_command_bits_not_used_as_fit_targets":True,
        "training_increment_range":[int(TRAIN[0]),int(TRAIN[-1])],
        "heldout_increment_range":[int(HELDOUT[0]),int(HELDOUT[-1])],
        "heldout_metric":"accumulated chip-boundary RF phase coherence (not continuous IQ EVM)",
        "cases":result,
        "caveats":[
            "These are repeated identical-FIFO-payload RF captures, not different heldout messages",
            "The measured response includes the RFIC, receiver filters, timing jitter and SDR oscillator",
            "Other recordings supply learned tap shape, target pilot alone supplies static carrier offset",
            "Target heldout phase is used strictly for scoring, never for correction",
            "Better chip-boundary coherence does not establish arbitrary IQ, stable APC or spectral compliance",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--chip-report",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite existing report")
    try:
        manifest=json.loads(args.manifest.read_text())
        bits,_,_=validate_manifest(manifest)
        source_report=json.loads(args.chip_report.read_text())
        rows=source_report["cases"]
        if not 2<=len(rows)<=20:
            raise ValueError("require 2..20 archives")
        phase_vectors=[]
        sources=[]
        for case in rows:
            path=Path(case["iq_path"])
            with path.open("rb") as stream:
                digest=hashlib.file_digest(stream,"sha256").hexdigest()
            if digest!=case["iq_npy_sha256"]:
                raise ValueError(f"archive digest mismatch: {path}")
            raw=np.load(path,mmap_mode="r",allow_pickle=False)
            phase_vectors.append(phase_increments(raw,case))
            sources.append({"npy_path":str(path),"npy_sha256":digest,
                            "pilot_bit0_s":case["first_fifo_bit_time_s"],
                            "pilot_correlation":case["pilot_correlation"]})
        report=cross_validate(bits,phase_vectors)
        report["sources"]=sources
        report["manifest_path"]=str(args.manifest)
        report["manifest_sha256"]=hashlib.sha256(args.manifest.read_bytes()).hexdigest()
        report["chip_report_sha256"]=hashlib.sha256(args.chip_report.read_bytes()).hexdigest()
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2)+"\n")
        for index,row in enumerate(report["cases"]):
            models=row["models"]
            print(Path(sources[index]["npy_path"]).name,
                  "single",round(models["single_binary"]["target_heldout_phase_accumulation_coherence"],4),
                  "adj3",round(models["adjacent_three_tap"]["target_heldout_phase_accumulation_coherence"],4),
                  "adj7",round(models["adjacent_seven_tap"]["target_heldout_phase_accumulation_coherence"],4))
        print("Saved",args.out)
    except (OSError,ValueError,KeyError,TypeError) as exc:
        p.error(str(exc))


if __name__=="__main__":
    main()
