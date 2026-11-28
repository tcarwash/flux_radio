#!/usr/bin/env python3
"""Analyze hypothetical bounded AM driver commands through SoapyFang RX IQ.

All writes are synthetic virtual 3-wire traces; no physical radio TX, SDR or
PA gate is invoked. Neither analog envelope linearity nor REG30 gate mapping
have been demonstrated on FD6818B B-chip hardware.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from sim.k6emu.rf.am_trace import schedule_driver_am,tone_quality,spectral_quality
from sim.k6emu.rf.tx_trace_replay import ReplayAssumptions,replay_envelope,carrier_iq
from tools.k6_soapyfang_modlab import fixture,_iq_reference_alignment,RATE
from tools.k6_frequency_modem_lab import rx_soapy


def run(*,write_us=117.,gain_ceiling=0x22,attack_ms=0.,release_ms=0.,
        snr_db=45.,carrier_phase_coupling_deg=0.):
    if not math.isfinite(carrier_phase_coupling_deg) or not -180<=carrier_phase_coupling_deg<=180:
        raise ValueError('phase-coupling model must be bounded')
    if not math.isfinite(snr_db) or not 0<=snr_db<=120:
        raise ValueError('SNR must be finite and in 0..120 dB')
    reference,_=fixture('am')
    # Normalize ideal 70%-modulated AM into strictly downward-only driver6
    # commands: no synthetic register ever exceeds declared low-power limit.
    ref_amplitude=np.abs(reference)
    desired=ref_amplitude/float(ref_amplitude.max())
    writes=schedule_driver_am(desired,write_us=write_us,gain_ceiling=gain_ceiling)
    analog,meta=replay_envelope(writes,len(reference)+int(RATE*.010),
                                options=ReplayAssumptions(attack_ms=attack_ms,
                                                          release_ms=release_ms))
    # This deliberately injects *hypothetical* AM-to-PM coupling, measuring
    # the price of an unqualified polar path; no RFIC transfer curve is known.
    pm=np.deg2rad(carrier_phase_coupling_deg)*(analog[:len(reference)]-np.mean(analog[:len(reference)]))
    iq=(analog[:len(reference)]*np.exp(1j*pm)).astype(np.complex64)
    filtered=rx_soapy(iq,snr_db=snr_db)
    aligned,lag,mse=_iq_reference_alignment(reference,filtered)
    # A 25-ms stable analysis excludes the initial 2-transaction carrier
    # startup and end-of-file filter tail; those remain explicitly reported.
    raw_quality=tone_quality(analog[:len(reference)],trim_ms=25.)
    rf_spectral=spectral_quality(aligned,trim_ms=25.)
    rf_quality=tone_quality(np.abs(aligned),trim_ms=25.)
    ref_quality=tone_quality(np.abs(reference),trim_ms=25.)
    a=np.abs(aligned)[1200:-1200]
    ideal=np.abs(reference)[1200:-1200]
    correlation=float(np.corrcoef(a,ideal)[0,1])
    tail=analog[len(reference):]
    tail_rms=float(np.sqrt(np.mean(tail**2)))
    # Assumes an analog RF driver that follows normalized gain instantly.
    # These gates are **simulation qualification only**, not AM TX approval.
    envelope_ok=(abs(rf_quality['depth']-.7)<.055 and correlation>.985
                 and rf_quality['thd_percent']<12. and tail_rms<.02)
    spectral_ok=(envelope_ok and abs(rf_spectral['sideband_asymmetry_db'])<2.
                 and rf_spectral['phase_rms_degrees']<6.)
    return dict(kind='hypothetical_REG36_driver6_AM_to_SoapyFang_RX',
                physical_rf=False,device_register_writes=False,
                hardware_am_qualified=False,
                gain_max_code=gain_ceiling,write_us=write_us,
                speaker_am_decode=False,
                modeled_analog_response_ms=[attack_ms,release_ms],
                hypothetical_am_pm_coupling_deg=carrier_phase_coupling_deg,
                commands=writes,command_count=len(writes),
                replay=meta,reference=ref_quality,virtual_pa=raw_quality,
                soapyfang_envelope=rf_quality,
                soapyfang_spectrum=rf_spectral,
                soapyfang_iq_reference_lag_samples=lag,
                soapyfang_frontend_mse_ratio=mse,
                envelope_correlation=correlation,
                post_key_up_rms=tail_rms,
                simulated_am_shape_pass=bool(envelope_ok),
                simulated_am_spectral_pass=bool(spectral_ok))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--write-us',type=float,default=117.)
    p.add_argument('--ceiling',type=lambda s:int(s,0),default=0x22)
    p.add_argument('--attack-ms',type=float,default=0.)
    p.add_argument('--release-ms',type=float,default=0.)
    p.add_argument('--am-pm-degrees',type=float,default=0.)
    p.add_argument('--snr-db',type=float,default=45.)
    p.add_argument('--json',type=Path)
    opts=p.parse_args(argv)
    try:
        result=run(write_us=opts.write_us,gain_ceiling=opts.ceiling,
                   attack_ms=opts.attack_ms,release_ms=opts.release_ms,
                   snr_db=opts.snr_db,carrier_phase_coupling_deg=opts.am_pm_degrees)
    except (ValueError,RuntimeError) as e:p.error(str(e))
    if opts.json:
        opts.json.parent.mkdir(parents=True,exist_ok=True)
        opts.json.write_text(json.dumps(result,indent=2)+'\n')
    print(f'Modeled AM {"PASS" if result["simulated_am_spectral_pass"] else "FAIL"}: '
          f'write={result["write_us"]:.1f}us count={result["command_count"]} '
          f'depth={result["soapyfang_envelope"]["depth"]:.3f} '
          f'THD={result["soapyfang_envelope"]["thd_percent"]:.2f}% '
          f'corr={result["envelope_correlation"]:.4f} '
          f'sideband={result["soapyfang_spectrum"]["sideband_asymmetry_db"]:.1f} dB '
          f'phase_rms={result["soapyfang_spectrum"]["phase_rms_degrees"]:.1f} deg '
          f'tail={result["post_key_up_rms"]:.4f}',flush=True)
    return 0 if result['simulated_am_spectral_pass'] else 1

if __name__=='__main__':raise SystemExit(main())
