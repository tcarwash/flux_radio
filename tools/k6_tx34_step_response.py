#!/usr/bin/env python3
"""TX-34: offline-only RF frequency/phase transition characterizer.

Synthetic IQ is generated from a *causal* single-write/transaction source
model, written as SigMF, and passed to the existing receive-only SoapyFang
SigmfSource -> RFBackend -> ChipModel. The independent receiver is the
observed channel-filter IQ; the pilot (only) calibrates delay and levels.
Nothing here sends RF or implements a K6 TX actuator.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from sim.k6emu.rf.backend import create_backend
from sim.k6emu.rf.sources import write_sigmf

RATE=240_000
CENTER=440_850_000  # synthetic RF center; NO radio access
PILOT=80
HELDOUT=256
PATHS=('native_fsk_4800','reg51_signed_400','reg40_frame_scale_4800')
CONTROLS=('none','reversed_levels','missing_gate','slow_bus','subaudio_134hz','noise','phase_impulse')


def pattern():
    """Pilot is alternating, heldout payload is reproducible but never fit."""
    return np.concatenate((np.tile([0,1],PILOT//2),
                           np.random.default_rng(34).integers(0,2,HELDOUT))).astype(np.uint8)


def generate(path, *, control='none', spi_us=117.0, subaudio_hz=1300.0):
    if path not in PATHS:raise ValueError('unknown physical path model')
    if control not in CONTROLS:raise ValueError('unknown negative control')
    if not math.isfinite(spi_us) or not 0<spi_us<=10000:raise ValueError('SPI time must be finite 0..10000us')
    if not math.isfinite(subaudio_hz) or not 0<subaudio_hz<=12000:
        raise ValueError('hypothetical subaudio filter cutoff must be finite positive')
    bits=pattern()
    symbol_rate=400 if path=='reg51_signed_400' else 4800
    samples_per_symbol=RATE//symbol_rate
    assert RATE%symbol_rate==0
    frequency_levels=900.0 if path=='reg51_signed_400' else 1650.0
    if path=='reg40_frame_scale_4800':
        frequency_levels *= 0.7 # per-FRAME constant only, never in-frame REG40 writes
    n=len(bits)*samples_per_symbol
    duration_us=np.arange(n,dtype=np.float64)*1e6/RATE
    write_times=None
    if path=='reg51_signed_400':
        period=1e6/symbol_rate
        write_times=np.empty(len(bits),dtype=float)
        bus=spi_us if control!='slow_bus' else 4500.0
        previous=-bus
        for i in range(len(bits)):
            # Every single scalar register update has its own bus occupancy.
            write_times[i]=max(float(i)*period,previous+bus)
            previous=write_times[i]
        idx=np.searchsorted(write_times,duration_us,side='right')-1
        idx=np.clip(idx,0,len(bits)-1)
        values=2*bits[idx].astype(float)-1
    else:
        # Native FSK FIFO symbol clock is physically distinct from 3-wire.
        values=np.repeat(2*bits.astype(float)-1,samples_per_symbol)
    if control=='reversed_levels':values=-values
    f=values*frequency_levels
    if path=='reg51_signed_400' or control=='subaudio_134hz':
        cutoff=134.4 if control=='subaudio_134hz' else subaudio_hz
        alpha=1.0-math.exp(-2*math.pi*cutoff/RATE)
        # Causal single-pole subaudio response. NOT calibrated silicon model.
        state=0.0
        for i,value in enumerate(f):
            state += alpha*(value-state)
            f[i]=state
    if control=='missing_gate':f[:]=0
    phase=2*math.pi*np.cumsum(f,dtype=np.float64)/RATE
    if control=='phase_impulse':
        phase[samples_per_symbol*(PILOT+30):] += 0.9
    iq=np.exp(1j*phase).astype(np.complex64)
    snr=9.0 if control=='noise' else 45.0
    rng=np.random.default_rng(3401)
    noise_power=10.0**(-snr/10.0)
    noise=(rng.standard_normal(n)+1j*rng.standard_normal(n))*math.sqrt(noise_power/2)
    return iq+(noise.astype(np.complex64)),dict(
        bits=bits,symbol_rate=symbol_rate,sps=samples_per_symbol,
        levels_hz=frequency_levels,write_times_us=write_times,
        simulated_spi_us=spi_us,filter_hz=(134.4 if control=='subaudio_134hz' else
                                           subaudio_hz if path=='reg51_signed_400' else None))


def filtered_receive(iq, base):
    base=Path(base)
    write_sigmf(base,np.asarray(iq,dtype=np.complex64),RATE,CENTER,
               description='TX34 SYNTHETIC IQ: NO K6 radio and no physical RF')
    backend=create_backend('sigmf:'+str(base))
    try:
        assert backend.status()['receive_only'] and not backend.source.live
        backend.chip.tune(CENTER)
        filtered=[]
        process=backend.chip.chan.process
        def tap(x):
            y=process(x)
            filtered.append(np.array(y,copy=True))
            return y
        backend.chip.chan.process=tap
        register_hz=CENTER//10
        regs={0x38:register_hz&0xffff,0x39:register_hz>>16}
        for tick in range(1,math.ceil(len(iq)/RATE/.05)+1):
            backend.read(0x67,regs,min(len(iq)/RATE,tick*.05))
        if not filtered:raise RuntimeError('SoapyFang did not process SigMF samples')
        data=np.concatenate(filtered)
        assert len(data)==len(iq)//(RATE//48000),(len(data),len(iq))
        return data,backend.chip.fa,backend.status()['receive_only']
    finally:backend.close()


def _estimate_pilot_delay(frequency, bits, sps):
    # Independent delay fitted on pilot alone. Never use heldout bits.
    expected=np.repeat(2*bits[:PILOT].astype(float)-1,sps)
    n=min(len(frequency),len(expected))
    best=None
    for lag in range(0,min(16,max(1,sps//2))):
        x=frequency[lag:n]
        y=expected[:n-lag]
        # Both pilot levels are known, but the IQ channel offset is unknown.
        scale=np.dot(x-x.mean(),y-y.mean())/(np.dot(y-y.mean(),y-y.mean())+1e-9)
        center=float(x.mean()-scale*y.mean())
        residual=float(np.mean((x-(scale*y+center))**2))
        if best is None or residual<best[0]:
            best=(residual,lag,center,float(scale))
    return best[1],best[2],best[3]


def score(filtered, fa, truth):
    bits=truth['bits']
    sps=round(fa/truth['symbol_rate'])
    if abs(sps*truth['symbol_rate']-fa)>1e-6:raise ValueError('fractional receiver symbol alignment unsupported')
    # Phase-derived discriminator from **post-SoapyFang channel-filter IQ**.
    frequency=np.angle(filtered[1:]*np.conj(filtered[:-1]))*fa/(2*math.pi)
    frequency=np.r_[frequency[:1],frequency]
    lag,offset,scale=_estimate_pilot_delay(frequency,bits,sps)
    symbol_indices=np.arange(len(bits))*sps+sps//2+lag
    if symbol_indices[-1]>=len(frequency):raise ValueError('short received signal')
    sample=frequency[symbol_indices]
    # No fitting against heldout payload. Falsify if pilot sign/differential fails.
    # Physical signed-level order is predeclared. Do not learn/invert the
    # payload polarity after seeing the pilot: reversed actuator sign FAILS.
    predicted=(sample>=offset).astype(np.uint8)
    pilot_error=float(np.mean(predicted[:PILOT]!=bits[:PILOT])) if abs(scale)>1e-7 else 1.0
    ber=float(np.mean(predicted[PILOT:]!=bits[PILOT:]))
    norm_levels=abs(scale)
    # Conservative linear fit, both pilot levels measured independently.
    eye=float(2*norm_levels/(np.std(sample[:PILOT]-
                     (offset+scale*(2*bits[:PILOT].astype(float)-1)))+1e-9))
    # Time/frequency transient windows around known alternating PILOT changes.
    steady=[]
    for i in range(8,PILOT-4):
        if bits[i]!=bits[i-1]:
            mid=i*sps+lag
            if 0<=mid and mid+sps<len(frequency):
                before=float(np.median(frequency[mid-sps//3:mid]))
                after=float(np.median(frequency[mid+sps//2:mid+sps]))
                steady.append((before,after))
    transition_hz=float(np.median([abs(b-a) for a,b in steady])) if steady else 0
    first_diff=np.diff(np.unwrap(np.angle(filtered)))
    # Raw phase jumps are not oscillator AM/PM; report IQ FIR phase increments.
    max_phase_step=float(np.max(np.abs(first_diff))) if len(first_diff) else float('nan')
    calibrated_step_samples=int(lag)
    # Estimate settling against known pilot level; avoid claiming absolute
    # on-chip group delay (this includes SoapyFang receive FIR).
    exceed=[]
    for i in range(8,PILOT-4):
        if bits[i]==bits[i-1]:continue
        start=i*sps+lag
        target=offset+scale*(2.0*bits[i]-1.0)
        portion=frequency[start:start+sps]
        threshold=max(0.1*norm_levels,1.0)
        settled=np.flatnonzero(np.abs(portion-target)<threshold)
        if len(settled):exceed.append(int(settled[0]))
    settle_us=float(np.median(exceed)*1e6/fa) if exceed else None
    return dict(heldout_ber=ber,pilot_ber=pilot_error,eye_opening_sigma=eye,
                pilot_level_separation_hz=2*norm_levels,
                rx_group_delay_samples=calibrated_step_samples,
                rx_group_delay_us=calibrated_step_samples*1e6/fa,
                median_transition_hz=transition_hz,
                settle_to_10pct_us=settle_us,
                max_filtered_phase_increment_rad=max_phase_step,
                iq_samples=len(filtered),iq_discriminator_rate_hz=fa)


def run(path,*,control='none',spi_us=117.0,subaudio_hz=1300.0,temp_dir=None):
    if temp_dir is None:
        with tempfile.TemporaryDirectory(prefix='tx34-offline-') as d:
            return run(path,control=control,spi_us=spi_us,subaudio_hz=subaudio_hz,temp_dir=d)
    iq,truth=generate(path,control=control,spi_us=spi_us,subaudio_hz=subaudio_hz)
    filtered,fa,receive_only=filtered_receive(iq,Path(temp_dir)/f'{path}-{control}')
    result=score(filtered,fa,truth)
    result.update(dict(path=path,control=control,rf_model='SoapyFang SigmfSource -> RFBackend -> ChipModel',
                       source='synthetic IQ, uncalibrated host actuator',
                       provenance='SYNTHETIC_NOT_BENCH',physical_tx=False,receive_only=receive_only,
                       host_spi_transaction_us=spi_us if path=='reg51_signed_400' else None,
                       max_update_count=len(truth['write_times_us']) if truth['write_times_us'] is not None else 0,
                       nominal_symbol_rate=truth['symbol_rate'],
                       modeled_subaudio_cutoff_hz=truth['filter_hz'],
                       physical_gate='DEFERRED_DEPENDENCY' if path!='native_fsk_4800' else 'NOT_AUTHORIZED'))
    # "offline-model" acceptance, never physical pass.
    result['offline_model_pass']=bool(result['heldout_ber']<.03 and result['pilot_ber']<.03 and
                                       result['eye_opening_sigma']>2 and receive_only)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--json',type=Path)
    p.add_argument('--paths',default=','.join(PATHS),help='comma-separated offline model paths')
    p.add_argument('--control',choices=CONTROLS,default='none')
    p.add_argument('--spi-us',type=float,default=117.0)
    p.add_argument('--subaudio-hz',type=float,default=1300.0,
                   help='hypothetical unverified bandwidth, not a claimed B-chip capability')
    a=p.parse_args(argv)
    paths=[x.strip() for x in a.paths.split(',')]
    if not paths or any(x not in PATHS for x in paths) or len(paths)!=len(set(paths)):
        p.error('unknown or duplicate model path')
    result=[run(x,control=a.control,spi_us=a.spi_us,subaudio_hz=a.subaudio_hz) for x in paths]
    doc=dict(issue=37,kind='OFFLINE',provenance='synthetic uncalibrated host IQ',
             physical_transmissions=0,hardware_access=False,
             caveat='Not a hardware TX qualification. REG51 and REG40 physical dependencies remain deferred.',
             results=result)
    if a.json:
        a.json.parent.mkdir(parents=True,exist_ok=True)
        a.json.write_text(json.dumps(doc,indent=2)+'\n')
    for x in result:
        print(f"{x['path']} {a.control}: BER={x['heldout_ber']:.4f} eye={x['eye_opening_sigma']:.1f} "
              f"step={x['median_transition_hz']:.0f}Hz lag={x['rx_group_delay_us']:.0f}us "
              f"offline_model_pass={x['offline_model_pass']}")
    return 0 if all(x['offline_model_pass'] for x in result) and a.control=='none' else (0 if a.control!='none' else 1)


if __name__=='__main__':raise SystemExit(main())
