#!/usr/bin/env python3
"""Offline blind-FSK packet acquisition sensitivity under synthetic IQ impairments.

Reuses already-captured physical SDR I/Q. Injects controlled additive complex
noise and software frequency offsets; does NOT key or access any hardware.
Results are not actual SNR/PER/link-budget measurements and cannot predict
receiver performance at a stated calibrated SNR.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from k6_fsk_iq_edges import read_iq,FS
from k6_fsk_sotto_sync_rx import find_frames

NOISE_RATIOS=(0,.25,.5,1.)
FREQUENCY_SHIFTS=(-1000.,0.,1000.)


def sweep(iq, *,noise_ratios=NOISE_RATIOS,frequency_shifts=FREQUENCY_SHIFTS,seed=20261123):
    n=len(iq)
    if n<FS:raise ValueError('capture too short')
    x=np.asarray(iq,dtype=np.complex128)
    # Use a known *time interval containing RF energy* for a reproducible
    # noise amplitude; NOT a decoder input or a calibrated external SNR.
    lo=int(.85*FS);hi=min(n,int(1.83*FS))
    ref=float(np.median(np.abs(x[lo:hi])))
    if ref<=0:raise ValueError('No signal amplitude in predeclared survey window')
    t=np.arange(n,dtype=np.float64)/FS
    results=[]
    for ratio in noise_ratios:
        rng=np.random.default_rng(seed+round(1e3*ratio))
        noise=(rng.standard_normal(n)+1j*rng.standard_normal(n))*(ref*ratio/np.sqrt(2))
        impaired=x+noise
        for hz in frequency_shifts:
            shifted=impaired*np.exp(2j*np.pi*hz*t)
            r=find_frames(shifted)
            accepted=[(p['msg_id'],p['seq']) for p in r['accepted']]
            results.append(dict(noise_amplitude_to_signal_ratio=ratio,
                                artificial_baseband_offset_hz=hz,
                                sync_candidates=r['frames_found'],
                                crc_valid=r['crc_valid'],
                                found_message_sequence=accepted,
                                has_all_eight=sorted(accepted)==[(73,i) for i in range(8)]))
    return dict(experiment='Offline additive IQ noise and frequency shift replay',
                input_signal_magnitude_reference=ref,
                input_signal_reference_window_s=[.85,1.83],
                artificial_noise_power_snr_uncalibrated=True,
                applied_to_saved_capture_only=True,
                random_seed=seed,scenarios=results,
                caveats=['Noise is digitally added to a previously excellent dummy-load IQ capture.',
                         'SNR values are relative injected-noise ratios, not calibrated RF link SNR.',
                         'Live RF capture and MCU CPU/packet costs are not tested.',
                         'Receiver knows only 4800bps nominal, preamble and sync, 64B L profile.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():p.error('Refusing to overwrite an existing report')
    iq=read_iq(args.iq,duration=3.0,offset=280500.)
    result=sweep(iq)
    with args.iq.open('rb') as f:result['source_capture_sha256']=hashlib.file_digest(f,'sha256').hexdigest()
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    for x in result['scenarios']:
        print('added amplitude noise ratio',x['noise_amplitude_to_signal_ratio'],
              'RF freq shift',x['artificial_baseband_offset_hz'],
              'Hz ->',x['crc_valid'],'CRC packets;',
              '8/8' if x['has_all_eight'] else 'partial')


if __name__=='__main__':main()
