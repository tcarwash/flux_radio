#!/usr/bin/env python3
"""TX-15Q: pilot-only symbol-clock correction on captured K6 FM audio.

**Offline only.** Reads saved raw RTL-SDR I/Q. Audio tone phase from
FM frequency discrimination is NOT carrier RF phase or complex RF IQ.

The TX-15P 150/s timestamp and phase report is independently acquired
from its first 16 symbols. Three predeclared strong pilot transitions
(12, 13, 15) locate the audio frequency midpoint in the saved RF capture.
A two-parameter linear schedule (start and slot interval) is frozen.
The same clock is used to fit phase across all 48 held-out symbols.
A further one-parameter *pilot-only* audio phase/transfer-delay
correction is frozen, then the held-out phase errors are reported.

The clock difference is SDR-referenced observed symbol timing.
Do not identify it with a calibrated MCU crystal error. The apparent
audio delay combines RX, TX, tuner and timing effects; it is NOT a
measured FD6818B internal group delay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, sosfiltfilt, hilbert

import k6_tx_tone_rf_analyze as tones
import k6_tx15_phase_rf_analyze as phase
from k6_tx15_phase_vector import NEW_HEADER,parse_header,WORD_PER_HZ

FS = 64_000
N = 64
PILOT = 16
BAUD = 150
NOMINAL_PERIOD = 1 / BAUD
STRONG_HZ = 450.
EDGE_WINDOW_S = .0035
MAX_DELAY_S = .00025
PHASE_GRID_STEP_S = .0000005


def wrapped_rms_deg(values:np.ndarray)->float:
    if not len(values):
        return float("nan")
    rad=np.angle(np.exp(1j*np.asarray(values,dtype=float)))
    return float(np.sqrt(np.mean(np.rad2deg(rad)**2)))


def find_audio_midpoint_edges(f:np.ndarray,symbols:np.ndarray,tx_hz:np.ndarray,
                              *,start_hint_s:float,time_origin_s:float,
                              fs:int=FS,smooth_samples:int=65,
                              search_s:float=EDGE_WINDOW_S)->list[dict]:
    """Detect predeclared large tone-step midpoints in RF discriminator audio.

    No generic received waveform classification is claimed: the precise
    ordered tone vector and its 150/s approximate timing are pre-known.
    """
    f=np.asarray(f)
    symbols=np.asarray(symbols)
    tx_hz=np.asarray(tx_hz)
    if (f.ndim!=1 or len(f)<int(.5*fs) or not np.isfinite(f).all() or
        len(symbols)!=N or len(tx_hz)!=8 or fs<8000 or
        not np.isfinite(start_hint_s) or
        not 0<smooth_samples<fs//50 or not 0<search_s<=.004):
        raise ValueError("invalid discriminator / timing inputs")
    sig=sosfiltfilt(butter(4,[200,3500],btype="bandpass",
                             output="sos",fs=fs),f)
    analytic=hilbert(sig)
    inst=np.angle(analytic[1:]*np.conj(analytic[:-1]))*fs/(2*np.pi)
    smoothed=uniform_filter1d(inst,smooth_samples)
    edges=[]
    for i in range(1,N):
        previous=float(tx_hz[int(symbols[i-1])])
        following=float(tx_hz[int(symbols[i])])
        if abs(following-previous)<STRONG_HZ:
            continue
        nominal_time=start_hint_s+i*NOMINAL_PERIOD
        l=int((nominal_time-search_s-time_origin_s)*fs)
        r=int((nominal_time+search_s-time_origin_s)*fs)
        if l<0 or r>len(smoothed) or r<=l+10:
            raise ValueError("known high-contrast edge outside IQ capture")
        window=smoothed[l:r]
        mid=(previous+following)/2
        signs=np.sign(window-mid)
        crossings=np.flatnonzero(np.diff(signs)!=0)
        if not len(crossings):
            raise ValueError(f"no valid audio mid-frequency crossing at slot {i}")
        candidates=l+crossings
        t=candidates/fs+time_origin_s
        target=int(np.argmin(abs(t-nominal_time)))
        sample=candidates[target]
        edges.append({
            "after_symbol_index":i,
            "region":"pilot" if i<PILOT else "heldout",
            "before_tone_index":int(symbols[i-1]),
            "after_tone_index":int(symbols[i]),
            "delta_audio_tone_hz":following-previous,
            "crossing_time_s":float(t[target]),
            "nominal_reference_time_s":float(nominal_time),
            "fractional_frequency_at_crossing_hz":float(smoothed[sample]),
        })
    return edges


def pilot_clock(edges:list[dict],*,nominal_period:float=NOMINAL_PERIOD)->dict:
    pilot=[e for e in edges if e["region"]=="pilot"]
    if len(pilot)<3 or len({e["after_symbol_index"] for e in pilot})<3:
        raise ValueError("need three predeclared high-contrast pilot edges")
    ids=np.array([e["after_symbol_index"] for e in pilot],dtype=float)
    times=np.array([e["crossing_time_s"] for e in pilot],dtype=float)
    if max(ids)-min(ids)<3:
        raise ValueError("pilot edges do not span enough symbols")
    interval,first=np.polyfit(ids,times,1)
    if not .99*nominal_period<interval<1.01*nominal_period:
        raise ValueError("observed audio timing outside bounded +/-1% clock model")
    residual=times-(first+interval*ids)
    return {
        "acquired_from_pilot_only":True,
        "pilot_strong_edges":int(len(pilot)),
        "pilot_indices":ids.astype(int).tolist(),
        "start_s":float(first),
        "period_s":float(interval),
        "observed_symbol_rate_per_s":float(1/interval),
        "apparent_schedule_skew_ppm_relative_SDR":float(
            (interval/nominal_period-1)*1e6),
        "pilot_crossing_rms_us":float(np.sqrt(np.mean(residual**2))*1e6),
        "pilot_crossing_max_abs_us":float(max(abs(residual))*1e6),
    }


def pilot_phase_delay(edge_events:list[dict],frequencies:np.ndarray,
                      *,step_s:float=PHASE_GRID_STEP_S,max_delay_s:float=MAX_DELAY_S
                      )->dict:
    pilot=[x for x in edge_events if x["region"]=="pilot" and
           x["fit_qualified"] and not x["same_word_write"]]
    if len(pilot)<5:
        raise ValueError("not enough qualified changed-word pilot transitions")
    df=np.asarray([frequencies[x["after_tone_index"]]-
                   frequencies[x["before_tone_index"]] for x in pilot])
    obs=np.deg2rad([x["observed_apparent_phase_jump_deg"] for x in pilot])
    grid=np.arange(-max_delay_s,max_delay_s+step_s/2,step_s)
    phase_res=phase.wrap_phase(obs[:,None]-2*np.pi*df[:,None]*grid[None,:])
    score=np.mean(phase_res**2,axis=0)
    winner=int(np.argmin(score))
    return {
        "acquired_from_pilot_only":True,
        "pilot_changed_edges":int(len(pilot)),
        "effective_audio_phase_delay_s":float(grid[winner]),
        "pilot_phase_residual_rms_deg":float(np.rad2deg(np.sqrt(score[winner]))),
        "fit_domain":"received FM discriminator audio, not carrier RF phase",
        "physical_group_delay_calibration":False,
    }


def _errors_for_clock(edges, clock):
    pilot=[e for e in edges if e["region"]=="pilot"]
    hold=[e for e in edges if e["region"]=="heldout"]
    ids0=np.asarray([e["after_symbol_index"] for e in pilot])
    y0=np.asarray([e["crossing_time_s"] for e in pilot])
    # Baseline is nominal 150/s plus ONLY pilot-derived constant delay.
    nominal_start=float(np.mean(y0-ids0*NOMINAL_PERIOD))
    def errors(e,intercept,period):
        ids=np.asarray([z["after_symbol_index"] for z in e])
        times=np.asarray([z["crossing_time_s"] for z in e])
        return (times-(intercept+ids*period))*1e6
    ideal=errors(hold,nominal_start,NOMINAL_PERIOD)
    adjusted=errors(hold,clock["start_s"],clock["period_s"])
    return {
        "heldout_high_contrast_boundaries":len(hold),
        "nominal_schedule_median_abs_error_us":float(np.median(abs(ideal))),
        "nominal_schedule_rms_error_us":float(np.sqrt(np.mean(ideal*ideal))),
        "pilot_clock_schedule_median_abs_error_us":float(np.median(abs(adjusted))),
        "pilot_clock_schedule_rms_error_us":float(np.sqrt(np.mean(adjusted*adjusted))),
        "pilot_clock_schedule_max_abs_error_us":float(max(abs(adjusted))),
    }


def analyze_discriminator(f,symbols,frequency_hz,*,start_hint_s,time_origin_s,
                          fs=FS)->dict:
    """Core evidence analysis; all model fitting is on the first 16 slots."""
    edges=find_audio_midpoint_edges(
        f,symbols,frequency_hz,start_hint_s=start_hint_s,
        time_origin_s=time_origin_s,fs=fs)
    clock=pilot_clock(edges)
    edge_score=_errors_for_clock(edges,clock)
    obs=phase.fit_phases(f,symbols,frequency_hz,
                         first_s=clock["start_s"],time_origin_s=time_origin_s,
                         fs=fs,symbol_step_s=clock["period_s"])
    result=phase.summarize_edges(
        obs["phases_rad"],symbols,frequency_hz,
        obs["amplitude_hz"],obs["fit_rmse_hz"],
        symbol_step_s=clock["period_s"])
    lag=pilot_phase_delay(result["edges"],frequency_hz)
    corrected=[]
    for e in result["edges"]:
        if e["region"]!="heldout" or not e["fit_qualified"] or e["same_word_write"]:
            continue
        freq_diff=frequency_hz[e["after_tone_index"]]-frequency_hz[e["before_tone_index"]]
        measured=np.deg2rad(e["observed_apparent_phase_jump_deg"])
        predicted=2*np.pi*freq_diff*lag["effective_audio_phase_delay_s"]
        corrected.append(float(phase.wrap_phase(measured-predicted)))
    if len(corrected)<12:
        raise ValueError("too few qualified held-out changes for evaluation")
    return {
        "clock_training":clock,"phase_delay_training":lag,
        "heldout_clock_test":edge_score,
         "clock_adjusted_heldout_same_word":result["summary"]["heldout_same_word"],
        "clock_adjusted_heldout_changed_word":result["summary"]["heldout_changed_word"],
        "clock_and_pilot_delay_adjusted_heldout_changed_word":{
            "n":len(corrected),
            "rms_audio_phase_residual_deg":wrapped_rms_deg(np.asarray(corrected)),
            "median_abs_audio_phase_residual_deg":float(np.median(abs(
                np.rad2deg(corrected)))),
            "zero_residual_coherence":float(abs(np.mean(np.exp(1j*np.asarray(corrected))))),
        },
        "slot_audio_fit_rmse_median_hz":float(np.median(obs["fit_rmse_hz"][PILOT:])),
        "edge_observations":edges,
        "phase_edge_observations":result["edges"],
    }


def analyze_iq(iq_path:Path,header:Path=NEW_HEADER)->dict:
    words,symbols=parse_header(header)
    frequency=words.astype(float)/WORD_PER_HZ
    # Phase analyzer uses only the first 16 symbols to determine the
    # initial nominal schedule anchor. No heldout information enters.
    pilot=phase.analyze(iq_path,header)
    first=float(pilot["acquisition"]["pilot_only_refined_phase_timing_s"])
    f,amp=tones.demod_raw(iq_path,early_s=.7,late_s=1.45)
    result=analyze_discriminator(
        f,symbols,frequency,start_hint_s=first,time_origin_s=.7)
    with iq_path.open("rb") as fd:
        digest=hashlib.file_digest(fd,"sha256").hexdigest()
    return {
        "experiment":"TX-15Q measured B-chip audio transition timebase + phase transfer",
        "saved_IQ_sha256":digest,"iq_file":str(iq_path),
        "header_sha256":hashlib.sha256(header.read_bytes()).hexdigest(),
        "source_pilot_start_timing_s":first,
        "pilot_tone_correct":pilot["acquisition"]["training_correct_tones"],
        "heldout_tone_correct":pilot["acquisition"]["heldout_correct_tones"],
        "pilot_training_symbols":PILOT,"heldout_symbols":N-PILOT,
        "clock_or_delay_derived_from_heldout":False,
        "transmitted_by_analyzer":False,
        "rf_or_mcu_clock_calibrated":False,
        "audio_phase_not_rf_carrier_iq":True,
        "sdr_audio_frequency_discriminator_sps":FS,
        **result,
        "notes":[
            "Clock skew is the observed RF-audio register update schedule relative to the RTL-SDR sampling clock, not a calibrated absolute MCU clock.",
            "Crossing midpoint timing has filter/latency biases. Heldout boundaries test the frozen pilot schedule; they do not fit it.",
            "Pilot fitted phase delay is a single effective audio-domain correction, not independently identified FD6818B internal filter group delay.",
            "Small residual recovered tone phase does not establish arbitrary RF carrier IQ or emission purity.",
        ],
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--header",type=Path,default=NEW_HEADER)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite analysis")
    result=analyze_iq(args.iq,args.header)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+"\n")
    print("tone heldout",result["heldout_tone_correct"],"/48",
          "pilot strong",result["clock_training"]["pilot_indices"],
          "clock skew",round(result["clock_training"]["apparent_schedule_skew_ppm_relative_SDR"]),
          "ppm, heldout boundary MAD",
          round(result["heldout_clock_test"]["pilot_clock_schedule_median_abs_error_us"],1),"us")
    print("phase RMS, timing corrected",
          round(result["clock_adjusted_heldout_changed_word"]["rms_apparent_phase_jump_deg"],2),
          "deg; timing + pilot audio delay corrected",
          round(result["clock_and_pilot_delay_adjusted_heldout_changed_word"]["rms_audio_phase_residual_deg"],2),"deg")


if __name__=="__main__":
    main()
