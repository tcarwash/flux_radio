#!/usr/bin/env python3
"""Pilot-based DQPSK receive feasibility using SoapyFang receive-only IQ.

Compares phase-integral signed-FM hypothesis against a real RX FIR; no TX,
DSP firmware or RF hardware. Native REG52 CTCSS phase cannot synthesize RF
carrier phase in this lab and is not used.
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
from sim.modulation.dqpsk_polar import Parameters,synthesize,PHASES,RATE
from tools.k6_frequency_modem_lab import rx_soapy


def decode_known_frame(model,iq):
    """Recover phase increments with pilot-only phase/frequency calibration.

    Frame start and baud are KNOWN, so this is a feasibility diagnostic and
    not a blind-acquisition or production demodulator. Neither held-out bits
    nor desired IQ are used to train the detector. Public sync is independent.
    """
    z=np.asarray(iq,dtype=np.complex128)
    syms=model['symbols']
    pilot_n=model['pilot_n'];sync_n=model['sync_n']
    sps=model['samples_per_symbol']
    if z.ndim!=1 or len(z)<(len(syms)+1)*sps or not np.isfinite(z).all():
        raise ValueError('received finite complex IQ must contain entire frame')
    best=None
    # Unknown 0..24-sample RX group delay searched on public pilot, and at
    # each boundary use an averaged complex sample to reduce Gaussian noise.
    for lag in range(0,25):
        # Sampling same relative phase across successive boundaries produces
        # meaningful dphase even with an unknown absolute RF phase.
        boundaries=np.arange(len(syms)+1)*sps + lag
        if boundaries[-1]+4>=len(z):continue
        point=np.array([np.mean(z[p:p+4]) for p in boundaries])
        increments=np.angle(point[1:]*np.conj(point[:-1]))
        centroids=np.array([np.angle(np.mean(np.exp(1j*increments[:pilot_n][syms[:pilot_n]==k]))) for k in range(4)])
        corr=np.abs(np.angle(np.exp(1j*(increments[:pilot_n]-centroids[syms[:pilot_n]]))))
        err=float(np.mean(corr**2))
        if best is None or err<best[0]:best=(err,lag,increments,centroids)
    if best is None:raise RuntimeError('unable to acquire pilot phase increments')
    error,lag,inc,means=best
    distance=np.abs(np.angle(np.exp(1j*(inc[:,None]-means[None,:]))))
    decisions=np.argmin(distance,axis=1)
    sync_errors=int(np.count_nonzero(decisions[pilot_n:pilot_n+sync_n]!=syms[pilot_n:pilot_n+sync_n]))
    payload=syms[pilot_n+sync_n:]
    observed=decisions[pilot_n+sync_n:]
    xor=np.asarray(payload^observed,dtype=np.uint8)
    bit_errors=int(np.count_nonzero(xor&1)+np.count_nonzero(xor&2))
    # Codes 1 and 2 are +90 and -90 respectively. Pilot fitting by itself
    # could make a reversed physical polarity appear as perfect packet BER.
    measured_sign=np.angle(np.exp(1j*(means[1]-means[0])))
    sign_valid=bool(measured_sign>.08)
    constellation_span_degrees=float(np.degrees(np.abs(measured_sign)))
    return dict(payload_bit_errors=bit_errors,
                payload_ber=bit_errors/(2*len(payload)),
                payload_symbol_errors=int(np.count_nonzero(payload!=observed)),
                sync_errors=sync_errors,pilot_mse_rad2=error,
                rx_group_delay_samples=lag,
                public_mean_angles_deg=[float(np.degrees(v)) for v in means],
                signed_frequency_mapping_valid=sign_valid,
                phase_level_span_degrees=constellation_span_degrees,
                acquisition='known_frame_start_pilot_only_phase_centroids')


def run(params:Parameters=Parameters(),*,snr_db=30.,cfo_hz=0.,
        reverse_sign=False,disable_updates=False):
    if not math.isfinite(snr_db) or not 0<=snr_db<=120:
        raise ValueError('bounded finite SNR needed')
    if not math.isfinite(cfo_hz) or abs(cfo_hz)>200:
        raise ValueError('bounded finite CFO needed')
    src=synthesize(params,reverse_sign=reverse_sign,disable_updates=disable_updates)
    n=np.arange(len(src['iq']))
    transmitted=np.asarray(src['iq']*np.exp(2j*np.pi*cfo_hz*n/RATE),dtype=np.complex64)
    observed=rx_soapy(transmitted,snr_db=snr_db)
    decoded=decode_known_frame(src,observed)
    qualified=(decoded['payload_ber']==0 and decoded['sync_errors']==0
               and decoded['signed_frequency_mapping_valid']
               and decoded['phase_level_span_degrees']>20.
               and src['dropped_writes']==0)
    return dict(kind='hypothetical_signed_frequency_integral_DQPSK',
                physical_tx=False,hardware_qualified=False,
                true_linear_qpsk=False,stock_k6_supported=False,
                baud=params.baud,spi_write_us=params.spi_write_us,
                bandwidth_hz=params.analog_bandwidth_hz,
                dac_bits=params.dac_bits,cfo_hz=cfo_hz,snr_db=snr_db,
                reverse_sign=reverse_sign,disable_updates=disable_updates,
                register_writes=len(src['writes']),dropped_register_writes=src['dropped_writes'],
                max_desired_deviation_hz=src['max_desired_hz'],
                iq_constant_envelope=bool(np.max(np.abs(np.abs(src['iq'])-1))<2e-6),
                **decoded,simulated_decode_pass=bool(qualified))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baud',type=int,default=100)
    p.add_argument('--write-us',type=float,default=117)
    p.add_argument('--bandwidth-hz',type=float,default=135)
    p.add_argument('--max-deviation-hz',type=float,default=1500)
    p.add_argument('--dac-bits',type=int,default=7)
    p.add_argument('--cfo-hz',type=float,default=0)
    p.add_argument('--snr-db',type=float,default=30)
    p.add_argument('--reverse-sign',action='store_true')
    p.add_argument('--disable-updates',action='store_true')
    p.add_argument('--json',type=Path)
    args=p.parse_args(argv)
    params=Parameters(baud=args.baud,spi_write_us=args.write_us,
                      analog_bandwidth_hz=args.bandwidth_hz,
                      max_deviation_hz=args.max_deviation_hz,
                      dac_bits=args.dac_bits)
    try:result=run(params,snr_db=args.snr_db,cfo_hz=args.cfo_hz,
                   reverse_sign=args.reverse_sign,disable_updates=args.disable_updates)
    except (ValueError,RuntimeError) as exc:p.error(str(exc))
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(result,indent=2)+'\n')
    print(f'Modeled DQPSK {"PASS" if result["simulated_decode_pass"] else "FAIL"}: '
          f'{result["baud"]} baud, {result["bandwidth_hz"]:.0f}Hz LPF, '
          f'BER={result["payload_ber"]:.5f}, '
          f'pilot-level-separation={result["phase_level_span_degrees"]:.1f} degrees, '
          f'updates={result["register_writes"]}, dropped={result["dropped_register_writes"]}')
    return 0 if result['simulated_decode_pass'] else 1

if __name__=='__main__':raise SystemExit(main())
