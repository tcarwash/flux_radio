#!/usr/bin/env python3
"""Feed five offline waveform models through actual SoapyFang receive filtering.

Uses generated SigMF IQ and the existing *receive-only* RFBackend/ChipModel,
not a hardware device or an invented TX capability. The tap observes the
complex channel-filter output that normally goes into the FM discriminator;
mode-specific host reference decoders consume that observed filtered IQ.
Neither the K6 firmware nor its FM discriminator decodes AM/SSB/QPSK/OFDM.
"""
from __future__ import annotations
import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))
from sim.k6emu.rf.backend import create_backend
from sim.k6emu.rf.sources import write_sigmf
from sim.modulation import am_waveform as am, cw_envelope as cw, ssb_iq as ssb
from sim.modulation import qpsk_lab as qpsk, ofdm_lab as ofdm

RATE = 48_000
CENTER_HZ = 440_850_000  # numeric simulated tuning only; no physical TX
MODES = ('cw','am','ssb','ssb-lsb','qpsk','ofdm')


def fixture(mode: str):
    """Deterministic IQ + ground truth, all at same channel model sample rate."""
    if mode == 'cw':
        schedule=cw.text_to_schedule('CQ', 20)
        shaped=cw.raised_cosine_envelope(schedule,RATE,.005)
        return cw.cw_iq(shaped,RATE), {'envelope':shaped, 'schedule':schedule}
    if mode == 'am':
        t=np.arange(12_000)/RATE
        audio=np.cos(2*np.pi*1000*t)
        return am.am_baseband(audio,.7,RATE),{'audio':audio,'depth':.7}
    if mode in ('ssb','ssb-lsb'):
        t=np.arange(12_000)/RATE
        audio=.5*np.cos(2*np.pi*800*t)+.35*np.cos(2*np.pi*1400*t)
        return ssb.ssb_modulate(audio,RATE,sideband=('lsb' if mode=='ssb-lsb' else 'usb')),{'audio':audio}
    if mode == 'qpsk':
        rng=np.random.default_rng(44)
        bits=rng.integers(0,2,512,dtype=np.int8)
        return qpsk.modulate(bits,sps=32,alpha=.35,span_symbols=8), {'bits':bits,'sps':32}
    if mode == 'ofdm':
        bits=ofdm.deterministic_bits(80*16,seed=44)
        return ofdm.modulate_bits(bits), {'bits':bits}
    raise ValueError(f'unknown mode {mode!r}')


def _iq_reference_alignment(ideal, received, max_lag=22):
    """Estimate and remove receiver FIR group delay and one complex gain.

    This is a genie-aided reference, not a carrier/timing acquisition modem.
    No private or original IQ is substituted into the demodulator.
    """
    n=min(len(ideal),len(received))
    ideal=np.asarray(ideal[:n],dtype=complex)
    received=np.asarray(received[:n],dtype=complex)
    best=None
    for lag in range(min(max_lag,n-2)+1):
        x=ideal[:n-lag]
        y=received[lag:n]
        g=np.vdot(x,y)/max(float(np.vdot(x,x).real),1e-15)
        e=float(np.mean(np.abs(y-g*x)**2)) / max(float(np.mean(np.abs(y)**2)),1e-15)
        if best is None or e < best[0]:
            best=(e,lag,g)
    _,lag,g=best
    if abs(g)<1e-8:
        raise ValueError('missing carrier or too little IQ at receiver tap')
    aligned=received[lag:]/g
    return np.pad(aligned,(0,lag)),lag,float(best[0])


def _am_depth(envelope):
    hi=float(np.percentile(envelope,99.9))
    lo=float(np.percentile(envelope,.1))
    return (hi-lo)/(hi+lo) if hi+lo else 0.0


def _ssb_image_db(iq, rate=RATE):
    spectrum=np.fft.fft(iq*np.hanning(len(iq)))
    f=np.fft.fftfreq(len(iq),1/rate)
    pos=float(np.sum(np.abs(spectrum[(f>=600)&(f<=1800)])**2))
    neg=float(np.sum(np.abs(spectrum[(f<=-600)&(f>=-1800)])**2))
    return 10*math.log10((pos+1e-20)/(neg+1e-20))


def _measure(mode, reference, rx, truth):
    result={}
    if mode=='cw':
        env=np.abs(rx)
        original=truth['envelope']
        active=original>.5
        observed=env>.5
        result['key_sample_accuracy']=float(np.mean(active==observed))
        result['key_up_rms']=float(np.sqrt(np.mean(env[~active & (original<.01)]**2)))
        result['max_envelope_error']=float(np.max(np.abs(env-original)))
        result['pass']=result['key_sample_accuracy']>=.99
    elif mode=='am':
        env=np.abs(rx)
        depth=_am_depth(env)
        target=1+.7*truth['audio']
        result['modulation_depth']=depth
        result['envelope_correlation']=float(np.corrcoef(env,target)[0,1])
        result['pass']=abs(depth-.7)<.045 and result['envelope_correlation']>.99
    elif mode in ('ssb','ssb-lsb'):
        result['image_rejection_db']=_ssb_image_db(rx) * (-1 if mode=='ssb-lsb' else 1)
        result['recovered_audio_correlation']=float(np.corrcoef(rx.real,truth['audio'])[0,1])
        result['pass']=result['image_rejection_db']>35 and result['recovered_audio_correlation']>.99
    elif mode=='qpsk':
        bits=truth['bits']
        decoded=qpsk.demodulate_bits(rx,len(bits)//2,sps=truth['sps'])
        ref_symbols=qpsk.bits_to_symbols(bits)
        actual_symbols=qpsk.demodulate_symbols(rx,len(bits)//2,sps=truth['sps'])
        result['bit_error_rate']=qpsk.bit_error_rate(bits,decoded)
        result['evm_pct']=qpsk.evm_rms(actual_symbols,ref_symbols)*100
        result['pass']=result['bit_error_rate']==0.0 and result['evm_pct']<17
    elif mode=='ofdm':
        bits=truth['bits']
        received=ofdm.ofdm_demodulate(rx)
        transmitted=ofdm.qpsk_modulate(bits)
        result['bit_error_rate']=float(np.mean(ofdm.qpsk_demodulate(received)!=bits))
        result['evm_pct']=ofdm.evm_rms(transmitted,received)*100
        result['papr_db']=ofdm.papr_db(reference)
        result['pass']=result['bit_error_rate']==0.0 and result['evm_pct']<28
    else:raise ValueError(mode)
    return result


def run_mode(mode, *, snr_db=60.0, tx_model='ideal', temp_dir=None, transmitted_iq=None):
    """Replay IQ via a real SigMFSource/SoapyFang RFBackend and inspect after FIR.

    Passing tests demonstrate modeled IQ conditioning and reference demod;
    only FM/FSK has a chip-native decoded audio/packet path in this model.
    """
    if mode not in MODES:raise ValueError(mode)
    if tx_model not in ('ideal','phase_only','offline_path_model'):
        raise ValueError('tx_model must be ideal, phase_only or offline_path_model')
    if tx_model=='offline_path_model' and transmitted_iq is None:
        raise ValueError('offline_path_model requires explicit IQ')
    if transmitted_iq is not None and tx_model!='offline_path_model':
        raise ValueError('Externally simulated IQ must be labeled offline_path_model')
    if not math.isfinite(snr_db) or not 0<=snr_db<=120:raise ValueError('snr_db must be finite 0..120')
    if temp_dir is None:
        with tempfile.TemporaryDirectory(prefix='soapyfang-modlab-') as d:
            return run_mode(mode,snr_db=snr_db,tx_model=tx_model,temp_dir=d,transmitted_iq=transmitted_iq)
    source,truth=fixture(mode)
    transmitted=source
    if tx_model=='phase_only':
        # Deliberate diagnostic of envelope loss, NOT an FD6818B TX model.
        # No claim the K6 can synthesize this continuous arbitrary phase.
        a=np.abs(source)
        transmitted=np.divide(source,a,out=np.zeros_like(source),where=a>1e-8)
    if transmitted_iq is not None:
        transmitted=np.asarray(transmitted_iq,dtype=np.complex128)
        if transmitted.shape != source.shape or not np.all(np.isfinite(transmitted)):
            raise ValueError('injected model IQ must be finite and match fixture length')
    power=float(np.mean(np.abs(transmitted)**2))
    rng=np.random.default_rng(100 + MODES.index(mode))
    std=math.sqrt(power*10**(-snr_db/10)/2)
    noise=std*(rng.standard_normal(len(source))+1j*rng.standard_normal(len(source)))
    iq=(transmitted+noise).astype(np.complex64)
    base=Path(temp_dir)/f'{mode}-iq'
    write_sigmf(base,iq,RATE,CENTER_HZ,description=f'Flux {mode}: synthetic IQ, no RF TX')
    backend=create_backend('sigmf:'+str(base))
    try:
        assert not backend.source.live and backend.status()['receive_only']
        backend.chip.tune(CENTER_HZ)
        observed=[]
        process=backend.chip.chan.process
        def iq_tap(x):
            y=process(x)
            observed.append(np.asarray(y,dtype=np.complex64).copy())
            return y
        backend.chip.chan.process=iq_tap
        n=CENTER_HZ//10
        regs={0x38:n&0xffff,0x39:n>>16}
        # SoapyFang intentionally skips old IQ on a single >1 s virtual
        # time jump. Advance in bounded 50-ms chunks to exercise every sample.
        raw=None
        rssi_samples=[]
        duration=len(iq)/RATE
        for tick in range(1,math.ceil(duration/.05)+1):
            seconds=min(duration,tick*.05)
            raw=backend.read(0x67,regs,seconds)
            rssi_samples.append(raw)
        if raw is None or not observed:
            raise RuntimeError('SoapyFang did not ingest the fixture')
        filtered=np.concatenate(observed)
        aligned,lag,mse=_iq_reference_alignment(source,filtered)
        metrics=_measure(mode,source,aligned,truth)
        metrics.update(dict(mode=mode,snr_db=snr_db,readback_rssi_raw=int(raw),
                            rssi_last_dbm=raw/2-174,
                            rssi_peak_dbm=max(rssi_samples)/2-174,
                            rf_model='SoapyFang ChipModel',
                            source='SigMF RX-only IQ replay',
                            received_samples=len(filtered),expected_samples=len(source),
                            alignment_lag_samples=lag,frontend_mse_ratio=mse,
                            fm_pcm_samples=int(len(backend.chip.pcm_after(0)[1])),
                            tx_model=tx_model,rf_retransmissions=0,physical_tx=False,
                            chip_native_decode=mode in ('fm','fsk')))
        metrics['pass']=bool(metrics['pass'] and len(filtered)==len(source)
                             and metrics['fm_pcm_samples']>0)
        return metrics
    finally:backend.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--modes',default=','.join(MODES))
    p.add_argument('--snr-db',type=float,default=60)
    p.add_argument('--tx-model',choices=('ideal','phase_only'),default='ideal',
                   help='ideal host IQ, or hypothetical constant-envelope phase-only conversion; never K6 TX')
    p.add_argument('--json',type=Path)
    args=p.parse_args(argv)
    modes=[m.strip().lower() for m in args.modes.split(',')]
    if not modes or any(m not in MODES for m in modes) or len(set(modes))!=len(modes):
        p.error('modes must be a unique comma-separated subset of '+','.join(MODES))
    results=[]
    with tempfile.TemporaryDirectory(prefix='soapyfang-modlab-') as d:
        for mode in modes:
            result=run_mode(mode,snr_db=args.snr_db,tx_model=args.tx_model,temp_dir=d)
            results.append(result)
            print(f'{mode.upper():5} {"PASS" if result["pass"] else "FAIL"} '
                  f'RSSI {result["rssi_peak_dbm"]:.1f} dBm, IQ {result["received_samples"]}, '
                  f'lag {result["alignment_lag_samples"]}, '
                  f'BER {result.get("bit_error_rate","n/a")}, '
                  f'EVM {result.get("evm_pct","n/a")}',flush=True)
    data=dict(kind='SoapyFang_RX_only_plus_host_reference_decoders',
              physical_tx=False,actual_radio_chip=False,tx_model=args.tx_model,
              caveat='Host ideal IQ transmit; SoapyFang receives/filters, reference decoders are not K6 firmware',
              results=results,pass_all=all(r['pass'] for r in results))
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(data,indent=2)+'\n')
    print('RESULT', 'PASS' if data['pass_all'] else 'FAIL')
    return 0 if data['pass_all'] else 1

if __name__=='__main__':raise SystemExit(main())
