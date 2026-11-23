#!/usr/bin/env python3
"""TX-15P RFIC tone-oscillator *phase discrimination* reference (OFFLINE).

The original TX8 150-symbol/s vector uses 450+300*k Hz; every symbol
is exactly an integer number of cycles. A phase-continuous oscillator
and a zero-phase-on-every-write oscillator then have identical *ideal*
trajectories, so tone ID decode does not establish phase continuity.

This module synthesizes audio-FM *frequency-control waveforms*, not RF
I/Q and not radio commands. Three competing models:
  continuous             oscillator phase integrates across REG_71 writes
  reset_on_change        phase resets only if register word changes
  reset_on_every_write   phase resets on every write, including same word
Their model separation is an experimental-design metric, not physical
K6 evidence. Actual RF must also characterize latency, filtering,
clock drift, audio deviation, envelope, and spectrum.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re

import numpy as np

FS = 48000
SYMBOL_BAUD = 150
PILOT_SYMBOLS = 16
N_SYMBOLS = 64
WORD_PER_HZ = 10.32444
MODES = ("continuous","reset_on_change","reset_on_every_write")
BASE = Path(__file__).resolve().parents[1]
OLD_HEADER = BASE / "c/k6/mod/tx8_probe.h"
NEW_HEADER = BASE / "c/k6/mod/tx8_phase_probe.h"


def parse_header(path:Path)->tuple[np.ndarray,np.ndarray]:
    src=Path(path).read_text()
    def values(name:str,capacity:int)->np.ndarray:
        m=re.search(r"static\s+const\s+uint(?:16|8)_t\s+"+name+
                    r"\[[A-Z0-9_]+\]\s*=\s*\{([^}]*)\}",src,re.S)
        if m is None:
            raise ValueError(f"missing {name} in vector header")
        a=np.asarray([int(x) for x in re.findall(r"\d+",m.group(1))],dtype=np.int32)
        if len(a)!=capacity:
            raise ValueError(f"{name} must have {capacity} entries")
        return a
    words=values("tx8_words",8)
    symbols=values("tx8_symbols",N_SYMBOLS)
    if np.any(words<=0) or np.any(words>65535) or np.any(symbols<0) or np.any(symbols>7):
        raise ValueError("invalid oscillator register words or symbol indices")
    if len(set(symbols[:PILOT_SYMBOLS]))<8:
        raise ValueError("pilot must exercise all eight oscillator words")
    return words,symbols


def ideal_audio_phase(words:np.ndarray,symbols:np.ndarray,mode:str, *,
                      fs:int=FS,baud:int=SYMBOL_BAUD,
                      mcu_ppm:float=0.0)->np.ndarray:
    """Analytic oscillator phase, not physical RF frequency/IQ."""
    if (mode not in MODES or fs<=0 or baud<=0 or fs%baud or
        len(words)!=8 or len(symbols)!=N_SYMBOLS or
        not np.isfinite(mcu_ppm) or abs(mcu_ppm)>2000):
        raise ValueError("invalid phase model inputs")
    sps=fs//baud
    phase=np.empty(sps*N_SYMBOLS,dtype=np.float64)
    acc=0.0
    prev=-1
    # MCU edge spacing may differ from RFIC crystal by mcu_ppm.
    # Timebase is the actual *sampling clock*: 1+ppm is an idealized
    # relative frequency offset, not a measured board calibration.
    freq_scale=1.0+mcu_ppm*1e-6
    for i,idx in enumerate(symbols):
        idx=int(idx)
        if (mode=="reset_on_every_write" or
            (mode=="reset_on_change" and idx!=prev)):
            acc=0.0
        freq=words[idx]/WORD_PER_HZ*freq_scale
        t=np.arange(sps,dtype=float)/fs
        phase[i*sps:(i+1)*sps]=acc+2*np.pi*freq*t
        acc=(acc+2*np.pi*freq*sps/fs)%(2*np.pi)
        prev=idx
    return phase


def coherence(a:np.ndarray,b:np.ndarray,lo:int,hi:int)->float:
    if len(a)!=len(b) or lo>=hi:
        raise ValueError("comparable analytic phase arrays required")
    aa=np.exp(1j*a[lo:hi])
    bb=np.exp(1j*b[lo:hi])
    return float(abs(np.mean(aa*np.conj(bb))))


def design_report(path:Path=NEW_HEADER, *, fs:int=FS,baud:int=SYMBOL_BAUD)->dict:
    words,symbols=parse_header(path)
    sps=fs//baud
    traces={mode:ideal_audio_phase(words,symbols,mode,fs=fs,baud=baud)
            for mode in MODES}
    pilot=(0,PILOT_SYMBOLS*sps)
    heldout=(PILOT_SYMBOLS*sps,N_SYMBOLS*sps)
    comparison=[]
    for i,a in enumerate(MODES):
        for b in MODES[i+1:]:
            comparison.append({
                "model_a":a,"model_b":b,
                "pilot_analytic_phase_coherence":
                    coherence(traces[a],traces[b],*pilot),
                "heldout_analytic_phase_coherence":
                    coherence(traces[a],traces[b],*heldout)})
    frequencies=words/WORD_PER_HZ
    remainder=((frequencies/baud)%1.0)
    return {
        "experiment":"Flux TX-15P prospective tone oscillator phase-discriminating stimulus",
        "offline_only":True,
        "physical_rf_emission_claim":False,
        "phase_model":"ideal analytical oscillator phase; not FM transmitter RF IQ",
        "header":str(path),
        "header_sha256":hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "rate_symbols_per_s":baud,"audio_sample_rate_hz":fs,
        "pilot_symbols":PILOT_SYMBOLS,"heldout_symbols":N_SYMBOLS-PILOT_SYMBOLS,
        "register_words":words.tolist(),"symbols":symbols.tolist(),
        "nominal_hz_after_register_quantization":frequencies.tolist(),
        "fractional_tone_cycles_per_symbol":remainder.tolist(),
        "all_nominal_tone_cycle_counts_integral":
            bool(np.all(np.minimum(remainder,1-remainder)<1e-8)),
        "hypothesis_coherence":comparison,
        "note":("Low model coherence demonstrates separability in ideal audio "
                "FM phase, NOT that real FD6818B phase is continuous or "
                "that the transmitter can synthesize arbitrary RF IQ."),
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--header",type=Path,default=NEW_HEADER)
    p.add_argument("--out",type=Path)
    args=p.parse_args(argv)
    r=design_report(args.header)
    print(json.dumps(r,indent=2))
    if args.out is not None:
        if args.out.exists():
            p.error("refusing to overwrite an existing design report")
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(r,indent=2)+"\n")


if __name__=="__main__":
    main()
