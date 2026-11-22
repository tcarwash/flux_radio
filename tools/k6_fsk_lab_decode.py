#!/usr/bin/env python3
"""Offline FSK known-training acquisition with a truly held-out data tail.

Reads saved RTL-SDR I/Q and a deterministic Flux lab manifest. Searches
unknown start time, bit clock and sampling phase using ONLY training bits,
then tests the remaining tail without using it for synchronization. No
radio/SDR device, serial interface, firmware flash, or RF transmission.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, fftconvolve, sosfiltfilt
from k6_fsk_iq_edges import read_iq, FS, RAW_RATE, CARRIER_SHIFT

def _bit_truth(manifest: dict) -> np.ndarray:
    bits=manifest.get("payload_bits_msb_each_byte_lower_byte_first","")
    if not isinstance(bits,str) or len(bits)<96 or any(x not in "01" for x in bits):
        raise ValueError("need explicit valid FIFO bit truth")
    expected=manifest.get("payload_bits",len(bits))
    if len(bits)!=expected:
        raise ValueError("manifest payload_bits length disagrees with truth")
    raw=bytes.fromhex(manifest.get("payload_bytes_hex",""))
    if not raw or len(raw)*8!=len(bits):
        raise ValueError("manifest FIFO bytes differ from bit count")
    parsed=np.unpackbits(np.frombuffer(raw,dtype=np.uint8))
    expected_bits=np.fromiter((int(c) for c in bits),dtype=np.uint8)
    if not np.array_equal(parsed,expected_bits):
        raise ValueError("manifest FIFO bytes differ from explicit bit order")
    sha=manifest.get("payload_sha256_le_words")
    if sha and hashlib.sha256(raw).hexdigest()!=sha:
        raise ValueError("manifest FIFO checksum differs from payload")
    return expected_bits


def _normalized_match(arr: np.ndarray, template: np.ndarray):
    tpl=template.astype(np.float64)
    tpl -= tpl.mean()
    norm_t=float(np.dot(tpl,tpl))
    if norm_t<1:
        raise ValueError("training pattern lacks bit diversity")
    n=len(tpl)
    if len(arr)<n:
        return None
    # Prefix-only normalized dot product across every candidate start,
    # using FFT correlation rather than a huge sliding-time matrix.
    ab=np.asarray(arr,dtype=np.float64)
    num=fftconvolve(ab,tpl[::-1],mode="valid")
    csum=np.concatenate(([0.0],np.cumsum(ab)))
    csum2=np.concatenate(([0.0],np.cumsum(ab*ab)))
    s=csum[n:]-csum[:-n]
    variance=np.maximum((csum2[n:]-csum2[:-n])-s*s/n,1e-9)
    corr=num/np.sqrt(variance*norm_t)
    return corr


def decode_samples(iq: np.ndarray, manifest: dict, *,
                   fs: int=FS, train_bits: int=512,
                   lowpass_hz: float=8000., min_correlation=.42,
                   sample_rate_trials: tuple[float,...]=(.995,1.,1.005),
                   phases: tuple[float,...]=(.125,.375,.625,.875)) -> dict:
    truth=_bit_truth(manifest)
    baud=float(manifest.get("native_baud",0))
    if not baud or baud>8000 or fs/baud<12:
        raise ValueError("unsupported symbol clock / sample-rate ratio")
    if not 64 <= train_bits <= len(truth)-32:
        raise ValueError("training bits must leave at least 32 unseen bits")
    if not 100 < lowpass_hz < fs/2:
        raise ValueError("invalid I/Q lowpass cutoff")
    if not 0 < min_correlation <= 1:
        raise ValueError("invalid minimum training correlation")
    z=np.asarray(iq)
    if z.ndim!=1 or len(z)<2*fs//10:
        raise ValueError("short complex I/Q capture")
    filt=sosfiltfilt(butter(3,lowpass_hz,fs=fs,output="sos"),z)
    d=np.angle(filt[1:]*np.conj(filt[:-1]))*fs/(2*np.pi)
    d=uniform_filter1d(d,size=max(3,int(round(.16*fs/baud))))
    prefix=truth[:train_bits]*2.-1.
    best=None
    for factor in sample_rate_trials:
        bit_rate=baud*factor
        if bit_rate<=0:
            raise ValueError("invalid rate candidate")
        stride=fs/bit_rate
        for phase in phases:
            if not 0 <= phase <1:
                raise ValueError("invalid sampling-phase candidate")
            times=(np.arange(int(len(d)//stride),dtype=float)+phase)*stride
            obs=np.interp(times,np.arange(len(d)),d)
            if len(obs)<len(truth):
                continue
            # The training window must leave a full unseen tail in I/Q.
            limit=len(obs)-len(truth)+1
            corr=_normalized_match(obs,prefix)
            if corr is None:continue
            corr=corr[:limit]
            k=int(np.argmax(np.abs(corr)))
            score=float(corr[k])
            if best is None or abs(score)>abs(best["score"]):
                best={"score":score,"rate":bit_rate,
                      "phase":phase,"start_bit_index":k,
                      "obs":obs}
    if best is None or abs(best["score"])<min_correlation:
        return {
            "locked":False,"training_only":True,"held_out_tail_evaluated":False,
            "training_bits":train_bits,
            "best_training_correlation":round(abs(best["score"]),5) if best else 0,
            "note":"No reliable match to training bits; no tail BER reported."
        }
    start=best["start_bit_index"]
    values=best["obs"][start:start+len(truth)]
    prefix_values=values[:train_bits]
    low=np.median(prefix_values[truth[:train_bits]==0])
    high=np.median(prefix_values[truth[:train_bits]==1])
    if abs(high-low)<100:
        return {
            "locked":False,"training_only":True,"held_out_tail_evaluated":False,
            "training_bits":train_bits,
            "best_training_correlation":round(abs(best["score"]),5),
            "note":"Training levels not sufficiently separated."
        }
    threshold=(low+high)/2
    polarity=1 if high>low else -1
    recovered=((values-threshold)*polarity>0).astype(np.uint8)
    errors=recovered != truth
    holdout=errors[train_bits:]
    return {
        "locked":True,
        "training_only":True,
        "held_out_tail_evaluated":True,
        "training_bits":train_bits,
        "held_out_tail_bits":int(len(holdout)),
        "held_out_tail_errors":int(holdout.sum()),
        "held_out_tail_ber":round(float(holdout.mean()),6),
        "training_bit_errors":int(errors[:train_bits].sum()),
        "best_training_correlation":round(abs(best["score"]),6),
        "candidate_baud_estimate":round(float(best["rate"]),6),
        "detected_first_payload_bit_center_s":round(
            ((start+best["phase"])*fs/best["rate"])/fs,7),
        "candidate_sample_phase_fraction":best["phase"],
        "median_training_frequency_bit0_hz_relative":round(float(low),1),
        "median_training_frequency_bit1_hz_relative":round(float(high),1),
        "estimated_received_fsk_separation_hz":round(float(abs(high-low)),1),
        "capture_condition":"zero-phase offline I/Q LPF then frequency discriminator",
        "caveats":[
            "Training prefix is known and used for acquisition: not completely blind synchronization.",
            "The held-out tail is not used for timing or threshold selection.",
            "A matched tail alone is not proof of on-air compatibility, emissions or calibrated RF deviation.",
            "Mix offset and SDR oscillator stability remain separately uncalibrated.",
            "No RF TX or RFIC phase-continuity claim from this decoder."
        ]
    }


def decode_archive(path: Path, manifest: dict, *, train_bits=512,
                   lowpass_hz=8000., carrier_shift=CARRIER_SHIFT,
                   duration=5.0) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    samples=read_iq(path,duration=duration,offset=carrier_shift)
    outcome=decode_samples(samples,manifest,train_bits=train_bits,
                           lowpass_hz=lowpass_hz)
    with path.open("rb") as fh:
        digest=hashlib.file_digest(fh,"sha256").hexdigest()
    return {
        "experiment":"Flux FSK-05 known-training/held-out-tail I/Q analysis",
        "offline":True,"transmission_initiated":False,
        "capture_sha256":digest,
        "capture":str(path),
        "sample_rate_input_hz":RAW_RATE,
        "sample_rate_decimated_hz":FS,
        "carrier_frequency_shift_hz_assumed":carrier_shift,
        "output":outcome,
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--manifest",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--training-bits",type=int,default=512)
    p.add_argument("--lowpass-hz",type=float,default=8000.)
    p.add_argument("--carrier-shift-hz",type=float,default=CARRIER_SHIFT)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite existing analysis JSON")
    try:
        manifest=json.loads(args.manifest.read_text())
        result=decode_archive(args.iq,manifest,train_bits=args.training_bits,
                              lowpass_hz=args.lowpass_hz,
                              carrier_shift=args.carrier_shift_hz)
    except (OSError,ValueError,KeyError) as exc:
        p.error(str(exc))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+"\n")
    print("Offline decoder, no TX:",json.dumps(result["output"]))
    print("Saved:",args.out)
    return 0 if result["output"]["locked"] else 2


if __name__=="__main__":
    raise SystemExit(main())
