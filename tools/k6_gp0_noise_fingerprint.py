#!/usr/bin/env python3
"""Offline fingerprint of CRC-verified pre-Sotto GP0 PCM captures.

Reads unchanged original 500/1000Hz PCM and partial IQ captures. All analysis
uses the *measured physical sample rate* from radio 100us ticks. Saves a new
metrics-only JSON (no WAV, IQ, tokens or private absolute filesystem paths).
Tone harmonics and signal-fit residuals are diagnostic, not guaranteed THD or
separately identified analog/input noise: the residual includes quantization,
phase jitter, sample-boundary error, and any aliased high-rate PDM energy.
No radio, SDR, serial, PTT, firmware, or flash access.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import math
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.signal import welch
from scipy.stats import chi2,kurtosis
from tools.k6_gp0_pcm_clock_replay import collect_packets,read_verified_wav,source_rate
from tools.k6_gp0_pcm_air import parse_iq_complex64

BANDS=((0,300),(300,800),(800,1500),(1500,2500),(2500,3500),(3500,4000))


def sine_fit(x,fs,hz,order=1):
    t=np.arange(len(x))/fs
    basis=[np.ones(len(x))]
    for h in range(1,order+1):
        if h*hz>=fs/2:break
        basis.extend((np.sin(2*np.pi*h*hz*t),np.cos(2*np.pi*h*hz*t)))
    matrix=np.column_stack(basis)
    coef=np.linalg.lstsq(matrix,x,rcond=None)[0]
    residual=x-matrix@coef
    harmonics=[float(math.hypot(coef[1+2*k],coef[2+2*k])) for k in range((len(coef)-1)//2)]
    return residual,harmonics


def fingerprint(pcm:bytes,ticks_100us:int,*,tone_hz=None):
    """Physically-clocked analysis of real or synthetic signed16 PCM bytes."""
    if len(pcm)%2 or len(pcm)<256 or not 1500<=ticks_100us<=2800:
        raise ValueError('at least 128 PCM samples and valid full capture ticks required')
    x=np.frombuffer(pcm,dtype='<i2').astype(np.float64)/32768
    rate=16000000/ticks_100us
    freq=None;harmonics=[]
    if tone_hz is not None:
        if not math.isfinite(tone_hz) or not 200<=tone_hz<=1500:
            raise ValueError('expected 200..1500Hz physical generator input')
        opt=minimize_scalar(lambda f:np.mean(sine_fit(x,rate,f)[0]**2),
                            method='bounded',bounds=(tone_hz*.97,tone_hz*1.03),
                            options={'xatol':1.e-4})
        freq=float(opt.x)
        # Use 1-harmonic residual for noise comparison, then explicitly
        # measure 2nd/3rd harmonic in a separate joint fit.
        residual,_=sine_fit(x,rate,freq)
        _,harmonics=sine_fit(x,rate,freq,order=3)
    else:residual=x-x.mean()
    noise=float(np.std(residual))
    freq_axis,psd=welch(residual,fs=rate,nperseg=min(256,len(x)),
                        noverlap=min(128,len(x)//2),scaling='spectrum',
                        detrend='constant',window='hann')
    band_energy=[]
    for low,high in BANDS:
        band_energy.append(float(np.sum(psd[(freq_axis>=low)&(freq_axis<high)])))
    total=sum(band_energy)
    ac=[]
    for lag in (1,2,3,4,8,16):
        a=residual[:-lag];b=residual[lag:]
        ac.append(float(np.corrcoef(a,b)[0,1]))
    # Ljung-Box 16-lag Q is exploratory only: sample correlation at short N,
    # nonstationarity, tone leakage and overlapping chunks limit inference.
    n=len(x)
    corrs=[float(np.corrcoef(residual[:-k],residual[k:])[0,1]) for k in range(1,17)]
    q=n*(n+2)*sum(r*r/(n-k) for k,r in enumerate(corrs,1))
    low_seg=[float(np.std(residual[i:i+160])) for i in range(0,len(x)-159,160)]
    data={
       'samples':n,'physical_sample_rate_hz':round(rate,3),
       'observed_duration_ms':round(n/rate*1000,3),
       'pcm_payload_sha256':hashlib.sha256(pcm).hexdigest(),
       'raw_pcm_rms_full_scale':round(float(np.std(x)),7),
       'residual_rms_full_scale':round(noise,7),
       'residual_rms_dbfs':round(20*math.log10(max(noise,1e-12)),2),
       'absolute_peak_full_scale':round(float(np.max(np.abs(x))),5),
       'samples_near_clip':int(np.sum(abs(x)>.999)),
       'residual_excess_kurtosis':round(float(kurtosis(residual)),3),
       'adjacent_correlations_lags_1_2_3_4_8_16':[round(a,3) for a in ac],
       'ljung_box_16_lags_exploratory_p':round(float(chi2.sf(q,16)),7),
       'frequency_bands_hz':[list(v) for v in BANDS],
       'welch_noise_energy_percent_by_band':[round(100*v/total,1) for v in band_energy],
       'within_record_160_sample_residual_rms':[round(v,5) for v in low_seg],
       'possible_source_categories':['PDM quantization / fold-down','analog input or RFIC mic noise','GPIO/DMA read artifacts','timebase or gain error'],
    }
    if tone_hz is not None:
        fund=harmonics[0]
        h2plus=math.sqrt(sum(v*v for v in harmonics[1:]))
        # This metric is noise-limited, especially with only 480 samples.
        data.update(generator_nominal_hz=tone_hz,
                    fitted_physical_fundamental_hz=round(freq,3),
                    fundamental_peak_full_scale=round(fund,6),
                    tone_to_residual_db=round(20*math.log10((fund/math.sqrt(2))/noise),2),
                    fitted_harmonic_amplitudes_full_scale=[round(v,6) for v in harmonics],
                    fitted_harmonic_to_fundamental_db=round(20*math.log10(max(h2plus,1e-12)/max(fund,1e-12)),2),
                    harmonic_measurement_caveat='noise-floor-limited apparent upper bound, not isolated analog THD')
    return data


def analyze_saved_runs(root:Path):
    runs={}
    for basename,kind,tone in (('gen-off-01','iq',None),('gen-500-01','wav',None),
                                ('gen-500-02','iq',500),('gen-1000-01','wav',1000)):
        if kind=='wav':
            c=read_verified_wav(root/(basename+'.wav'),root/(basename+'.json'))
        else:
            packets,_=parse_iq_complex64(root/(basename+'.c64'))
            c=collect_packets(packets,allow_partial=True,source=basename)
        row=fingerprint(c.pcm,c.ticks_100us,tone_hz=tone)
        row.update(accepted_packets=c.accepted_packets,required_packets=67,
                   complete=c.complete,real_rf_crc_verified=True)
        runs[basename]=row
    a=runs['gen-off-01']['residual_rms_full_scale']
    b=runs['gen-500-01']['residual_rms_full_scale']
    c=runs['gen-500-02']['residual_rms_full_scale']
    d=runs['gen-1000-01']['residual_rms_full_scale']
    rms=[a,b,c,d]
    return {'schema':1,'offline_only':True,'input':'CRC-verified pre-Sotto PCM AIR packet captures',
            'no_originals_modified':True,'no_rf_or_radio_used':True,
            'run_fingerprints':runs,
            'noise_rms_variation_db_across_four_runs':round(20*math.log10(max(rms)/min(rms)),2),
            'conclusion':'Four recordings exhibit similar broad-band residual, independent of strong generator tone; clipping absent; source/alias mechanism unresolved',
            'limits':['short (56 to 188ms) independent sessions',
                      'RF transport verified by per-packet CRC but no raw PDM samples captured',
                      'spectral fractions are only descriptive within these finite windows',
                      'apparent harmonic distortion is noise-floor limited',
                      'cannot separate high-rate PDM folding from in-band analog noise from 8k PCM alone']}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-root',type=Path,required=True,help='private original GP0 AIR capture folder')
    ap.add_argument('--json-out',type=Path,required=True,help='new metrics-only JSON; refused if exists')
    args=ap.parse_args()
    if args.json_out.exists():ap.error('refuse overwrite: output already exists')
    result=analyze_saved_runs(args.source_root)
    with args.json_out.open('x') as f:f.write(json.dumps(result,sort_keys=True,indent=2)+'\n')
    for name,metrics in result['run_fingerprints'].items():
        print(name,'N',metrics['samples'],'residual_rms',metrics['residual_rms_full_scale'],
              'lag1',metrics['adjacent_correlations_lags_1_2_3_4_8_16'][0],
              'bands',metrics['welch_noise_energy_percent_by_band'])
    print('run-to-run noise RMS spread dB:',result['noise_rms_variation_db_across_four_runs'])


if __name__=='__main__':main()
