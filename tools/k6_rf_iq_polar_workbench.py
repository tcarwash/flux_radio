#!/usr/bin/env python3
"""Flux RF waveform workbench: complex-IQ target -> polar control feasibility.

Offline / inert. Converts arbitrary complex baseband to normalized envelope
and instantaneous frequency/phase trajectory. Simulates ideal constant-
envelope, ideal independently controllable amplitude, and quantized envelope
backends within an assumed frequency offset limit. **This is a target
representation, not an implemented hardware DAC or calibrated K6 model.**

Use for chirps, AM, QPSK, 16-QAM and supplied complex numpy arrays to assess
the exact capabilities missing from an FM-only, saturated-PA architecture.
No serial, SDR control, PTT, firmware flashing or hardware register access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

MAX_COMPLEX_SAMPLES=1_000_000


def _phase_at_zeros(signal: np.ndarray)->np.ndarray:
    """Interpolate undefined phase over exact-zero amplitude segments."""
    good=np.abs(signal)>1e-9
    if not good.any():
        raise ValueError("all-zero waveform has undefined phase")
    phase=np.unwrap(np.angle(signal[good]))
    return np.interp(np.arange(signal.size),
                     np.flatnonzero(good).astype(float),
                     phase)


def complex_to_polar(x:np.ndarray,fs:float)->dict:
    y=np.asarray(x)
    if y.ndim!=1 or y.size<4 or y.size>MAX_COMPLEX_SAMPLES:
        raise ValueError("complex waveform must be 1D, 4..1M samples")
    if not np.iscomplexobj(y) or not np.isfinite(y).all():
        raise ValueError("complex finite I/Q samples required")
    if not np.isfinite(fs) or not 100<=fs<=2_000_000:
        raise ValueError("unsupported sample rate")
    envelope=np.abs(y)
    phase=_phase_at_zeros(y)
    # First-order forward differences give an exact discrete phase
    # trajectory at sample edges; the first control sample is the
    # first following edge's estimated slope. Reconstruct with same
    # convention to avoid fictitious sample-rate dependent phase errors.
    frequency=np.r_[np.diff(phase)[0],np.diff(phase)]*(fs/(2*np.pi))
    return dict(envelope=envelope,phase=phase,frequency_hz=frequency,
                sample_rate_hz=fs)


def reconstruct(polar:dict,*,freq_limit_hz:float,
                envelope_mode:str="none",envelope_levels:int=4)->dict:
    f=np.asarray(polar["frequency_hz"],dtype=float)
    raw_env=np.asarray(polar["envelope"],dtype=float)
    fs=float(polar["sample_rate_hz"])
    if not np.isfinite(freq_limit_hz) or freq_limit_hz<=0 or freq_limit_hz>fs/2:
        raise ValueError("invalid hypothetical frequency-offset limit")
    if envelope_mode not in ("none","ook","quantized","continuous"):
        raise ValueError("unknown envelope backend")
    if not isinstance(envelope_levels,int) or not 2<=envelope_levels<=256:
        raise ValueError("envelope quantization levels must be 2..256")
    clamped=np.clip(f,-freq_limit_hz,freq_limit_hz)
    ph=np.empty_like(clamped)
    ph[0]=float(polar["phase"][0])
    ph[1:]=ph[0]+np.cumsum(clamped[1:])*(2*np.pi/fs)
    max_a=max(float(raw_env.max()),1e-12)
    normalized=raw_env/max_a
    if envelope_mode=="none":
        out_a=np.ones_like(normalized)
    elif envelope_mode=="ook":
        out_a=(normalized>=.5).astype(float)
    elif envelope_mode=="quantized":
        out_a=np.rint(normalized*(envelope_levels-1))/(envelope_levels-1)
    else:
        out_a=normalized
    output=(out_a*np.exp(1j*ph)).astype(np.complex128)
    return dict(iq=output,frequency_hz=clamped,
                normalized_amplitude=out_a,
                clipped_frequency_samples=int(np.count_nonzero(f!=clamped)),
                clipped_frequency_fraction=float(np.mean(f!=clamped)),
                max_target_frequency_hz=float(np.max(np.abs(f))),
                envelope_mode=envelope_mode)


def complex_fit(target:np.ndarray,candidate:np.ndarray)->dict:
    """Best single complex gain (phase/level), not per-sample equalization."""
    x=np.asarray(target); y=np.asarray(candidate)
    if x.shape!=y.shape or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("invalid complex vectors")
    energy=float(np.vdot(x,x).real)
    power=float(np.vdot(y,y).real)
    if energy<=0 or power<=0:
        raise ValueError("zero complex reference or modeled output")
    scale=np.vdot(y,x)/power
    residual=x-scale*y
    nmse=float(np.vdot(residual,residual).real/energy)
    cosine=abs(np.vdot(x,y))/np.sqrt(energy*power)
    return dict(normalized_evm=float(np.sqrt(max(nmse,0.))),
                normalized_error_power=nmse,
                best_static_complex_gain_magnitude=float(abs(scale)),
                coherent_correlation=float(cosine))


def analyze(x:np.ndarray,fs:float,*,freq_limit_hz=1200.,
            envelope_levels=4)->tuple[dict,dict]:
    polar=complex_to_polar(x,fs)
    case={}
    signals={}
    for name,cap,levels in (
        ("fm_only","none",envelope_levels),
        ("ook_envelope","ook",envelope_levels),
        ("four_level_envelope","quantized",envelope_levels),
        ("ideal_continuous_envelope","continuous",envelope_levels)
    ):
        r=reconstruct(polar,freq_limit_hz=freq_limit_hz,
                      envelope_mode=cap,envelope_levels=levels)
        case[name]={**complex_fit(x,r["iq"]),
                    "envelope_mode":cap,
                    "clipped_frequency_samples":r["clipped_frequency_samples"],
                    "clipped_frequency_fraction":r["clipped_frequency_fraction"],
                    "physical_rf_validated":False,
                    "amp_linearity_verified":False}
        signals[name]=r["iq"]
    envelope=polar["envelope"]
    f=polar["frequency_hz"]
    report={
        "sample_rate_hz":fs,"complex_samples":len(x),
        "duration_s":len(x)/fs,
        "assumed_max_abs_offset_hz":freq_limit_hz,
        "assumed_envelope_levels":envelope_levels,
        "max_required_abs_frequency_hz":float(np.max(np.abs(f))),
        "required_abs_frequency_hz_p95":float(np.quantile(abs(f),.95)),
        "target_envelope_peak_to_rms":float(np.max(envelope)/np.sqrt(np.mean(envelope**2))),
        "target_envelope_dynamic_range_db_p99_p01":float(
            20*np.log10(max(np.percentile(envelope,99),1e-12)/
                        max(np.percentile(envelope,1),1e-12))),
        "constant_envelope_target":bool(np.allclose(envelope,np.mean(envelope),rtol=1e-4,atol=1e-5)),
        "results":case,
        "caveats":[
            "FM-only can reproduce phase trajectories subject to deviation/clock limits, but cannot reproduce variable RF amplitude.",
            "OOK/four-level/continuous amplitude outputs are IDEAL MATHEMATICAL MODELS, not verified FD6818B control capabilities.",
            "Assumed frequency limit is a user-chosen research constraint, not calibration of the RF chip or microphone path.",
            "No analog coupling, PA nonlinearities, spectral-regrowth, modulation settling, PLL bandwidth, phase noise, adjacent-channel mask or regulatory certification modeled.",
            "The reconstructed waveform is at baseband; nothing is transmitted."
        ]
    }
    return report,dict(target=x,amplitude=envelope,phase=polar["phase"],
                       frequency_hz=f,**{f"mode_{k}":v for k,v in signals.items()})


def _smooth_steps(values:np.ndarray,sps:int)->np.ndarray:
    if sps<4:raise ValueError("need 4+ samples/symbol")
    # Soft transition via Hann finite-impulse response, only for
    # synthetic study, not a spec-compliant pulse-shaping filter.
    up=np.repeat(values,sps)
    taps=np.hanning(max(5,(sps//4)|1))
    taps=taps/np.sum(taps)
    if np.iscomplexobj(up):
        return np.convolve(up.real,taps,mode="same")+1j*np.convolve(
            up.imag,taps,mode="same")
    return np.convolve(up,taps,mode="same")


def reference(mode:str,fs=48_000,duration_s=.5,seed=17)->np.ndarray:
    if not isinstance(fs,int) or not 100<=fs<=2_000_000 or (
        not np.isfinite(duration_s) or duration_s<=0):
        raise ValueError("invalid reference configuration")
    n=round(fs*duration_s)
    if not 16<=n<=MAX_COMPLEX_SAMPLES:
        raise ValueError("invalid complex IQ sample length")
    t=np.arange(n)/fs
    rng=np.random.default_rng(seed)
    if mode=="cw":
        return np.ones(n,dtype=np.complex128)
    if mode=="chirp":
        f=-750.+1500*t/duration_s
        return np.exp(2j*np.pi*np.cumsum(f)/fs)
    if mode=="fsk":
        symbol_count=round(duration_s*75)
        symbols=rng.choice([-900,-300,300,900],symbol_count)
        freq=np.repeat(symbols,int(np.ceil(n/len(symbols))))[:n]
        return np.exp(2j*np.pi*np.cumsum(freq)/fs)
    if mode=="am":
        amp=.55+.40*np.sin(2*np.pi*13*t)
        return amp*np.exp(2j*np.pi*120*t)
    if mode=="qpsk":
        sps=round(fs/100)
        symbols=np.exp(1j*(np.pi/4+rng.integers(0,4,(n+sps-1)//sps)*np.pi/2))
        z=_smooth_steps(symbols,sps)[:n]
        return z.astype(np.complex128)
    if mode=="qam16":
        sps=round(fs/100)
        amps=np.array([-3,-1,1,3])/np.sqrt(10)
        symbols=(rng.choice(amps,(n+sps-1)//sps)+
                 1j*rng.choice(amps,(n+sps-1)//sps))
        return _smooth_steps(symbols,sps)[:n].astype(np.complex128)
    raise ValueError("mode must be cw,chirp,fsk,am,qpsk,qam16")


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode",choices=("cw","chirp","fsk","am","qpsk","qam16","npy"),
                    required=True)
    ap.add_argument("--source",type=Path,
                    help="only for --mode npy: complex64/complex128 numpy vector")
    ap.add_argument("--out-prefix",type=Path,required=True)
    ap.add_argument("--sample-rate",type=int,default=48000)
    ap.add_argument("--duration",type=float,default=.5)
    ap.add_argument("--max-frequency-offset",type=float,default=1200.)
    ap.add_argument("--envelope-levels",type=int,default=4)
    ap.add_argument("--seed",type=int,default=17)
    args=ap.parse_args(argv)
    if args.mode=="npy":
        if args.source is None:ap.error("npy needs --source")
        try:
            y=np.load(args.source,mmap_mode="r",allow_pickle=False)
            x=np.array(y,dtype=np.complex128) if np.iscomplexobj(y) else y
        except (OSError,ValueError) as e:
            ap.error(str(e))
    else:
        if args.source:ap.error("--source only for npy")
        try:
            x=reference(args.mode,args.sample_rate,args.duration,args.seed)
        except (ValueError,MemoryError) as e:
            ap.error(str(e))
    try:
        report,signals=analyze(x,args.sample_rate,
                               freq_limit_hz=args.max_frequency_offset,
                               envelope_levels=args.envelope_levels)
        report["source_mode"]=args.mode
        dest=args.out_prefix.parent
        dest.mkdir(parents=True,exist_ok=True)
        j=args.out_prefix.with_suffix(".json")
        z=args.out_prefix.with_suffix(".npz")
        if j.exists() or z.exists():
            ap.error("refusing to overwrite existing waveform files")
        np.savez_compressed(z,**signals)
        report["npz_path"]=str(z)
        with z.open("rb") as handle:report["npz_sha256"]=hashlib.file_digest(handle,"sha256").hexdigest()
        j.write_text(json.dumps(report,indent=2)+"\n")
        print(args.mode,"FM EVM",round(report["results"]["fm_only"]["normalized_evm"],4),
              "ideal-envelope EVM",round(report["results"]["ideal_continuous_envelope"]["normalized_evm"],4))
        print("Saved",j,z)
    except (ValueError,TypeError,OSError) as e:
        ap.error(str(e))


if __name__=="__main__":
    main()
