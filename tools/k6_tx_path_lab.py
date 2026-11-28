#!/usr/bin/env python3
"""Offline-only matrix of K6 TX actuator hypotheses against CW/AM/SSB/QPSK/OFDM.

Enumerates every 1-, 2-, 3-actuator subset and every envelope model,
preserving conflicting paths explicitly. Runs an inexpensive mathematical
IQ distortion screen for every compatible configuration, then *selected*
actual SoapyFang SigMF receiver replays with independent host demodulators.
No K6 firmware TX path is executed here; hardware capability is never inferred
from synthetic reference demodulation.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import tempfile
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from sim.modulation.tx_paths import (SAMPLE_RATE,ENVELOPES,ACTUATORS,
                                    all_configurations,compatibility,synthesize,
                                    envelope_response,deviation_components)
from tools.k6_soapyfang_modlab import MODES,fixture,run_mode
from sim.k6emu.rf.tx_trace import observe_writes

# Each pair corresponds to an evidence question, not an actual hardware mode.
SOAX={
    'cw':((('ideal',),'ideal'),(('carrier',),'ideal'),(('dcs_bits',),'measured_gate'),(('carrier',),'hard_gate'),
          (('carrier',),'measured_gate'),(('carrier',),'apc_slow'),
          (('carrier',),'apc_knee'),(('carrier',),'driver6_gate'),
          (('tone',),'constant_pa'),(('tone','subaudio'),'measured_gate')),
    'am':((('ideal',),'ideal'),(('carrier',),'ideal'),(('carrier',),'constant_pa'),(('ideal',),'constant_pa'),
          (('carrier',),'apc_slow'),(('carrier',),'apc_knee'),
          (('carrier',),'driver6'),(('carrier',),'driver6_bias4'),
          (('tone',),'driver6'),(('micin','subaudio'),'driver6_bias4')),
    'ssb':((('ideal',),'ideal'),(('carrier',),'ideal'),(('carrier',),'constant_pa'),
           (('micin',),'ideal'),(('micin',),'driver6'),
           (('subaudio',),'driver6'),(('signed_dac',),'driver6_bias4'),
           (('native_fsk','subaudio'),'driver6')),
    'ssb-lsb':((('ideal',),'ideal'),(('carrier',),'ideal'),(('micin',),'ideal'),
               (('signed_dac',),'driver6_bias4')),
    'qpsk':((('ideal',),'ideal'),(('ideal',),'constant_pa'),(('ctcss_phase',),'constant_pa'),(('carrier',),'ideal'),(('native_fsk',),'constant_pa'),
            (('native_fsk','reg40'),'constant_pa'),
            (('subaudio',),'constant_pa'),(('micin',),'constant_pa'),
            (('signed_dac',),'constant_pa'),(('gp0',),'constant_pa'),
            (('native_fsk','subaudio'),'constant_pa')),
    'ofdm':((('ideal',),'ideal'),(('ideal',),'constant_pa'),(('carrier',),'ideal'),(('carrier',),'constant_pa'),
            (('micin',),'ideal'),(('native_fsk',),'driver6'),
            (('signed_dac',),'driver6_bias4'),
            (('gp0',),'driver6_bias4'),
            (('native_fsk','subaudio'),'driver6_bias4')),
}


def normalized_error(reference,signal):
    """Gain-compensated complex waveform NMSE, not symbol EVM."""
    x=np.asarray(reference,dtype=np.complex128)
    y=np.asarray(signal,dtype=np.complex128)
    if x.shape!=y.shape or not len(x):raise ValueError('waveforms mismatch')
    den=max(float(np.vdot(y,y).real),1e-20)
    gain=np.vdot(y,x)/den
    return math.sqrt(float(np.mean(np.abs(y*gain-x)**2))/max(float(np.mean(np.abs(x)**2)),1e-20))*100


def metrics(reference,output):
    r=np.asarray(reference)
    y=np.asarray(output)
    power=np.abs(y)**2
    papr=10*math.log10(max(float(power.max()),1e-20)/max(float(power.mean()),1e-20))
    envelope_err=float(np.sqrt(np.mean((np.abs(r)-np.abs(y))**2)))
    return dict(waveform_error_pct=round(normalized_error(r,y),4),
                envelope_rms_error=round(envelope_err,4),
                papr_db=round(papr,3),
                samples=len(y),simulated_only=True)


def sample_one(mode,actors,env,*,snr=30,spi_write_us=117.):
    ref,_=fixture(mode)
    out=synthesize(ref,actors,envelope=env,spi_write_us=spi_write_us)
    with tempfile.TemporaryDirectory(prefix='flux-tx-combo-') as d:
        rx=run_mode(mode,snr_db=snr,tx_model='offline_path_model',
                    transmitted_iq=out,temp_dir=d)
    return dict(**metrics(ref,out),receiver=rx)


def matrix_rows(modes,max_actors=3,spi_write_us=117.):
    configs=all_configurations(max_actors)
    # Cache phase/actual-actuator conversion per waveform and combination;
    # generating all 1,290 combinations * six modes should be reproducible.
    rows=[]
    for mode in modes:
        reference,_=fixture(mode)
        envelope_cache={name:envelope_response(abs(reference),name,spi_write_us=spi_write_us)
                        for name in ENVELOPES}
        previous=None
        phasor=None
        for config in configs:
            actors=tuple(config['actors'])
            if config['compatibility']!='conflict' and actors!=previous:
                freq=deviation_components(reference,actors,spi_write_us=spi_write_us)
                phase=np.angle(reference[0])+2*np.pi*np.cumsum(freq)/SAMPLE_RATE
                phasor=np.exp(1j*phase)
                previous=actors
            base=dict(mode=mode,actors=config['actors'],envelope=config['envelope'],
                      compatibility=config['compatibility'],
                      hardware_qualified=False,
                      amplitude_evidence=config['amplitude_evidence'],
                      actor_evidence=config['actor_evidence'])
            if config['compatibility']=='conflict':
                rows.append(dict(**base,status='conflict',reason=config['explanation']))
                continue
            try:
                out=envelope_cache[config['envelope']]*phasor
                rows.append(dict(**base,status='simulated',**metrics(reference,out)))
            except (ValueError,ArithmeticError) as exc:
                rows.append(dict(**base,status='model_error',reason=str(exc)))
    return rows


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--modes',default=','.join(MODES),help='comma-delimited modes, default all 6 including USB/LSB')
    p.add_argument('--max-actors',type=int,default=3,choices=(1,2,3))
    p.add_argument('--snr-db',type=float,default=30)
    p.add_argument('--spi-write-us',type=float,default=117.,
                   help='hypothetical per 3-wire register write latency; 117us baseline, 5us unverified fast-SPI')
    p.add_argument('--skip-soapy',action='store_true',help='enumerate/model math only, no actual SoapyFang replay')
    p.add_argument('--full-matrix',action='store_true',help='simulate every nonconflicting path, not just representative SoapyFang cases')
    p.add_argument('--regtrace',type=Path,help='JSON {clock_hz,writes:[[cycles,reg,value],...]}, from an OFFLINE K6Board trace')
    p.add_argument('--json',type=Path,required=True)
    a=p.parse_args(argv)
    modes=[x.strip() for x in a.modes.split(',')]
    if not modes or any(x not in MODES for x in modes) or len(set(modes))!=len(modes):
        p.error('invalid or duplicated mode')
    if not math.isfinite(a.snr_db) or not 0<=a.snr_db<=120:
        p.error('snr-db must be finite and 0..120')
    if not math.isfinite(a.spi_write_us) or not 0<=a.spi_write_us<=1e6:
        p.error('spi-write-us must be finite in 0..1000000')
    configs=all_configurations(a.max_actors)
    samples=[]
    for mode in modes:
        for actors,env in SOAX[mode]:
            if len(actors)>a.max_actors:continue
            status,reason=compatibility(actors)
            row=dict(mode=mode,actors=list(actors),envelope=env,
                     compatibility=status,hardware_qualified=False)
            if status=='conflict':
                samples.append(dict(**row,status='blocked',reason=reason))
                continue
            if a.skip_soapy:
                x,_=fixture(mode)
                y=synthesize(x,actors,env,spi_write_us=a.spi_write_us)
                samples.append(dict(**row,status='simulated',**metrics(x,y)))
                continue
            try:
                m=sample_one(mode,actors,env,snr=a.snr_db,spi_write_us=a.spi_write_us)
                samples.append(dict(**row,status='soapy_replayed',**m))
                recv=m['receiver']
                print(f'{mode:7} {"+".join(actors):26} {env:18} '
                      f'host decode={"PASS" if recv["pass"] else "FAIL"} '
                      f'IQ error={m["waveform_error_pct"]:.1f}%',flush=True)
            except (ValueError,RuntimeError) as exc:
                samples.append(dict(**row,status='model_error',reason=str(exc)))
    all_rows=matrix_rows(modes,a.max_actors,a.spi_write_us) if a.full_matrix else []
    observed=None
    if a.regtrace is not None:
        data=json.loads(a.regtrace.read_text())
        observed=observe_writes(data['writes'],clock_hz=data.get('clock_hz',96_000_000))
    result=dict(kind='no_hardware_tx_path_hypotheses_soapyfang_rx_model',
                physical_rf=False,hardware_qualification=False,
                caveat='Components, path sums and assumed frequency/envelope transfer functions are not calibrated hardware data',
                config_count=len(configs),
                compatible_config_count=sum(c['compatibility']!='conflict' for c in configs),
                conflicting_config_count=sum(c['compatibility']=='conflict' for c in configs),
                mode_count=len(modes),spi_write_us=a.spi_write_us,
                simulated_matrix=all_rows,sampled_replays=samples,register_trace=observed)
    a.json.parent.mkdir(parents=True,exist_ok=True)
    a.json.write_text(json.dumps(result,indent=2)+'\n')
    print(f'Enumerated {len(configs)} path combos × {len(modes)} modes; '
          f'{len(samples)} representative cases; '
          f'{sum(r["status"]=="model_error" for r in samples)} model errors; '
          f'{len(all_rows)} matrix rows',flush=True)
    return 1 if any(r['status']=='model_error' for r in samples) else 0

if __name__=='__main__':raise SystemExit(main())
