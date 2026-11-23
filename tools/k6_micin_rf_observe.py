#!/usr/bin/env python3
"""Offline MICIN 4-FSK receiver bench: independently score pilot/held-out RF.

This command accepts **already captured** complex baseband IQ at 48 ksample/s
or raw RTL-SDR interleaved uint8 IQ at 1.024 Msample/s. It NEVER accesses
a radio, soundcard, USB, serial link, or live SDR; it never plays or transmits.

The absolute RF downmix frequency MUST be supplied and verified from
capture metadata. Frequency is *relative*, not calibrated RF deviation
until a real source-to-MICIN electrical path has been verified.

A single 16-symbol pilot determines timing and affine voltage->frequency
calibration; 32 held-out four-level symbols receive no tuning.
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
from k6_micin_burst_lab import (
    FS, BAUD, GUARD_MS, PILOT_SYMBOLS, make_cycle, reference_symbols
)

CYCLE_N = 2112
SPS = FS // BAUD
MIN_GATE_MS = 65
MAX_GATE_MS = 200
PILOT_MIN_CORR = .91
# One sample is 20.83 us. A near-symbol-time search is unnecessary.
SEARCH_STEP = 4


def from_file(path: Path, *, rate: int, channel_offset_hz: float = 0.) -> np.ndarray:
    """Convert saved I/Q. U8 requires explicit SDR LO correction in Hz."""
    raw=np.load(path,mmap_mode="r",allow_pickle=False)
    if (raw.ndim != 1 or len(raw) == 0 or
            not np.isfinite(channel_offset_hz) or
            abs(channel_offset_hz) >= rate/2):
        raise ValueError("invalid archived RF capture dimensions or mixing offset")
    if raw.dtype == np.uint8:
        if (rate != 1_024_000 or len(raw) % 2 or
                not int(.1*rate) <= len(raw)//2 <= int(20*rate)):
            raise ValueError("interleaved U8 requires 1.024 MS/s and .1..20s of IQ")
        i=raw[::2].astype(np.float32)-127.5
        q=raw[1::2].astype(np.float32)-127.5
        z=i+1j*q
        t=np.arange(len(z),dtype=float)/rate
        z*=np.exp(-2j*np.pi*channel_offset_hz*t)
        return resample_poly(z,3,64,window=("kaiser",10)).astype(np.complex64)
    if not np.issubdtype(raw.dtype,np.complexfloating) or rate!=FS:
        raise ValueError("complex IQ requires 48 ksample/s; U8 IQ requires 1.024 MS/s")
    if len(raw)<int(.1*FS) or len(raw)>int(20*FS) or not np.isfinite(raw).all():
        raise ValueError("invalid 48 ksample/s complex-IQ array")
    if abs(channel_offset_hz)>0:
        return (raw*np.exp(-2j*np.pi*channel_offset_hz*np.arange(len(raw))/FS)).astype(np.complex64)
    return np.asarray(raw,np.complex64)


def demodulate(iq:np.ndarray) -> tuple[np.ndarray,np.ndarray]:
    iq=np.asarray(iq)
    if (iq.ndim!=1 or iq.dtype.kind!='c' or len(iq)<int(.1*FS)
            or not np.isfinite(iq).all()):
        raise ValueError("IQ must be finite complex 48-kHz samples over .1s")
    a=abs(iq)
    if float(np.max(a)) < 1e-5:
        raise ValueError("no RF signal")
    # Bandlimit to expected ~3 kHz 4-FSK deviations and pulse shape.
    z=sosfiltfilt(butter(4,6500,fs=FS,output='sos'),iq)
    freq=np.zeros(len(z),dtype=float)
    freq[1:]=np.angle(z[1:]*np.conj(z[:-1]))*FS/(2*np.pi)
    freq[0]=freq[1]
    freq=uniform_filter1d(freq,size=9,mode='nearest')
    envelope=uniform_filter1d(abs(z),size=48,mode='nearest')
    return freq,envelope


def active_window(envelope:np.ndarray) -> tuple[int,int,dict]:
    """Require an isolated RF burst, not one continuous or unverified signal."""
    envelope=np.asarray(envelope,float)
    if len(envelope)<int(.1*FS) or not np.isfinite(envelope).all():
        raise ValueError("insufficient RF envelope")
    low=float(np.quantile(envelope,.05))
    high=float(np.quantile(envelope,.98))
    if high<max(1e-4,3.0*low):
        raise ValueError("unable to distinguish RF from no-TX baseline")
    threshold=low+.24*(high-low)
    hot=envelope>threshold
    changes=np.diff(np.r_[False,hot,False].astype(np.int8))
    spans=[(int(s),int(e)) for s,e in zip(np.flatnonzero(changes==1),
                                           np.flatnonzero(changes==-1))
           if MIN_GATE_MS/1000*FS <= e-s <= MAX_GATE_MS/1000*FS]
    if not spans:
        raise ValueError("no isolated bounded 65..200 ms RF burst")
    s,e=max(spans,key=lambda p:p[1]-p[0])
    pad=int(.008*FS)
    if s<pad or e>len(envelope)-pad:
        raise ValueError("RF capture lacks >=8ms unkeyed baseline on both sides")
    return s,e,{
        "activity_start_s":s/FS,"activity_end_s":e/FS,
        "activity_duration_ms":(e-s)/FS*1000,
        "envelope_floor":low,"envelope_top":high,
        "activity_threshold":threshold,
        "no_rf_burst_proven_without_independent_tx_metadata":True,
    }


def acquire_and_score(freq: np.ndarray, gate: tuple[int,int], *,
                      pattern="stress", gain_lower_hz=80.,
                      search_step=SEARCH_STEP) -> dict:
    """Find periodic pilot from received RF; freeze offset/gain for heldout."""
    freq=np.asarray(freq)
    if (freq.ndim!=1 or not np.isfinite(freq).all() or
            search_step<1 or search_step>20 or
            not np.isfinite(gain_lower_hz) or gain_lower_hz<=0):
        raise ValueError("invalid receiver/known-pilot parameters")
    start,end=gate
    if not (0<=start<end<=len(freq) and end-start>=int(.065*FS)):
        raise ValueError("invalid bounded RF gate")
    ref,truth=make_cycle(pattern=pattern)
    indices=round(GUARD_MS*FS/1000)+(np.arange(len(truth))+.5)*SPS
    index=indices.astype(int)
    # Fit against the entire independently specified SHAPED pilot waveform,
    # not 16 sparse centers; this makes accidental 16-point false locks less
    # likely. Its known 16-symbol span only is used (none of the holdout).
    pilot_first=round(GUARD_MS*FS/1000)
    pilot_last=pilot_first+PILOT_SYMBOLS*SPS
    pilot_x=ref[pilot_first:pilot_last]/float(np.max(abs(ref)))
    pilot_x_mean=float(np.mean(pilot_x))
    pilot_x_centered=pilot_x-pilot_x_mean
    pilot_x_power=float(pilot_x_centered@pilot_x_centered)
    pilot_x_norm=float(np.sqrt(pilot_x_power))
    # Only admit candidates with a complete reference cycle, including
    # heldout, entirely inside the observed RF burst. This is geometry
    # (not looking at actual heldout frequencies).
    first=start
    last=end-len(ref)
    if last<first:
        raise ValueError("short RF gate cannot hold complete waveform period")
    candidates=range(first,last+1,search_step)
    best=None
    for k in candidates:
        values=freq[k+pilot_first:k+pilot_last]
        centered=values-float(np.mean(values))
        slope=float(centered@pilot_x_centered/pilot_x_power)
        intercept=float(np.mean(values)-slope*pilot_x_mean)
        scale=np.linalg.norm(centered)*pilot_x_norm
        corr=float(centered@pilot_x_centered/scale) if scale>0 else 0.
        # For calibration require monotonic four-level deviation (positive slope).
        if not np.isfinite(slope) or slope<=gain_lower_hz:
            continue
        residual=float(np.mean((values-(pilot_x*slope+intercept))**2))
        candidate=(corr,-residual,k,slope,intercept)
        if best is None or candidate[:2]>best[:2]:
            best=candidate
    if best is None or best[0]<PILOT_MIN_CORR:
        raise ValueError("known 16-symbol MICIN pilot not acquired")
    corr,neg_mse,begin,gain,offset=best
    # No timing, carrier offset, gain or model parameters come from
    # these 32 held-out symbol-center samples.
    measured=freq[begin+index]
    decoded=np.argmin(np.abs(((measured-offset)/gain)[:,None]-
                             LEVELS[None,:]),axis=1).astype(np.uint8)
    held=decoded[PILOT_SYMBOLS:]
    expected=truth[PILOT_SYMBOLS:]
    inferred=(measured-offset)/gain
    plateau={}
    for level in range(4):
        ix=np.flatnonzero(expected==level)+PILOT_SYMBOLS
        plateau[str(level)]={
            "num_heldout":len(ix),
            "median_level_estimate":float(np.median(inferred[ix])),
            "first_level_estimate":float(inferred[ix[0]]),
            "last_level_estimate":float(inferred[ix[-1]]),
            "heldout_level_errors":int(np.count_nonzero(decoded[ix]!=level)),
        }
    # Waveform residual is also frozen-pilot: offset and gain are not
    # refit to either the known shaped holdout waveform or its outcomes.
    hold_slice=slice(begin+int(index[PILOT_SYMBOLS])-SPS//2,
                     begin+int(index[-1])+SPS//2)
    sample_index=np.arange(hold_slice.start,hold_slice.stop)
    source_index=sample_index-begin
    shaped=ref[source_index]/float(np.max(abs(ref)))
    predicted=offset+gain*shaped
    measured_wave=freq[hold_slice]
    return {
        "known_pilot_acquired":True,
        "data_heldout_from_acquisition":True,
        "pilot_symbol_count":PILOT_SYMBOLS,
        "heldout_symbol_count":len(expected),
        "pilot_correlation":corr,
        "pilot_normalized_frequency_rmse":float(np.sqrt(-neg_mse)/gain),
        "pilot_only_gain_hz_per_normalized_level":float(gain),
        "pilot_only_frequency_offset_hz":float(offset),
        "first_cycle_start_s":begin/FS,
        "holdout_symbol_errors":int(np.count_nonzero(held!=expected)),
        "holdout_normalized_level_rmse":float(np.sqrt(np.mean(
            (inferred[PILOT_SYMBOLS:]-LEVELS[expected])**2))),
        "heldout_frequency_waveform_rmse_hz_frozen_pilot":
            float(np.sqrt(np.mean((measured_wave-predicted)**2))),
        "heldout_level_details":plateau,
        "heldout_recovered_symbols":held.tolist(),
        "heldout_expected_symbols":expected.tolist(),
        "not_full_complex_iq_evm":True,
        "not_a_calibrated_transmitter_deviation_measurement":True,
    }


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True,help="Existing .npy interleaved U8 or complex array")
    p.add_argument("--sample-rate",type=int,choices=(48000,1024000),required=True)
    p.add_argument("--rf-offset-hz",type=float,default=0.,
                   help="Observed RF carrier offset relative to SDR center; verify independently")
    p.add_argument("--pattern",choices=("stress","balanced"),required=True)
    p.add_argument("--report",type=Path,required=True)
    args=p.parse_args(argv)
    if args.report.exists():
        p.error("will not overwrite existing receiver analysis")
    try:
        iq=from_file(args.iq,rate=args.sample_rate,channel_offset_hz=args.rf_offset_hz)
        f,e=demodulate(iq)
        start,end,activity=active_window(e)
        result=acquire_and_score(f,(start,end),pattern=args.pattern)
        result.update({
            "experiment":"Flux TX-12D offline MICIN four-level RF receiver",
            "raw_capture_path":str(args.iq),
            "source_file_sha256":hashlib.sha256(args.iq.read_bytes()).hexdigest(),
            "sample_rate_input_hz":args.sample_rate,
            "provided_rf_offset_hz":args.rf_offset_hz,
            "pattern":args.pattern,
            "radio_control_performed":False,
            "audio_playback_performed":False,
            "rf_transmitted_by_this_tool":False,
            "physical_micin_hardware_verified":False,
            "iq_source_provenance_not_attested_by_this_tool":True,
            "activity":activity,
            "caveats":[
                "Raw IQ and supplied offset metadata may be synthetic, stale or otherwise invalid",
                "Pilot-only timing/deviation fitting; holdout measured independently",
                "RF startup/truncation estimated from received envelope not independent TX GPIO",
                "Assumes 48-kHz receiver clock stable and 1200-symbol/s source, no drift correction",
                "Frequency discriminator is relative to supplied RF carrier; no calibrated power/spectrum metrics",
                "No physical MICIN route or RF transmitted by this analysis tool",
            ]
        })
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+"\n")
        print("pilot_corr",round(result["pilot_correlation"],4),
              "holdout_errors",result["holdout_symbol_errors"],
              "of",result["heldout_symbol_count"],
              "fitted_dev_hz",round(result["pilot_only_gain_hz_per_normalized_level"],1))
        print("Saved",args.report)
    except (ValueError,TypeError,OSError) as exc:
        p.error(str(exc))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
