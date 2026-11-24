#!/usr/bin/env python3
"""Offline register-paced 2/4-FSK → actual SoapyFang IQ front end → pilot RX.

Native-FSK FIFO clock and CPU REG51 writes are separately modeled. Sync and
pilot are the only decoding training data. Physical TX/flash is NEVER used.
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
from sim.k6emu.rf.frequency_modem import Settings,build,RATE,PATHS
from sim.k6emu.rf.sources import write_sigmf
from sim.k6emu.rf.burst_acquisition import acquire
from sim.k6emu.rf.backend import create_backend
from tools.k6_soapyfang_modlab import CENTER_HZ


def rx_soapy(iq, *, snr_db=30,seed=80):
    if not math.isfinite(snr_db) or not 0<=snr_db<=120:
        raise ValueError('snr_db must be finite and in 0..120')
    rng=np.random.default_rng(seed)
    sigma=(10**(-snr_db/20))/math.sqrt(2)
    x=(iq+sigma*(rng.standard_normal(len(iq))+1j*rng.standard_normal(len(iq))))
    with tempfile.TemporaryDirectory(prefix='flux-freq-modem-') as d:
        path=Path(d)/'fm'
        write_sigmf(path,np.asarray(x,dtype=np.complex64),RATE,CENTER_HZ,
                    description='host-only synthetic FM; no actual transmitter')
        backend=create_backend('sigmf:'+str(path))
        try:
            assert backend.status()['receive_only'] and not backend.source.live
            backend.chip.tune(CENTER_HZ)
            orig=backend.chip.chan.process
            taps=[]
            def observe(y):
                output=orig(y)
                taps.append(np.asarray(output,dtype=np.complex64).copy())
                return output
            backend.chip.chan.process=observe
            n=CENTER_HZ//10
            regs={0x38:n&65535,0x39:n>>16}
            duration=len(iq)/RATE
            for step in range(1,math.ceil(duration/.05)+1):
                backend.read(0x67,regs,min(step*.05,duration))
            # RFBackend batches read advances below 1 ms. The final partial
            # tail of an arbitrarily positioned burst must still be ingested;
            # otherwise sub-millisecond lead offsets spuriously lose samples.
            backend.chip.advance(duration)
            if not taps:raise RuntimeError('No IQ received by SoapyFang')
            filtered=np.concatenate(taps)
            if len(filtered)!=len(iq):
                raise RuntimeError(f'SoapyFang dropped IQ: {len(filtered)} vs {len(iq)}')
            return filtered
        finally:backend.close()


def fm_discriminator(iq,rate=RATE):
    x=np.asarray(iq,dtype=np.complex128)
    if x.ndim!=1 or len(x)<2 or not np.isfinite(x).all():
        raise ValueError('finite 1d complex IQ required')
    return np.r_[0.,np.angle(x[1:]*np.conj(x[:-1]))*rate/(2*math.pi)]


def decode_pilot(frame,iq):
    """Genie frame-start, pilot-calibrated clock/levels; payload never trains.

    Candidate sample phases and gain are selected using only fixed pilot
    symbols. The sync following pilot is tested but cannot improve its fit.
    No blind packet acquisition or CFO/timing tracker exists in this version.
    """
    rx=fm_discriminator(iq)
    syms=frame['source_levels']
    sps=RATE//frame['symbol_rate']
    pilot_n=frame['pilot_n'];sync_n=frame['sync_n']
    order=frame['order']
    pilot=syms[:pilot_n]
    best=None
    # Only sample the central ~40% of each symbol to reduce edge transients;
    # delay is learned entirely from public pilot, never held-out payload.
    half=max(1,int(sps*.18))
    centers=np.arange(len(syms))*sps +sps//2
    for lag in range(0,min(2*sps,120)):
        positions=centers+lag
        if positions[-1]+half >= len(rx):continue
        samples=np.array([np.mean(rx[p-half:p+half+1]) for p in positions])
        data=samples[:pilot_n]
        means=np.array([np.mean(data[pilot==i]) for i in range(order)])
        if max(means)-min(means)<20:continue
        estimate=means[pilot]
        score=float(np.mean((data-estimate)**2))/(max(means)-min(means))**2
        if best is None or score<best[0]:best=(score,lag,means,samples)
    if best is None:
        raise RuntimeError('Cannot acquire public pilot levels')
    score,lag,means,samples=best
    # Pilot-calibrated BER alone can hide reversed DAC polarity: the oracle
    # would relabel all four pilot means and still decode held-out bits.
    # Require the documented positive-level ordering as a *separate* gate.
    order_valid=bool(np.all(np.diff(means)>0))
    decisions=np.argmin(np.abs(samples[:,None]-means[None,:]),axis=1)
    payload_start=pilot_n+sync_n
    truth=syms[payload_start:]
    decoded=decisions[payload_start:]
    ser=float(np.mean(decoded!=truth))
    # For 4-FSK, compare two Gray-coded bits per symbol. SER != BER.
    if order==4:
        expected_gray=truth ^ (truth>>1)
        decoded_gray=decoded ^ (decoded>>1)
        xor=expected_gray ^ decoded_gray
        errors=int(np.count_nonzero(xor&1)+np.count_nonzero(xor&2))
        ber=errors/(2*len(truth))
    else:
        ber=ser
    return dict(payload_ber=float(ber),payload_symbol_error_rate=ser,
                payload_symbols=len(syms)-payload_start,
                payload_bits=(len(syms)-payload_start)*(2 if order==4 else 1),
                sync_errors=int(np.count_nonzero(decisions[pilot_n:payload_start]!=syms[pilot_n:payload_start])),
                pilot_errors=int(np.count_nonzero(decisions[:pilot_n]!=pilot)),
                pilot_eye_hz=float(np.min(np.diff(np.sort(means)))),
                level_order_valid=order_valid,
                level_means_hz=[round(float(v),2) for v in means],
                pilot_normalized_mse=float(score),
                recovered_delay_samples=int(lag),
                acquisition='pilot_frame_known_start_no_blind_sync')


def run_blind(settings,*,snr_db=30,payload_symbols=128,wrong_signed_order=False,
              lead_samples=1289,offset_hz=230,noise_seed=80):
    """Unknown burst start and carrier offset through SoapyFang RX FIR.

    Public pilot/sync are the *only* acquisition labels supplied to the
    blind-start algorithm. Expected payload is used after lock for scoring.
    """
    if not isinstance(lead_samples,int) or not 0<=lead_samples<=RATE:
        raise ValueError('leading silence must be 0..one sample-rate second')
    if not math.isfinite(offset_hz) or abs(offset_hz)>2000:
        raise ValueError('frequency offset must be finite and bounded')
    source=build(settings,payload_symbols=payload_symbols,
                 wrong_signed_order=wrong_signed_order)
    wave=np.r_[np.zeros(lead_samples,dtype=np.complex64),source['iq']]
    rotation=np.exp(2j*np.pi*offset_hz*np.arange(len(wave))/RATE)
    observed=rx_soapy(np.asarray(wave*rotation,dtype=np.complex64),
                      snr_db=snr_db,seed=noise_seed)
    pilot_n=source['pilot_n'];sync_n=source['sync_n']
    found=acquire(fm_discriminator(observed),baud=settings.baud,rate=RATE,
                  order=source['order'],
                  pilot=source['source_levels'][:pilot_n],
                  sync=source['source_levels'][pilot_n:pilot_n+sync_n],
                  payload_symbols=payload_symbols)
    if not found['locked']:
        return dict(path=settings.path,baud=settings.baud,
                    lock=False,pass_frame=False,lead_samples=lead_samples,
                    offset_hz=offset_hz,
                    physical_rf=False,hardware_qualified=False,
                    **{k:v for k,v in found.items() if k!='locked'})
    decision=found.pop('decisions')
    means=found.pop('estimated_levels_hz')
    truth=source['source_levels'][pilot_n+sync_n:]
    payload=decision[pilot_n+sync_n:]
    ser=float(np.mean(payload!=truth))
    if source['order']==4:
        xor=(payload^(payload>>1))^(truth^(truth>>1))
        ber=(np.count_nonzero(xor&1)+np.count_nonzero(xor&2))/(2*len(truth))
    else:
        ber=ser
    qualifies=bool(ser==0 and found['level_order_valid']
                   and source['missed_register_updates']==0)
    return dict(path=settings.path,baud=settings.baud,order=source['order'],
                lock=True,pass_frame=qualifies,lead_samples=lead_samples,
                offset_hz=offset_hz,snr_db=snr_db,
                recovered_start_sample=found['start_sample'],
                start_error_samples=int(found['start_sample']-lead_samples),
                payload_ber=float(ber),payload_symbol_error_rate=ser,
                payload_bits=len(truth)*(2 if source['order']==4 else 1),
                missed_register_updates=source['missed_register_updates'],
                level_means_hz=[float(x) for x in means],
                physical_rf=False,hardware_qualified=False,
                **{k:v for k,v in found.items() if k not in ('locked','start_sample')})


def run(settings,*,snr_db=30,payload_symbols=128,wrong_signed_order=False):
    source=build(settings,payload_symbols=payload_symbols,
                 wrong_signed_order=wrong_signed_order)
    observed=rx_soapy(source['iq'],snr_db=snr_db)
    demod=decode_pilot(source,observed)
    # A known pilot can learn a consistently reversed 4-FSK sign; that is
    # allowed for BER but cannot establish which DAC sign maps to which chip
    # register. Preserve measured means and the signed-order diagnostic.
    pass_frame=(demod['payload_ber']==0 and demod['payload_symbol_error_rate']==0
                and demod['sync_errors']==0
                and demod['level_order_valid'] and source['missed_register_updates']==0)
    return dict(path=settings.path,baud=settings.baud,
                order=source['order'],snr_db=snr_db,
                write_us=settings.write_us,analog_bandwidth_hz=settings.bandwidth_hz,
                wrong_signed_order=wrong_signed_order,
                samples=len(source['iq']),register_writes=len(source['writes']),
                missed_register_updates=source['missed_register_updates'],
                physical_rf=False,hardware_qualified=False,
                simulator='SoapyFang SigMF IQ→ChipModel channel FIR→independent FM pilot RX',
                **demod,pass_frame=pass_frame)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--path',choices=PATHS,default='native_fsk')
    p.add_argument('--baud',type=int)
    p.add_argument('--spi-write-us',type=float,default=117.)
    p.add_argument('--bandwidth-hz',type=float,default=0.)
    p.add_argument('--snr-db',type=float,default=30.)
    p.add_argument('--payload-symbols',type=int,default=128)
    p.add_argument('--wrong-signed-order',action='store_true')
    p.add_argument('--blind-start',action='store_true',
                   help='search unknown burst start using only public pilot and sync')
    p.add_argument('--lead-samples',type=int,default=1289)
    p.add_argument('--offset-hz',type=float,default=230.)
    p.add_argument('--json',type=Path)
    a=p.parse_args(argv)
    baud=a.baud or (4800 if a.path in ('native_fsk','reg40_per_frame') else 300)
    settings=Settings(path=a.path,baud=baud,write_us=a.spi_write_us,
                      bandwidth_hz=a.bandwidth_hz)
    try:
        if a.blind_start:
            r=run_blind(settings,snr_db=a.snr_db,payload_symbols=a.payload_symbols,
                        wrong_signed_order=a.wrong_signed_order,
                        lead_samples=a.lead_samples,offset_hz=a.offset_hz)
        else:
            r=run(settings,snr_db=a.snr_db,payload_symbols=a.payload_symbols,
                  wrong_signed_order=a.wrong_signed_order)
    except (ValueError,RuntimeError) as e:p.error(str(e))
    if a.json:
        a.json.parent.mkdir(parents=True,exist_ok=True)
        a.json.write_text(json.dumps(r,indent=2)+'\n')
    if a.blind_start:
        print(f'{r["path"]} {r["baud"]} baud blind-start: '
              f'{"PASS" if r["pass_frame"] else "FAIL"} '
              f'lock={r["lock"]}, BER={r.get("payload_ber","n/a")}, '
              f'start={r.get("recovered_start_sample","n/a")}, '
              f'offset_est={r.get("estimated_center_offset_hz","n/a")} Hz')
    else:
        print(f'{r["path"]} {r["baud"]} baud {r["order"]}-FSK: '
              f'{"PASS" if r["pass_frame"] else "FAIL"} BER={r["payload_ber"]:.4f}, '
              f'eye={r["pilot_eye_hz"]:.1f} Hz, '
              f'gap={r["missed_register_updates"]} writes, '
              f'RX pilot lag {r["recovered_delay_samples"]} samples')
    return 0 if r['pass_frame'] else 1

if __name__=='__main__':raise SystemExit(main())
