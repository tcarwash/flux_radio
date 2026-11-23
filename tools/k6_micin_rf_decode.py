#!/usr/bin/env python3
"""Flux TX-12: offline SDR-to-MICIN-frequency-trajectory acquisition.

Reads saved RTL-SDR unsigned 8-bit I/Q. Never opens an SDR, audio
device, serial port or radio. Detects a sustained RF event, then fits
only the 16 known 1200-symbol/s pilot values of a previously specified
loopable analog 4-FSK reference. Every other symbol is held out.

This is an *RF observation* scorer, not a command to transmit. Even a
successful match does not prove the audio source, jack wiring, TX chain
calibration, spectral compliance or emissions came from the specified
radio. An unrelated carrier or replays can fool a receiver.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, resample_poly, sosfiltfilt

from k6_micin_4fsk import LEVELS
from k6_micin_rf_edge_analyze import measure as measure_short_rf_edges
from k6_micin_burst_lab import (
    BAUD, FS, GUARD_MS, PILOT_SYMBOLS, TOTAL_SYMBOLS,
    make_cycle,
)

SDR_FS = 1_024_000
SPS = FS // BAUD
CYCLE_SAMPLES = 2112
REQUIRED_GATE_MS = 90.0
MIN_PILOT_CORRELATION = 0.93
MAX_HELDOUT_SYMBOLS = TOTAL_SYMBOLS - PILOT_SYMBOLS


def _guard_input(raw: np.ndarray, offset_hz: float) -> None:
    if (not isinstance(raw,np.ndarray) or raw.dtype != np.uint8 or
        raw.ndim != 1 or len(raw) % 2 or not SDR_FS//2 <= len(raw)//2 <= 8*SDR_FS or
        not np.isfinite(offset_hz) or abs(offset_hz)>SDR_FS/2-20000):
        raise ValueError("expected 0.5..8 s interleaved uint8 RTL-SDR I/Q and in-band RF offset")


def to_baseband(raw: np.ndarray, *, offset_hz=-280500.) -> np.ndarray:
    """RF offset is relative to the RTL tuner; 48-kHz complex stream."""
    _guard_input(raw,offset_hz)
    iq = ((raw[::2].astype(np.float32)-127.5) +
          1j*(raw[1::2].astype(np.float32)-127.5))
    n=np.arange(len(iq))
    iq *= np.exp(-2j*np.pi*(offset_hz/SDR_FS)*n)
    # 1,024,000 -> 48,000 is the exact 3/64 rational ratio.
    return resample_poly(iq,3,64,window=("kaiser",10)).astype(np.complex64)


def rf_gate_segments(z: np.ndarray, *, threshold_db=11.) -> tuple[list[dict],dict]:
    """RF power detection is independent of the FSK payload labels."""
    if (z.ndim!=1 or not np.iscomplexobj(z) or not np.isfinite(z).all() or
        len(z)<FS//2 or not (4<=threshold_db<=30)):
        raise ValueError("invalid complex capture or relative gate threshold")
    power=uniform_filter1d(np.abs(z)**2,size=round(.002*FS))
    quiet=float(np.percentile(power,25))
    high=float(np.percentile(power,99.95))
    if quiet<=0:
        quiet=max(float(np.median(power)),1e-8)
    mask=power > quiet*(10**(threshold_db/10))
    # Gaps under 1 ms in RF activity shouldn't split a burst; merging is
    # local only, and cannot join two widely separated 26-ms bursts.
    from scipy.ndimage import binary_closing
    mask=binary_closing(mask,iterations=round(.0005*FS))
    ends=np.diff(np.pad(mask.astype(np.int8),(1,1)))
    segments=[]
    for a,b in zip(np.flatnonzero(ends==1),np.flatnonzero(ends==-1)):
        if b-a >= int(.008*FS):
            segments.append({
                "start_sample":int(a),
                "end_sample":int(b),
                "start_s":float(a/FS),
                "end_s":float(b/FS),
                "duration_ms":float((b-a)*1000/FS),
                "sufficient_for_arbitrary_phase_cycle":
                    bool((b-a)*1000/FS >= REQUIRED_GATE_MS),
            })
    return segments,{
        "relative_gate_threshold_db":threshold_db,
        "baseline_smoothed_power":quiet,
        "peak_smoothed_power":high,
        "peak_over_baseline_db":float(10*np.log10(max(high,1e-20)/quiet)),
        "not_a_calibrated_rf_power_measurement":True,
    }


def frequency_trace(z:np.ndarray) -> np.ndarray:
    """Differentiate received RF phase; lowpass *frequency*, not labels."""
    if (len(z)<FS//2 or not np.isfinite(z).all()):
        raise ValueError("invalid phase-bearing observation")
    # Frequency discriminator; sample-to-sample 180-degree wrap not expected
    # within the guarded ~10 kHz instantaneous FM band after decimation.
    hz=np.angle(z[1:]*np.conj(z[:-1])) * FS/(2*np.pi)
    hz=np.r_[hz[0],hz]
    return sosfiltfilt(butter(4,4900,fs=FS,output="sos"),hz)


def _candidate_acquire(freq:np.ndarray, gate:dict,
                       reference:np.ndarray, symbols:np.ndarray,
                       *, min_correlation:float) -> dict | None:
    """Acquisition and all calibration decisions are PILOT ONLY."""
    if not 0.7<=min_correlation<=1.0:
        raise ValueError("invalid pilot threshold")
    a,b=gate["start_sample"],gate["end_sample"]
    # Guard against treating an RF startup edge as a complete symbol cycle.
    if b-a < int(REQUIRED_GATE_MS/1000*FS):
        return None
    centers=round(GUARD_MS*FS/1000)+round(SPS/2)+np.arange(TOTAL_SYMBOLS)*SPS
    # Search only whole cycles captured on the RF plateau. We ignore
    # the first and last millisecond to avoid RF attack/release ringing.
    start=a+round(.001*FS)
    stop=b-CYCLE_SAMPLES-round(.001*FS)
    if stop<start:
        return None
    indices=np.arange(start,stop+1,dtype=np.int32)
    pilots=centers[:PILOT_SYMBOLS]
    refpilot=reference[pilots]
    ref0=refpilot-refpilot.mean()
    matrix=freq[indices[:,None]+pilots[None,:]]
    o0=matrix-matrix.mean(axis=1,keepdims=True)
    numer=o0@ref0
    denom=np.sqrt(np.sum(o0*o0,axis=1)*np.sum(ref0**2))
    correlations=np.divide(numer,denom,out=np.zeros(len(indices)),where=denom>1e-12)
    # We accept polarity inversion but report it. No held-out data used
    # for selecting time, gain, inversion or carrier offset.
    best=int(np.argmax(np.abs(correlations)))
    corr=float(correlations[best])
    if abs(corr)<min_correlation:
        return None
    candidate=int(indices[best])
    observation=freq[candidate+centers]
    pilotobs=observation[:PILOT_SYMBOLS]
    slope=float(np.dot(pilotobs-pilotobs.mean(),ref0)/np.dot(ref0,ref0))
    intercept=float(pilotobs.mean()-slope*refpilot.mean())
    predicted=intercept+slope*reference[centers]
    residual=observation-predicted
    half_deviation=float(abs(slope)*np.max(np.abs(reference)))
    if not 200<=half_deviation<=12000:
        # A flat RF carrier or nonsensical pilot noise must not count as a
        # meaningful frequency deviation calibration.
        return None
    ideal_levels=intercept+slope*np.max(np.abs(reference))*LEVELS
    labels=np.argmin(np.abs(observation[:,None]-ideal_levels[None,:]),axis=1)
    held=slice(PILOT_SYMBOLS,TOTAL_SYMBOLS)
    # Symbol decision is independent of the expected heldout labels.
    metric={
        "gate_start_s":gate["start_s"],
        "gate_end_s":gate["end_s"],
        "time_of_complete_cycle_s":float(candidate/FS),
        "pilot_score_signed_correlation":corr,
        "pilot_score_abs_correlation":abs(corr),
        "pilot_only_slope_hz_per_wav_fullscale":slope,
        "pilot_only_carrier_intercept_hz_relative_to_selected_rf_lo":intercept,
        "pilot_only_estimated_peak_rf_deviation_hz":half_deviation,
        "pilot_only_estimated_frequency_polarity":("normal" if slope>=0 else "inverted"),
        "pilot_only_center_rmse_hz":float(np.sqrt(np.mean(residual[:PILOT_SYMBOLS]**2))),
        "heldout_center_rmse_hz":float(np.sqrt(np.mean(residual[held]**2))),
        "heldout_normalized_rf_frequency_rmse":float(
            np.sqrt(np.mean(residual[held]**2))/half_deviation),
        "heldout_symbol_count":int(len(symbols[held])),
        "heldout_symbol_errors":int(np.count_nonzero(labels[held]!=symbols[held])),
        "heldout_predicted_symbol_ids":[int(v) for v in labels[held]],
        "pilot_derived_timing_gain_intercept_not_refit_on_holdout":True,
    }
    return metric


def analyze(raw:np.ndarray, *, pattern="balanced", amplitude=.03, bt=.5,
            offset_hz=-280500., gate_threshold_db=11.,
            min_pilot_correlation=MIN_PILOT_CORRELATION) -> dict:
    """Score RF ONLY if whole cycle + pilot are independently established."""
    _guard_input(raw,offset_hz)
    reference,symbols=make_cycle(amplitude=amplitude,bt=bt,pattern=pattern)
    # A narrow RF FFT detector is deliberately independent from the
    # downconverted signal's broadband-power gate. The earlier PA-off lab
    # pulse is visible in 2-ms spectral windows even when decimated
    # complex-power rises by only ~4 dB. Preserve BOTH observations.
    spectral=measure_short_rf_edges(raw,sample_rate=SDR_FS,
                                    expected_offset_hz=offset_hz,
                                    threshold_db=15.,min_windows=3)
    z=to_baseband(raw,offset_hz=offset_hz)
    gates,diag=rf_gate_segments(z,threshold_db=gate_threshold_db)
    edge_gates=[]
    for span in spectral["detected_segments"]:
        a=max(0,round(span["start_s"]*FS))
        b=min(len(z),round(span["end_s"]*FS))
        edge_gates.append({
            "start_sample":a,"end_sample":b,
            "start_s":float(a/FS),"end_s":float(b/FS),
            "duration_ms":float((b-a)*1000/FS),
            "sufficient_for_arbitrary_phase_cycle":
                bool((b-a)*1000/FS>=REQUIRED_GATE_MS),
            "spectral_local_peak_over_flanks_db":
                span["peak_local_bin_vs_flanks_db"],
        })
    candidates=[g for g in edge_gates if g["sufficient_for_arbitrary_phase_cycle"]]
    report={
        "project":"Flux",
        "experiment":"TX-12D saved-SDR signed-frequency MICIN 4FSK heldout",
        "software_only":True,"rf_tx_initiated":False,
        "micin_connected_or_verified_by_this_tool":False,
        "radio_emission_source_independently_authenticated":False,
        "measured_micin_to_rf_analog_transfer_function":False,
        "sample_rate_raw_hz":SDR_FS,
        "downconverted_complex_sample_rate_hz":FS,
        "relative_rf_offset_hz":float(offset_hz),
        "reference_pattern":pattern,
        "reference_amplitude_fraction_of_wav_fullscale":float(amplitude),
        "reference_gaussian_bt":float(bt),
        "reference_float64_cycle_sha256":
            hashlib.sha256(reference.astype("<f8").tobytes()).hexdigest(),
        "pilot_symbols":PILOT_SYMBOLS,
        "heldout_symbols":MAX_HELDOUT_SYMBOLS,
        "minimum_gate_duration_ms_for_phase_independent_whole_cycle":REQUIRED_GATE_MS,
        "rf_gates":edge_gates,
        "short_rf_edge_fft_diagnostics":spectral,
        "downconverted_power_gate_diagnostics":gates,
        "power_diagnostics":diag,
        "iq_adc_clip_fraction":float(np.mean((raw<=1)|(raw>=254))),
        "pilot_qualified_fsk_cycle":False,
        "measurement":None,
        "warnings":[
            "An RF carrier, even with similar frequency modulation, is not proof of MICIN wiring or audio injection",
            "The computer soundcard / cable / input bias are uncalibrated without external electrical measurements",
            "RF detection only spans the narrow expected SDR passband; not a calibrated spectrum or out-of-band test",
            "Only a pilot-timed four-level FM match can be scored; PA startup transient is not 4FSK",
            "PLL and SDR sample clocks are not independent frequency standards",
            "A valid match also requires external, timestamped source/WAV, RX metadata, radio build-ID and TX watchdog proof",
            "Receiver filtering and RF timing affect normalized frequency error and the decoded symbols",
        ],
    }
    if not candidates:
        report["qualification_failure"]="no spectrally detected RF gate long enough for arbitrary-phase 44-ms cycle"
        return report
    freq=frequency_trace(z)
    scores=[]
    for g in candidates:
        fit=_candidate_acquire(freq,g,reference,symbols,min_correlation=min_pilot_correlation)
        if fit is not None:
            scores.append(fit)
    if not scores:
        report["qualification_failure"]="sustained RF detected, but no independently prescribed pilot with valid deviation"
        return report
    # Gate/cycle is selected by PILOT correlation only, never heldout accuracy.
    best=max(scores,key=lambda s:s["pilot_score_abs_correlation"])
    report["pilot_qualified_fsk_cycle"]=True
    report["measurement"]=best
    return report


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True,
                   help="saved .npy with interleaved U8 RTL I/Q at 1024000 samples/s")
    p.add_argument("--pattern",choices=("stress","balanced"),required=True,
                   help="choose the source's actual known transmitted reference BEFORE analyzing")
    p.add_argument("--rf-offset-hz",type=float,default=-280500.)
    p.add_argument("--gate-threshold-db",type=float,default=11.)
    p.add_argument("--min-pilot-correlation",type=float,default=MIN_PILOT_CORRELATION)
    p.add_argument("--amplitude",type=float,default=.03)
    p.add_argument("--bt",type=float,default=.5)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite prior RF analysis")
    try:
        raw=np.load(args.iq,mmap_mode="r",allow_pickle=False)
        result=analyze(raw,pattern=args.pattern,
                       offset_hz=args.rf_offset_hz,
                       gate_threshold_db=args.gate_threshold_db,
                       min_pilot_correlation=args.min_pilot_correlation,
                       amplitude=args.amplitude,bt=args.bt)
        with args.iq.open("rb") as f:
            result["iq_npy_sha256"]=hashlib.file_digest(f,"sha256").hexdigest()
        result["iq_source_path"]=str(args.iq)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2)+"\n")
        print("qualified pilot",result["pilot_qualified_fsk_cycle"],
              "RF gates",len(result["rf_gates"]),
              "heldout",result["measurement"] and
              f'{result["measurement"]["heldout_symbol_errors"]}/{MAX_HELDOUT_SYMBOLS}',
              "reason",result.get("qualification_failure"))
        print("Saved",args.out)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        p.error(str(exc))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
