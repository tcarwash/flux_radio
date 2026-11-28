#!/usr/bin/env python3
"""Offline TX CW amplitude-filter sweep analyzer; does not operate a radio.

--synthetic validates fitter against a known 1-pole LPF without fabricating
physical RF evidence. --manifest reads raw complex64 IQ + actual normalized
command arrays supplied *after* a separate, authorized bench capture.
Neither mode opens a serial port, SDR, PTT, RF output or network.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from sim.modulation.cw_tx_response import (
    analyze_tone,fit_single_pole,synth_tone,bounded_lowpass_preemphasis)
from sim.modulation.cw_envelope import raised_cosine_envelope,text_to_schedule,leakage_ratio
from sim.k6emu.rf.tx_trace_replay import (
    schedule_cw_envelope,replay_envelope,ReplayAssumptions,carrier_iq)

DEFAULT_FREQS=(10,20,40,80,120,200,350,500,800,1000)


def _json_object(path):
    data=json.loads(Path(path).read_text())
    if not isinstance(data,dict):raise ValueError('manifest must be an object')
    return data


def _read_capture(path,dtype,limit=600000):
    f=Path(path)
    if not f.is_file():raise ValueError(f'missing IQ or command capture: {f}')
    item=np.dtype(dtype)
    if f.stat().st_size%item.itemsize:
        raise ValueError(f'not an integral {item.name} file: {f}')
    n=f.stat().st_size//item.itemsize
    if n<256 or n>limit:
        raise ValueError('capture length outside bounded 256..600000 samples')
    return np.fromfile(f,dtype=item)


def read_manifest(path):
    """Manifest requires per-sample *actual* command trace and same-clock IQ.

    A manifest claiming physical RF is an operator assertion, NOT a validation
    that the load, lease or calibration actually existed. The analyzer refuses
    to extrapolate hardware response from an idealized command waveform.
    """
    p=Path(path).resolve()
    doc=_json_object(p)
    rate=doc.get('sample_rate_hz')
    if type(rate) not in (int,float) or not math.isfinite(rate) or not 1000<=rate<=10_000_000:
        raise ValueError('sample_rate_hz must be finite in 1k..10M')
    provenance=doc.get('source_kind')
    if provenance not in ('synthetic','operator_declared_conducted_iq'):
        raise ValueError('source_kind must explicitly label synthetic vs operator-declared conducted')
    runs=doc.get('runs')
    if not isinstance(runs,list) or not 4<=len(runs)<=40:
        raise ValueError('manifest requires 4..40 frequency runs')
    if provenance=='operator_declared_conducted_iq':
        required=('sdr_agc_disabled','sdr_fixed_gain','external_dummy_load_confirmed',
                  'gain_settings_recorded','tx_restore_verified')
        if not all(doc.get(k) is True for k in required):
            raise ValueError('physical-evidence manifest lacks AGC-off/bench/restore declarations')
    points=[]
    for row in runs:
        if not isinstance(row,dict) or not all(k in row for k in ('frequency_hz','iq_cf32le','command_f32le')):
            raise ValueError('each run needs frequency_hz, IQ and measured command arrays')
        f=float(row['frequency_hz'])
        iq=_read_capture(p.parent/row['iq_cf32le'],'<c8')
        cmd=_read_capture(p.parent/row['command_f32le'],'<f4')
        if len(iq)!=len(cmd):
            raise ValueError('IQ and command waveform must have identical sample count and timebase')
        points.append(analyze_tone(cmd,iq,rate,f,
                                   known_rf=provenance=='operator_declared_conducted_iq'))
    return dict(points=points,provenance=provenance,rate=rate,
                manifest=str(p),declarations_verified=False)


def analyze_synthetic(*,cutoff_hz=90.,am_pm_deg=12.,snr_db=50.,
                      frequencies=DEFAULT_FREQS):
    points=[]
    for hz in frequencies:
        command,iq=synth_tone(hz,cutoff_hz=cutoff_hz,
                              rf_am_pm_deg=am_pm_deg,snr_db=snr_db)
        points.append(analyze_tone(command,iq,48000,hz))
    return dict(points=points,provenance='synthetic',rate=48000,
                synthetic_settings=dict(cutoff_hz=cutoff_hz,
                                        am_pm_deg=am_pm_deg,snr_db=snr_db),
                declarations_verified=False)


def cw_forecast(cutoff_hz,*,write_us=117.,rate=48000,ramps_ms=(4.,5.,8.,12.)):
    """Conditional CW ramp comparison: ordinary vs bounded pre-emphasis.

    No hardware amplitude transfer or safe PA ceiling is inferred by this
    operation. Quantized SPI scheduling, model-only response, RF-off after
    each mark and relative spectral leakage are all made explicit.
    """
    if not math.isfinite(cutoff_hz) or not 1<=cutoff_hz<=5000:
        raise ValueError('corner frequency outside bounded 1..5000 Hz')
    tau_ms=1000/(2*math.pi*cutoff_hz)
    sched=text_to_schedule('CQ',20)
    records=[]
    for ramp_ms in ramps_ms:
        target=raised_cosine_envelope(sched,rate,ramp_ms/1000)
        # A final CQ letter/word gap is long. Evaluate EVERY real key-off,
        # not the silence after file end (which can hide an RF tail).
        cursor=0
        keyoffs=[]
        for key_on,seconds in sched:
            length=int(round(seconds*rate))
            if key_on and length:keyoffs.append(cursor+length)
            cursor+=length
        for method,command_target in (
            ('naive',target),
            ('clipped_one_pole_inverse',bounded_lowpass_preemphasis(
                target,rate=rate,cutoff_hz=cutoff_hz))):
            commands=schedule_cw_envelope(command_target,rate=rate,write_us=write_us)
            model,meta=replay_envelope(commands,len(target)+int(rate*.025),
                                       options=ReplayAssumptions(sample_rate=rate,
                                                                 attack_ms=tau_ms,
                                                                 release_ms=tau_ms))
            compare=model[:len(target)]
            out_of_band=leakage_ratio(carrier_iq(compare,rate=rate),rate,50)
            ideal_out=leakage_ratio(carrier_iq(target,rate=rate),rate,50)
            def post_keyoff(ms):
                skip=int(round(ms*rate/1000))
                vals=[float(model[k+skip]) for k in keyoffs if k+skip<len(target)]
                return max(vals,default=0.)
            records.append(dict(ramp_ms=ramp_ms,method=method,
                         simulated_write_count=len(commands),
                         max_amplitude_error=float(np.max(abs(compare-target))),
                         mean_absolute_error=float(np.mean(abs(compare-target))),
                         relative_50hz_leakage=(out_of_band/ideal_out if ideal_out else 0.),
                         post_keyoff_1ms_peak=post_keyoff(1.),
                         post_keyoff_5ms_peak=post_keyoff(5.),
                         post_keyoff_10ms_peak=post_keyoff(10.),
                         assumed_one_pole_ms=tau_ms,physical_rf=False,
                         modeled_only=True))
    return records


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--synthetic',action='store_true')
    group.add_argument('--manifest',type=Path)
    parser.add_argument('--synthetic-cutoff-hz',type=float,default=90.)
    parser.add_argument('--synthetic-am-pm-deg',type=float,default=12.)
    parser.add_argument('--snr-db',type=float,default=50.)
    parser.add_argument('--write-us',type=float,default=117.)
    parser.add_argument('--json',type=Path)
    a=parser.parse_args(argv)
    if not math.isfinite(a.write_us) or not 0<a.write_us<=10000:
        parser.error('write-us must be finite and in 0..10000')
    try:
        report=analyze_synthetic(cutoff_hz=a.synthetic_cutoff_hz,
                                 am_pm_deg=a.synthetic_am_pm_deg,snr_db=a.snr_db) if a.synthetic else read_manifest(a.manifest)
        fit=fit_single_pole(report['points'],physical=False)
        report.update(kind='cw_tx_sweep_post_capture_analysis',physical_rf_verified=False,
                      measured_physical_rf=None if report['provenance']=='synthetic'
                      else 'operator_declared_not_independently_verified',
                      one_pole_fit=fit)
        report['cw_forecasts']=cw_forecast(fit['estimated_cutoff_hz'],write_us=a.write_us)
    except (ValueError,TypeError,RuntimeError,OSError) as exc:
        parser.error(str(exc))
    if a.json:
        a.json.parent.mkdir(parents=True,exist_ok=True)
        a.json.write_text(json.dumps(report,indent=2)+'\n')
    print(f"TX sweep {report['provenance']}: fitted one-pole {fit['estimated_cutoff_hz']:.2f} Hz "
          f"(rms {fit['response_fit_rms_db']:.3f} dB; credible={fit['fit_credible']})")
    for forecast in report['cw_forecasts']:
        print(f"  ramp {forecast['ramp_ms']:.1f} ms {forecast['method']}: peak error "
              f"{forecast['max_amplitude_error']:.3f}, +5ms-keyoff "
              f"{forecast['post_keyoff_5ms_peak']:.5f}, "
              f"FFT leakage/ideal {forecast['relative_50hz_leakage']:.3f}")
    return 0 if fit['fit_credible'] else 1

if __name__=='__main__':raise SystemExit(main())
