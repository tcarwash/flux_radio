#!/usr/bin/env python3
"""Offline CW register-command timeline → modeled envelope → SoapyFang IQ RX.

Uses an explicit, hypothetical REG30 gate mask and normalized REG36 driver.
Synthetic commands are not real firmware. External trace files may come from
K6Board's passive three-wire capture. Actual K6 TX is NEVER opened or keyed.
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
from sim.k6emu.rf.tx_trace_replay import (
    ReplayAssumptions,replay_envelope,schedule_cw_envelope,carrier_iq)
from sim.modulation.cw_envelope import leakage_ratio,max_edge_step
from tools.k6_soapyfang_modlab import fixture,run_mode


def run(*,write_us=117.,trace=None,gate_mask=8,gain_source='driver6',
        attack_ms=0,release_ms=0,snr_db=30):
    target,_=fixture('cw')
    if trace is not None:
        if not isinstance(trace,dict) or not isinstance(trace.get('writes'),list):
            raise ValueError('trace must provide a JSON list of passive FD6818B writes')
        opts=ReplayAssumptions(clock_hz=trace.get('clock_hz',96_000_000),
                               gate_mask=gate_mask,gain_source=gain_source,
                               attack_ms=attack_ms,release_ms=release_ms)
        writes=trace['writes']
        provenance='external_K6Board_register_write_trace'
    else:
        if gain_source!='driver6':
            raise ValueError('synthetic gain requests only support driver6')
        writes=schedule_cw_envelope(np.abs(target),write_us=write_us,gate_mask=gate_mask)
        opts=ReplayAssumptions(gate_mask=gate_mask,gain_source=gain_source,
                               attack_ms=attack_ms,release_ms=release_ms)
        provenance='synthetic_CW_driver_commands_not_firmware'
    modeled,meta=replay_envelope(writes,len(target),options=opts)
    with tempfile.TemporaryDirectory(prefix='flux-cw-trace-rx-') as d:
        rx=run_mode('cw',tx_model='offline_path_model',
                    transmitted_iq=carrier_iq(modeled),snr_db=snr_db,temp_dir=d)
    # Spectral comparison intentionally samples ideal target and modeled
    # carrier, not a physical PA RF envelope; use same FFT cutoff and length.
    cutoff=50.0
    ideal_leak=leakage_ratio(target,48_000,cutoff)
    modeled_leak=leakage_ratio(carrier_iq(modeled),48_000,cutoff)
    shape_err=float(np.max(np.abs(modeled-np.abs(target))))
    tail_rms=float(np.sqrt(np.mean(modeled[-250:]**2)))
    strict=bool(rx['pass'] and rx['key_sample_accuracy']>=.995
                and shape_err<=.1 and tail_rms<=.01)
    return dict(kind='modeled_CW_register_timing_into_SoapyFang_RX',
                physical_rf=False,firmware_transmission=False,
                actual_pa_calibration=False,source=provenance,
                command_write_us=write_us if trace is None else None,
                commands=writes,command_meta=meta,
                modeled=dict(peak_step=max_edge_step(modeled),
                             rms_tail_250_samples=tail_rms,
                             max_amplitude_error=shape_err,
                             ideal_fft_leakage=ideal_leak,
                             modeled_fft_leakage=modeled_leak,
                             leakage_ratio_relative_to_ideal=(modeled_leak/ideal_leak if ideal_leak else 0)),
                soapyfang=rx,pass_strict_shape=strict)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--write-us',type=float,default=117,
                   help='synthetic one-3wire-write interval; 5us is unverified high-speed hypothesis')
    p.add_argument('--trace',type=Path,
                   help='inert JSON {clock_hz,writes:[[cycles,reg,value],...]}, from simulator only')
    p.add_argument('--gate-mask',type=lambda x:int(x,0),default=None,
                   help='explicit HYPOTHETICAL REG30 bit; no automatic PA mapping')
    p.add_argument('--gain-source',choices=('driver6','bias4','product','gate_only'),default='driver6')
    p.add_argument('--attack-ms',type=float,default=0)
    p.add_argument('--release-ms',type=float,default=0)
    p.add_argument('--snr-db',type=float,default=30)
    p.add_argument('--json',type=Path)
    a=p.parse_args(argv)
    if not math.isfinite(a.snr_db) or not 0<=a.snr_db<=120:p.error('snr-db must be 0..120 and finite')
    if a.trace is not None and a.gate_mask is None:
        p.error('external traces need an EXPLICIT hypothetical --gate-mask; never infer the PA gate')
    if a.gate_mask is None:a.gate_mask=0x8  # only for our synthetic fixture
    if a.trace is None and (not math.isfinite(a.write_us) or not 0<a.write_us<=1e6):
        p.error('write-us must be positive and finite')
    try:
        r=run(write_us=a.write_us,trace=json.loads(a.trace.read_text()) if a.trace else None,
              gate_mask=a.gate_mask,gain_source=a.gain_source,
              attack_ms=a.attack_ms,release_ms=a.release_ms,snr_db=a.snr_db)
    except (ValueError,TypeError) as e:p.error(str(e))
    if a.json:
        a.json.parent.mkdir(parents=True,exist_ok=True)
        a.json.write_text(json.dumps(r,indent=2)+'\n')
    print(f'CW trace {"PASS" if r["pass_strict_shape"] else "FAIL"}: '
          f'{len(r["commands"])} source register writes, '
          f'key accuracy {r["soapyfang"]["key_sample_accuracy"]:.6f}, '
          f'max envelope error {r["modeled"]["max_amplitude_error"]:.4f}, '
          f'leakage/ideal {r["modeled"]["leakage_ratio_relative_to_ideal"]:.3f}',flush=True)
    return 0 if r['pass_strict_shape'] else 1

if __name__=='__main__':raise SystemExit(main())
