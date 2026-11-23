#!/usr/bin/env python3
"""TX24B: read-only 4800-baud FSK + slow REG40 per-frame RF discriminator.

A fixed 16x55 + public sync (076b3e36) determines symbol-clock phase
and polarity, not the payload or register level. The payload is eight
64-bit long runs of alternately 0 and 1, repeated identically in all
eight RF frames. Only first and third frame (lowbyte 0x16) set a
deviation calibration. All other levels and even repeated baseline
frame #4 are HELDOUT.

This is a per-FRAME REG40 effect, NOT TX26 REG70 per-symbol 4FSK.
No radio, SDR tuner, GPIO, serial, or MCU firmware access. The source
must be physically qualified before claiming RF REG40+native FSK.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter,fftconvolve,find_peaks,sosfiltfilt

from k6_fsk_iq_edges import read_iq,FS
from modem import pager

BAUD=4800
LEVELS=(0x16,0x40,0x16,0x80,0x16,0x00,0x40,0x80)
PILOT=(0,2)
HELDOUT=(1,3,4,5,6,7)
KNOWN_BITS=np.repeat(np.arange(8,dtype=np.uint8)%2,64)
SOURCE_PAYLOAD_BYTES=np.packbits(KNOWN_BITS).tobytes()
GUARD_BYTES=b"\x5a\xa5"
PREFIX_BYTES=b"\xf8\x00"
N_PAYLOAD_BITS=33*16
PREAMBLE=bytes([pager.PREAMBLE_BYTE])*16
SYNC=pager.SYNC
MARK_BITS=np.unpackbits(np.frombuffer(PREAMBLE+SYNC,dtype=np.uint8))
MARK_LEN=len(MARK_BITS)
CENTRAL=np.concatenate([
    np.arange(g*64+16,g*64+48) for g in range(8)
])
assert len(KNOWN_BITS)==512 and len(MARK_BITS)==160
assert len(CENTRAL)==256
assert len(SOURCE_PAYLOAD_BYTES)==64

MIN_CORR=.92
MIN_MARK_SEPARATION_HZ=550.
MAX_PILOT_SPREAD=.12
MAX_HELDOUT_BASELINE_DRIFT=.12
MAX_NONBASE_REPEAT_DIFF=.12
POSITIVE_THRESHOLDS={0x00:-.03,0x40:.08,0x80:.20}

def _match(stream,marker):
    """Public preamble/sync normalized frequency discriminator correlation."""
    template=marker.astype(float)*2-1
    template-=template.mean()
    denom=float(template@template)
    x=np.asarray(stream,dtype=float)
    product=fftconvolve(x,template[::-1],mode="valid")
    sums=np.concatenate(([0.],np.cumsum(x)))
    squares=np.concatenate(([0.],np.cumsum(x*x)))
    n=len(template)
    spread=np.maximum(
        squares[n:]-squares[:-n]-(sums[n:]-sums[:-n])**2/n,1e-9)
    return product/np.sqrt(spread*denom)


def prefix_and_guard_candidates(data_bits:np.ndarray):
    """Select only documented zero-prefix vs 16-bit F800 artifact states.

    The last sacrificial A55A word is visible ONLY without the
    leading F800 word. On the leading-artifact path, all 512
    original known bits survive and replace the guard.
    """
    if len(data_bits)!=N_PAYLOAD_BITS:
        raise ValueError("expected exactly 528 emitted FSK data bits")
    bytes_received=np.packbits(data_bits).tobytes()
    nominal=(bytes_received[:2]==SOURCE_PAYLOAD_BYTES[:2] and
             bytes_received[-2:]==GUARD_BYTES)
    shifted=(bytes_received[:2]==PREFIX_BYTES and
             bytes_received[-2:]==SOURCE_PAYLOAD_BYTES[-2:])
    return [offset for yes,offset in ((nominal,0),(shifted,16)) if yes]


def find_known_frames(freq:np.ndarray,*,fs=FS,
                      rate_factors=(1.,),
                      phases=(.125,.375,.625,.875)):
    """Acquire using ONLY the public 160-bit marker.

    No expected source payload, register states, CRC, or guard is used
    to choose start time, polarity, rate, frequency reference or
    sampling phase. Known data and guard are only scored afterwards.
    """
    freq=np.asarray(freq)
    if freq.ndim!=1 or len(freq)<.2*fs or not np.isfinite(freq).all():
        raise ValueError("bad/missing FSK discriminator trace")
    grid=np.arange(len(freq),dtype=float)
    candidates=[]
    for factor in rate_factors:
        rate=BAUD*factor
        for fraction in phases:
            samp_per_bit=fs/rate
            pos=(np.arange(int(len(freq)/samp_per_bit))+fraction)*samp_per_bit
            obs=np.interp(pos,grid,freq)
            if len(obs)<MARK_LEN+N_PAYLOAD_BITS:
                continue
            correlation=_match(obs,MARK_BITS)
            limit=len(obs)-(MARK_LEN+N_PAYLOAD_BITS)
            peaks,_=find_peaks(np.abs(correlation[:limit+1]),
                               height=MIN_CORR,distance=MARK_LEN//2)
            for start in peaks:
                c=float(correlation[start])
                marker_freq=obs[start:start+MARK_LEN]
                zeros=marker_freq[MARK_BITS==0]
                ones=marker_freq[MARK_BITS==1]
                f_zero=float(np.median(zeros))
                f_one=float(np.median(ones))
                if abs(f_one-f_zero)<MIN_MARK_SEPARATION_HZ:
                    continue
                polarity=1 if f_one>f_zero else -1
                threshold=(f_zero+f_one)/2
                marker_decision=((marker_freq-threshold)*polarity>0)
                bad=int(np.count_nonzero(marker_decision!=MARK_BITS))
                if bad>8:continue
                data=obs[start+MARK_LEN:start+MARK_LEN+N_PAYLOAD_BITS]
                data_bits=((data-threshold)*polarity>0).astype(np.uint8)
                candidates.append(dict(
                    marker_start_s=(start+fraction)/rate,
                    payload_start_s=(start+fraction+MARK_LEN)/rate,
                    corr=abs(c),signed_corr=c,
                    marker_errors=bad,
                    marker_fsk_separation_hz=abs(f_one-f_zero),
                    physical_data_freq=data,
                    physical_data_bits=data_bits,
                    polarity=polarity,
                    marker_threshold_hz=threshold,
                    rate_hz=rate,
                    marker_phase=fraction))
    # Deduplicate candidates acquired at multiple marker sampling phases.
    candidates.sort(key=lambda x:x["payload_start_s"])
    groups=[]
    for c in candidates:
        if groups and abs(c["payload_start_s"]-
                          groups[-1][-1]["payload_start_s"])<.009:
            groups[-1].append(c)
        else:
            groups.append([c])
    selected=[]
    for group in groups:
        group.sort(key=lambda x:(x["marker_errors"],-x["corr"]))
        selected.append(group[0])
    return selected


def score_frequency(freq:np.ndarray,*,fs=FS):
    frames=find_known_frames(freq,fs=fs)
    if len(frames)!=len(LEVELS):
        raise ValueError(
            f"Expected 8 public-sync FSK frames, discovered {len(frames)}")
    out=[]
    for i,(f,reg) in enumerate(zip(frames,LEVELS)):
        bits=f["physical_data_bits"]
        offsets=prefix_and_guard_candidates(bits)
        if len(offsets)!=1:
            decision="ambiguous" if len(offsets)>1 else "neither"
            raise ValueError(f"frame {i}: no unique predeclared 0/16-bit guard state: {decision}")
        shift=offsets[0]
        # Heldout source word values are used only after public-sync
        # signal acquisition and 0/16-bit sacrificial guard decision.
        interior=f["physical_data_freq"][CENTRAL+shift]
        marks=KNOWN_BITS[CENTRAL].astype(bool)
        lower=float(np.median(interior[~marks]))
        upper=float(np.median(interior[marks]))
        separation=abs(upper-lower)
        delta=(interior-f["marker_threshold_hz"])*f["polarity"]
        recovered=(delta>0).astype(np.uint8)
        errors=int(np.count_nonzero(recovered!=KNOWN_BITS[CENTRAL]))
        out.append(dict(
            index=i,reg40_word=0x3500|reg,
            lowbyte=reg,pilot=i in PILOT,
            prefix_displacement_bits=shift,
            marker_start_s=round(f["marker_start_s"],7),
            payload_start_s=round(f["payload_start_s"],7),
            sync_correlation=round(f["corr"],6),
            sync_errors=f["marker_errors"],
            marker_fsk_separation_hz=f["marker_fsk_separation_hz"],
            discriminator_fsk_separation_hz=separation,
            lower_sign_hz=lower,upper_sign_hz=upper,
            payload_interior_bits=len(CENTRAL),
            known_bit_errors=errors))
    reference=np.mean([out[i]["discriminator_fsk_separation_hz"]
                       for i in PILOT])
    for f in out:
        f["separation_relative_to_pilot"]=(
            f["discriminator_fsk_separation_hz"]/reference)
    groups={k:[f["separation_relative_to_pilot"] for f in out
               if f["lowbyte"]==k] for k in sorted(set(LEVELS))}
    controls=groups[0x16]
    by_level={str(k):float(np.median(v)) for k,v in groups.items()}
    pilot_diff=abs(out[0]["separation_relative_to_pilot"]-
                   out[2]["separation_relative_to_pilot"])
    controls_spread=max(controls)-min(controls)
    replay_diff=max(abs(vals[0]-vals[1])/
                    max(np.mean(vals),1e-9)
                    for k,vals in groups.items() if k in (0x40,0x80))
    checks=dict(
        eight_public_sync_frames=bool(len(out)==8),
        zero_public_sync_errors=bool(
            all(f["sync_errors"]==0 for f in out)),
        all_known_steady_sign_bits_correct=bool(
            sum(f["known_bit_errors"] for f in out)==0),
        pilot_reference_stable=bool(pilot_diff<=MAX_PILOT_SPREAD),
        heldout_reference_stable=bool(
            controls_spread<=MAX_HELDOUT_BASELINE_DRIFT),
        repeated_nonbaseline_levels_stable=bool(
            replay_diff<=MAX_NONBASE_REPEAT_DIFF),
        slow_REG40_effect_monotone=bool(
            by_level["0"] < by_level["22"] <
            by_level["64"] < by_level["128"]),
        REG40_effect_above_separate_thresholds=bool(
            by_level["0"]<=1+POSITIVE_THRESHOLDS[0x00] and
            by_level["64"]>=1+POSITIVE_THRESHOLDS[0x40] and
            by_level["128"]>=1+POSITIVE_THRESHOLDS[0x80]))
    return dict(
        experiment="TX24B B-chip REG40 slow native-FSK frame mode compatibility",
        offline_analysis_only=True,
        radio_touched=False,
        hardware_native_FSK_coexistence_unproven=True,
        source_symbol_rate=BAUD,
        original_payload_bits_per_frame=512,
        sacrificial_guard_bits=16,
        source_reg70_word=0x0095,
        source_reg72_baud_word=0xc194,
        known_source_payload_hex=SOURCE_PAYLOAD_BYTES.hex(),
        public_sync_hex=(PREAMBLE+SYNC).hex(),
        pilot_indices=list(PILOT),
        heldout_indices=list(HELDOUT),
        per_frame_lowbyte=list(LEVELS),
        source_reg40_constant_within_each_frame=True,
        no_symbol_rate_reg40_or_true_four_FSK_demonstrated=True,
        precommitted_thresholds=dict(
            min_sync_corr=MIN_CORR,
            min_fsk_sync_separation_hz=MIN_MARK_SEPARATION_HZ,
            max_pilot_relative_diff=MAX_PILOT_SPREAD,
            max_heldout_baseline_spread=MAX_HELDOUT_BASELINE_DRIFT,
            max_nonbaseline_repeat_fraction=MAX_NONBASE_REPEAT_DIFF,
            expected_level_effect=POSITIVE_THRESHOLDS),
        normalized_by_level=by_level,
        conditions=checks,
        candidate_native_FSK_REG40_effect_qualified=bool(all(checks.values())),
        total_heldout_interior_bits=sum(x["payload_interior_bits"] for x in out),
        heldout_bit_errors=sum(x["known_bit_errors"] for x in out),
        frames=out,
        limitations=[
            "No hardware measurements: this read-only code must be physically tested on B.",
            "B-chip native REG40 may be latched or ignored in native FSK mode.",
            "REG40 is changed only between frames, never at 4800-baud symbol rate.",
            "Known bits are heldout from public-sync timing/polarity and amplitude normalization.",
            "A positive cannot establish true 4FSK, symbol-rate timing or arbitrary I/Q.",
        ])


def score_raw_iq(path:Path):
    iq=read_iq(path,duration=4.5,offset=280500.)
    # Filter the *complex RF IQ* before FM discrimination to preserve
    # known 4800-baud bit centers and avoid its high-frequency noise.
    filtered=sosfiltfilt(butter(3,8000,fs=FS,output="sos"),iq)
    frequency=np.angle(filtered[1:]*filtered[:-1].conj())*FS/(2*np.pi)
    frequency=uniform_filter1d(frequency,size=5)
    score=score_frequency(frequency)
    with path.open("rb") as f:
        sha=hashlib.file_digest(f,"sha256").hexdigest()
    score["source_iq"]=str(path)
    score["source_iq_sha256"]=sha
    return score


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refuse to overwrite existing IQ scoring report")
    score=score_raw_iq(args.iq)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(score,indent=2)+"\n")
    print("TX24B candidate per-frame effect",score[
        "candidate_native_FSK_REG40_effect_qualified"])
    print("heldout BER",score["heldout_bit_errors"],"/",
          score["total_heldout_interior_bits"])
    print("levels",score["normalized_by_level"])


if __name__=="__main__":
    main()
