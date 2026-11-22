#!/usr/bin/env python3
"""Offline native-FSK two-frame boundary analysis from air-coupled IQ.

Recreates both known TX FIFO truth sequences from the exact bounded
two-frame module's word algorithm. Each frame's first 512 bits are
known for independent acquisition; the final 512 bits of each frame
are held out. Measures extra interframe payload time and whether the
carrier envelope drops, not RF phase continuity or compliance.

No K6, serial, live SDR, flash, PTT or module installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, sosfiltfilt

from k6_fsk_lab_vector import make_probe_vector,bits_from_words,build_vector
from k6_fsk_iq_edges import read_iq,FS
from k6_fsk_lab_decode import decode_samples

BAUD=4800
WORDS=64
BITS=WORDS*16
TRAIN=512
RX_OFFSET_HZ=280_500.0
FILTER_HZ=8_000.0


def known_frames() -> tuple[dict,dict]:
    """Must mirror c/k6/mod/fsk_boundary.c word_for() exactly."""
    orig,_=make_probe_vector(word_count=WORDS,seed=0xACE1)
    first=tuple(orig[:56])+tuple([0]*8)
    second=tuple([0]*8+[0xffff]*8+[~x&0xffff for x in orig[16:]])
    if len(first)!=WORDS or len(second)!=WORDS:
        raise AssertionError("frame word count mismatch")
    _,a=build_vector(word_count=WORDS,gain=21,baud=BAUD,
                     data_bits=bits_from_words(first))
    _,b=build_vector(word_count=WORDS,gain=21,baud=BAUD,
                     data_bits=bits_from_words(second))
    return a,b


def compute_boundary(first:dict,second:dict, iq:np.ndarray, *,
                     fs:int=FS)->dict:
    if (not first.get("locked") or not second.get("locked")
        or first.get("training_bits")!=TRAIN
        or second.get("training_bits")!=TRAIN
        or first.get("held_out_tail_bits")!=BITS-TRAIN
        or second.get("held_out_tail_bits")!=BITS-TRAIN
        or first.get("held_out_tail_errors",100)>4
        or second.get("held_out_tail_errors",100)>4
        or min(first["best_training_correlation"],
               second["best_training_correlation"])<.93):
        raise ValueError("insufficient known-training frame acquisition")
    t1=float(first["detected_first_payload_bit_center_s"])
    t2=float(second["detected_first_payload_bit_center_s"])
    payload_time=BITS/BAUD
    gap=t2-t1-payload_time
    if not 0.001<gap<.3:
        raise ValueError("frame order or interframe interval out of bounds")
    x=np.asarray(iq)
    if x.ndim!=1 or len(x)<fs*1.4:
        raise ValueError("I/Q recording too short")
    # 1ms RF envelope smooths individual sample noise but retains
    # ms-scale carrier outages. It cannot detect phase discontinuity.
    rms=uniform_filter1d(abs(x),size=max(64,round(fs*.001)))
    before=(np.arange(len(x))/fs>=t1+(BITS-128)/BAUD+.003) & (
        np.arange(len(x))/fs<=t1+BITS/BAUD-.003)
    after=(np.arange(len(x))/fs>=t2+.003) & (
        np.arange(len(x))/fs<=t2+128/BAUD-.003)
    region=(np.arange(len(x))/fs>t1+payload_time) & (
        np.arange(len(x))/fs<t2)
    if not before.any() or not after.any() or not region.any():
        raise ValueError("marker plateau or RF gap outside recording")
    median_before=float(np.median(rms[before]))
    median_after=float(np.median(rms[after]))
    baseline=float(np.median(rms[int(.35*fs):int(.60*fs)]))
    min_gap=float(np.min(rms[region]))
    mean_gap=float(np.median(rms[region]))
    if min(median_before,median_after)<baseline*4:
        raise ValueError("receiver RF marker too close to its baseline")
    filt=sosfiltfilt(butter(4,10000,fs=fs,output="sos"),x)
    freq=np.angle(filt[1:]*np.conj(filt[:-1]))*fs/(2*np.pi)
    low0=float(np.median(freq[np.flatnonzero(before)[:-1]]))
    low1=float(np.median(freq[np.flatnonzero(after)[:-1]]))
    gap_f=freq[np.flatnonzero(region)[:-1]]
    return {
        "first_frame_first_bit_center_s":round(t1,7),
        "second_frame_first_bit_center_s":round(t2,7),
        "first_to_second_payload_start_s":round(t2-t1,7),
        "single_frame_payload_duration_s":round(payload_time,7),
        "additional_rearm_and_framing_time_s":round(gap,7),
        "additional_rearm_and_framing_time_ms":round(gap*1000,3),
        "equivalent_4800_bit_chips_not_payload":round(gap*BAUD,2),
        "first_frame_heldout_bit_errors":first["held_out_tail_errors"],
        "second_frame_heldout_bit_errors":second["held_out_tail_errors"],
        "first_frame_training_correlation":first["best_training_correlation"],
        "second_frame_training_correlation":second["best_training_correlation"],
        "median_rf_envelope_before_boundary_adc":round(median_before,4),
        "median_rf_envelope_after_boundary_adc":round(median_after,4),
        "median_rf_envelope_during_nonpayload_interval_adc":round(mean_gap,4),
        "minimum_1ms_smoothed_envelope_during_interval_adc":round(min_gap,4),
        "minimum_boundary_envelope_fraction_vs_holds":round(
            min_gap/max((median_before+median_after)/2,1e-9),5),
        "early_capture_background_envelope_adc":round(baseline,4),
        "last_first_frame_zero_marker_frequency_hz_relative":round(low0,2),
        "first_second_frame_zero_marker_frequency_hz_relative":round(low1,2),
        "interframe_frequency_q10_q50_q90_hz_relative":[round(float(y),2)
            for y in np.quantile(gap_f,[.1,.5,.9])],
        "tx_was_released_between_frames_by_module":False,
        "hardware_modem_was_rearmed_between_frames":True,
        "carrier_absence_established":bool(min_gap<baseline*2),
        "rf_phase_continuity_established":False,
        "rf_streaming_established":False,
        "note":"Observed extra frame time includes unknown RFIC preamble/sync, interrupt polling, SPI rearm and modem state. Air-coupled receiver is not calibrated."
    }


def analyze_file(path:Path,*,offset_hz=RX_OFFSET_HZ)->dict:
    with path.open("rb") as handle:
        digest=hashlib.file_digest(handle,"sha256").hexdigest()
    iq=read_iq(path,duration=1.8,offset=offset_hz)
    manifests=known_frames()
    frames=[decode_samples(iq,m,train_bits=TRAIN,lowpass_hz=FILTER_HZ)
            for m in manifests]
    data=compute_boundary(frames[0],frames[1],iq)
    return {
        "project":"Flux",
        "experiment":"FSK-09 two restarted fixed FIFO frames under one TX lease",
        "offline_analysis":True,
        "rf_transmission_initiated_by_analyzer":False,
        "source_capture_path":str(path),
        "source_capture_sha256":digest,
        "sample_rate_processing_hz":FS,
        "carrier_shift_hz_assumed":offset_hz,
        "frame_manifests_sha256":[m["payload_sha256_le_words"] for m in manifests],
        "frame_acquisition":frames,
        "boundary":data,
        "limitations":[
            "This proves two fixed-length frames and added framing time, not transparent FIFO refill.",
            "Amplitude continuity does not prove RF complex phase continuity.",
            "Results are from air-coupled SDR, not calibrated spectrum analyzer.",
            "Each 512-bit training prefix determines its own frame timing; tail bits are reserved.",
            "Estimated duration is accurate to roughly a native-bit interval or less, not absolute RFIC state timing."
        ]
    }


def main(argv=None)->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite existing report")
    try:
        r=analyze_file(args.iq)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(r,indent=2)+"\n")
    except (OSError,TypeError,ValueError) as exc:
        p.error(str(exc))
    b=r["boundary"]
    print("Two-frame RF: both 512-bit heldout tails",
          b["first_frame_heldout_bit_errors"],
          b["second_frame_heldout_bit_errors"],"errors")
    print("Extra non-payload interval:",
          b["additional_rearm_and_framing_time_ms"],"ms")
    print("Minimum smoothed RF amplitude fraction:",
          b["minimum_boundary_envelope_fraction_vs_holds"])
    print("Saved:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
