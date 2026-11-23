#!/usr/bin/env python3
"""OFFLINE counterfactual K6 polar-IQ composite from TWO SEPARATE RF captures.

Combines the measured instantaneous RF phase of a native-FSK transmission
with the measured amplitude envelope of a *different* APC transmission.
The result was NEVER transmitted as a combined signal. It is useful for
quantifying an amplitude actuator's slow 80/100% envelope error without
confounding native-FSK frequency control, and is NOT hardware IQ EVM.

No radio/SDR/serial/control access, flash, TX, network or firmware dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly
from k6_rf_iq_polar_workbench import complex_fit

FS_IN = 1_024_000
FS_OUT = 64_000
IF_HZ = -280_484.375
SEG_SECONDS = .180
NFFT = 4096
FULL_CYCLE_HALF_SEC = .0206
APC_DELAY_SEC = .086


def _load_iq(path: Path, *, source: str):
    path=Path(path)
    raw=np.load(path,mmap_mode="r",allow_pickle=False)
    if raw.dtype!=np.uint8 or raw.ndim!=1 or len(raw)%2 or not (1e6<=len(raw)<=30e6):
        raise ValueError(f"invalid raw interleaved IQ: {path}")
    meta=json.loads(path.with_suffix(".json").read_text())
    if int(meta.get("sample_rate_hz",-1))!=FS_IN:
        raise ValueError("unexpected capture sample rate")
    if source=="fsk":
        if (not meta.get("modem_frame_confirmed") or
                not meta.get("dummy_load_user_confirmed") or
                meta.get("center_hz")!=433_750_000):
            raise ValueError("FSK capture metadata not hardware-validated")
    elif source=="apc":
        tx=meta.get("hardware_lab_transaction",{})
        if (meta.get("capture_center_hz")!=433_750_000 or
                tx.get("module_completion",{}).get("result")!=24 or
                not tx.get("original_module_restored") or
                not tx.get("original_radio_frequency_restored")):
            raise ValueError("APC capture metadata not hardware-validated")
        actual=hashlib.sha256(np.asarray(raw).tobytes()).hexdigest()
        if actual!=meta.get("iq_raw_u8_sha256"):
            raise ValueError("APC source checksum mismatch")
    else:
        raise ValueError("unrecognized source kind")
    return raw,meta


def _burst_bounds(raw: np.ndarray):
    nf=(len(raw)//2)//NFFT
    x=np.asarray(raw[:nf*2*NFFT],dtype=np.float32)
    z=(x[::2]-127.5+1j*(x[1::2]-127.5)).reshape(nf,NFFT)
    freq=np.fft.fftfreq(NFFT,1/FS_IN)
    roi=(freq>-310_000)&(freq<-250_000)
    power=np.max(np.abs(np.fft.fft(z*np.hanning(NFFT),axis=1)[:,roi])**2,axis=1)
    present=np.flatnonzero(power>4_000_000)
    if len(present)<30:
        raise ValueError("no sufficiently strong RF burst")
    return float(present[0]*NFFT/FS_IN),float((present[-1]+1)*NFFT/FS_IN)


def _downconvert(raw: np.ndarray, start_sec: float, duration_sec: float):
    start=round(start_sec*FS_IN)
    samples=round(duration_sec*FS_IN)
    if start<0 or start+samples>len(raw)//2:
        raise ValueError("requested complex slice outside IQ capture")
    v=np.asarray(raw[2*start:2*(start+samples)],dtype=np.float32)
    z=v[::2]-127.5+1j*(v[1::2]-127.5)
    i=np.arange(samples,dtype=np.float64)+start
    z*=np.exp(-2j*np.pi*IF_HZ/FS_IN*i)
    return resample_poly(z,1,FS_IN//FS_OUT)


def _ideal_amplitude(t,shift):
    """Nominal 80/100% low-high sequence with global timing shift only."""
    interval=np.floor((t-APC_DELAY_SEC+shift)/FULL_CYCLE_HALF_SEC).astype(int)
    return np.where((interval%2)==0,.8,1.)


def analyze(fsk_path: Path,apc_path: Path,*,duration=SEG_SECONDS):
    if not .06<=duration<=.19:raise ValueError("duration must be 60..190 ms")
    fsk,fsk_meta=_load_iq(fsk_path,source="fsk")
    apc,apc_meta=_load_iq(apc_path,source="apc")
    fsk_on,fsk_off=_burst_bounds(fsk)
    apc_on,apc_off=_burst_bounds(apc)
    # Avoid FSK startup/framing and tail; require a single common
    # analysis duration, even though the transmissions were at different times.
    fsk_begin=fsk_on+.018
    apc_begin=apc_on+APC_DELAY_SEC
    if fsk_begin+duration>=fsk_off-.008 or apc_begin+duration>=apc_off-.008:
        raise ValueError("insufficient overlapping active RF durations")

    fsk_iq=_downconvert(fsk,fsk_begin,duration)
    apc_iq=_downconvert(apc,apc_begin,duration)
    n=min(len(fsk_iq),len(apc_iq))
    fsk_iq=fsk_iq[:n];apc_iq=apc_iq[:n]
    if n<FS_OUT*.06:raise ValueError("insufficient decoded RF samples")
    # Independent APC carrier amplitude calibration on initial ~80ms
    # full-calibrated hold, using same relative IQ power scale.
    apc_reference=_downconvert(apc,apc_on+.023,.035)
    full=float(np.median(np.abs(apc_reference)))
    if full<.5:raise ValueError("APC reference carrier missing")
    apc_amplitude=np.abs(apc_iq)/full
    # Smooth 1-ms ADC envelope noise; do not smooth FSK phase.
    width=round(FS_OUT*.001)
    smooth=np.convolve(apc_amplitude,np.ones(width)/width,mode="same")
    fsk_magnitude=np.abs(fsk_iq)
    if np.quantile(fsk_magnitude,.1)<.8:raise ValueError("FSK baseband carrier inadequate")
    frequency_phase=np.unwrap(np.angle(fsk_iq))
    fsk_unit=np.exp(1j*frequency_phase)
    virtual=smooth*fsk_unit
    time=np.arange(n)/FS_OUT
    # Global timing offset minimizes a *separate* amplitude-only mismatch.
    # It cannot equalize a nonlinear, time-varying envelope or alter phase.
    shifts=np.linspace(-.012,.012,97)
    fits=[]
    for shift in shifts:
        level=_ideal_amplitude(time+APC_DELAY_SEC,shift)
        target=level*fsk_unit
        fit=complex_fit(target,virtual)
        fits.append(fit["normalized_evm"])
    best=int(np.argmin(fits))
    shift=float(shifts[best])
    target_amplitude=_ideal_amplitude(time+APC_DELAY_SEC,shift)
    target=target_amplitude*fsk_unit
    fit=complex_fit(target,virtual)
    # Stable-phase frequency summaries are indicative of *one measured
    # FSK frame*, NOT a receiver decode or nominal absolute RF error.
    dphi=np.diff(frequency_phase)*FS_OUT/(2*np.pi)
    spread=np.quantile(dphi,[.10,.50,.90])
    report={
        "analysis":"COUNTERFACTUAL composite of TWO INDEPENDENT RF recordings",
        "combined_polar_tx_demonstrated":False,
        "hardware_combined_iq_evm_measured":False,
        "source_fsk_path":str(fsk_path),
        "source_apc_path":str(apc_path),
        "fsk_frame_confirmed":True,
        "apc_hardware_tx_and_restore_verified":True,
        "sample_rate_hz":FS_OUT,
        "complex_samples":n,
        "duration_seconds":n/FS_OUT,
        "fsk_capture_burst_seconds":[fsk_on,fsk_off],
        "apc_capture_burst_seconds":[apc_on,apc_off],
        "fsk_phase_frequency_percentiles_hz_10_50_90":[round(float(x),2) for x in spread],
        "apc_100percent_reference_relative_adc":round(full,4),
        "modeled_apc_measured_envelope_percentiles_10_50_90":[round(float(x),4) for x in np.quantile(smooth,[.1,.5,.9])],
        "desired_high_low_levels":[1.,.8],
        "desired_halfstep_seconds":FULL_CYCLE_HALF_SEC,
        "global_best_alignment_shift_seconds":shift,
        "counterfactual_amplitude_only_evm":fit["normalized_evm"],
        "counterfactual_amplitude_only_coherent_correlation":fit["coherent_correlation"],
        "warnings":[
            "Separate FSK and APC RF recordings were multiplied offline; this combined RF was NEVER transmitted",
            "FSK phase comes from received radio samples and is reused as ideal target, so this EVM measures only APC envelope error",
            "No joint actuator timing, phase continuity under APC change, power/spectral calibration, receiver alignment or hardware IQ EVM is measured",
            "Global timing shift is optimized on the same data; reported amplitude-only error is an optimistic bound",
            "Near-70% APC hysteresis is deliberately excluded; modeled amplitude floor is 80%",
        ],
    }
    arrays={
        "desired_iq":target.astype(np.complex64),
        "counterfactual_iq":virtual.astype(np.complex64),
        "actual_fsk_carrier_unit_phase":fsk_unit.astype(np.complex64),
        "actual_apc_envelope_normalized":smooth.astype(np.float32),
        "desired_apc_envelope":target_amplitude.astype(np.float32),
    }
    return report,arrays


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--fsk-iq",type=Path,required=True)
    p.add_argument("--apc-iq",type=Path,required=True)
    p.add_argument("--out-prefix",type=Path,required=True)
    args=p.parse_args(argv)
    report,arrays=analyze(args.fsk_iq,args.apc_iq)
    out=args.out_prefix
    if out.with_suffix(".json").exists() or out.with_suffix(".npz").exists():
        p.error("refusing to overwrite an existing analysis artifact")
    out.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(out.with_suffix(".npz"),**arrays)
    report["npz_path"]=str(out.with_suffix(".npz"))
    report["npz_sha256"]=hashlib.sha256(out.with_suffix(".npz").read_bytes()).hexdigest()
    out.with_suffix(".json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
