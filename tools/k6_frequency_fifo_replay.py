#!/usr/bin/env python3
"""Offline synthetic native-FSK burst -> SoapyFang -> FD6818B emulated FIFO RX.

Exercises actual simulated register semantics/IRQ/FIFO, not only a reference
IQ discriminator. Never creates a SoapySDR TX stream or touches radio hardware.
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
from sim.k6emu.rf.fsk import SYNC,FULL,DONE

RATE=240_000
CENTER=440_850_000
MARKER=b'\x55'*16 + bytes.fromhex('076b3e36')


def native_burst(payload,*, rate=RATE,lead_samples=5231,offset_hz=230.,
                 deviation_hz=1800.,polarity=1,snr_db=25,seed=42,
                 marker=MARKER,tx_amplitude=.15,flip_payload_bit=None):
    """Modulated known payload with an UNKNOWN-to-receiver arbitrary lead.

    This intentionally has no TX register/PA model. RX must find preamble+sync
    on filtered IQ, and cannot be given the starting sample or payload bits.
    """
    if (not isinstance(payload,(bytes,bytearray)) or len(payload)%2 or not 2<=len(payload)<=254
            or rate%4800 or rate<48000 or lead_samples<0 or lead_samples>rate
            or polarity not in (-1,1) or not math.isfinite(offset_hz)
            or abs(offset_hz)>2000 or not math.isfinite(snr_db)
            or not 0<=snr_db<=120):
        raise ValueError('unsupported native-FSK synthetic frame options')
    if not isinstance(marker,bytes) or len(marker)!=20:
        raise ValueError('marker must contain 16 preamble + four sync bytes')
    if not math.isfinite(tx_amplitude) or not 0<=tx_amplitude<=.15:
        raise ValueError('invalid virtual TX amplitude')
    bits=np.unpackbits(np.frombuffer(marker+payload,dtype=np.uint8)).copy()
    if flip_payload_bit is not None:
        if not isinstance(flip_payload_bit,int) or not 0<=flip_payload_bit<len(payload)*8:
            raise ValueError('invalid payload bit fault index')
        bits[len(marker)*8+flip_payload_bit]^=1
    sps=rate//4800
    guard=int(.020*rate)
    n=lead_samples+len(bits)*sps+guard
    # Even in the unkeyed lead/guard, background noise and RF amplitude are
    # present; no caller-provided frame timestamp is passed to FskRx.
    frequency=np.full(n,offset_hz,dtype=float)
    frequency[lead_samples:lead_samples+len(bits)*sps] += (
        np.repeat(bits.astype(float)*2-1,sps)*deviation_hz*polarity)
    phase=np.cumsum(2*np.pi*frequency/rate)
    amplitude=np.zeros(n)
    amplitude[lead_samples:lead_samples+len(bits)*sps]=tx_amplitude
    rng=np.random.default_rng(seed)
    sigma=.15*10**(-snr_db/20)/math.sqrt(2)
    samples=(amplitude*np.exp(1j*phase)
             +sigma*(rng.standard_normal(n)+1j*rng.standard_normal(n)))
    return np.asarray(samples,dtype=np.complex64)


def _configure(backend,regs,nbytes,*,threshold=4):
    if nbytes%2 or not 2<=nbytes<=254 or not 1<=threshold<=7:
        raise ValueError('FIFO payload must be even, threshold 1..7')
    def put(addr,val):
        backend.write(addr,val,regs,0)
        regs[addr]=val
    for addr,val in ((0x58,0xe1),(0x72,0xc194),(0x5a,0x076b),
                     (0x5b,0x3e36),(0x5c,0x5625),(0x5d,(nbytes-1)<<8),
                     (0x5e,0x200|threshold),(0x59,0x40f8),
                     (0x59,0x10f8),(0x02,0),(0x3f,SYNC|FULL|DONE)):
        put(addr,val)


def replay(payload,*,rate=RATE,lead_samples=5231,offset_hz=230.,
           polarity=1,snr_db=25,poll_ms=1.,seed=42,threshold=4,
           marker=MARKER,tx_amplitude=.15,flip_payload_bit=None):
    if not math.isfinite(poll_ms) or not .1<=poll_ms<=50:
        raise ValueError('poll_ms out of range')
    signal=native_burst(payload,rate=rate,lead_samples=lead_samples,
                        offset_hz=offset_hz,polarity=polarity,snr_db=snr_db,seed=seed,
                        marker=marker,tx_amplitude=tx_amplitude,
                        flip_payload_bit=flip_payload_bit)
    with tempfile.TemporaryDirectory(prefix='flux-native-fifo-') as d:
        path=Path(d)/'burst'
        write_sigmf(path,signal,rate,CENTER,description='Synthetic offline FSK; TX disabled')
        backend=create_backend('sigmf:'+str(path))
        try:
            assert backend.status()['receive_only'] and not backend.source.live
            tune=CENTER//10
            regs={0x38:tune&0xffff,0x39:tune>>16}
            _configure(backend,regs,len(payload),threshold=threshold)
            events=[]
            data=bytearray()
            duration=len(signal)/rate
            step=poll_ms*.001
            t=step
            # Keep chunks small enough for interrupt/FIFO service; do not jump
            # over SoapyFang's 1s recording window or poll once at EOF.
            while t <= duration+step*.6:
                now=min(t,duration)
                if backend.read(0x0c,regs,now)&1:
                    backend.write(0x02,0,regs,now)
                    flags=backend.read(0x02,regs,now)
                    events.append((round(now,6),int(flags)))
                    if flags&FULL:
                        for _ in range(threshold):
                            data.extend(backend.read(0x5f,regs,now).to_bytes(2,'little'))
                    if flags&DONE:
                        while len(data)<len(payload):
                            data.extend(backend.read(0x5f,regs,now).to_bytes(2,'little'))
                t+=step
            state=backend.chip.fsk.status()
            # Avoid any oracle-assisted payload decoding. The only payload
            # comparison occurs once register-read FIFO words are recovered.
            exact=(bytes(data)==bytes(payload) and state['frames']==1
                   and not state['overruns'] and not state['underflows']
                   and not state['gaps'] and any(f&DONE for _,f in events)
                   and any(f&SYNC for _,f in events))
            return dict(kind='synthetic_native_fsk_to_SoapyFang_register_FIFO',
                        rf_hardware=False,firmware_tx=False,pass_exact=exact,
                        configured_payload_len=len(payload),
                        recovered_payload_hex=bytes(data).hex(),
                        recovered_bytes=len(data),
                        frames=state['frames'],overruns=state['overruns'],
                        underflows=state['underflows'],gaps=state['gaps'],
                        status=state['unsupported'],sync_events=sum(bool(f&SYNC) for _,f in events),
                        done_events=sum(bool(f&DONE) for _,f in events),
                        full_events=sum(bool(f&FULL) for _,f in events),
                        events=events,rate=rate,lead_samples=lead_samples,
                        offset_hz=offset_hz,polarity=polarity,snr_db=snr_db,
                        poll_ms=poll_ms)
        finally:backend.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lead-samples',type=int,default=5231)
    p.add_argument('--offset-hz',type=float,default=230)
    p.add_argument('--polarity',type=int,choices=(-1,1),default=1)
    p.add_argument('--snr-db',type=float,default=25)
    p.add_argument('--poll-ms',type=float,default=1.)
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--payload-bytes',type=int,default=64)
    p.add_argument('--json',type=Path)
    a=p.parse_args(argv)
    if a.payload_bytes%2 or not 2<=a.payload_bytes<=254:p.error('payload must be even 2..254 bytes')
    payload=np.random.default_rng(96).integers(0,256,a.payload_bytes,dtype=np.uint8).tobytes()
    try:
        result=replay(payload,lead_samples=a.lead_samples,offset_hz=a.offset_hz,
                      polarity=a.polarity,snr_db=a.snr_db,poll_ms=a.poll_ms,seed=a.seed)
    except (ValueError,RuntimeError) as e:p.error(str(e))
    if a.json:
        a.json.parent.mkdir(parents=True,exist_ok=True)
        a.json.write_text(json.dumps(result,indent=2)+'\n')
    print(f'FIFO {"PASS" if result["pass_exact"] else "FAIL"}: '
          f'{result["recovered_bytes"]}/{len(payload)} bytes, '
          f'{result["frames"]} frame(s), overruns={result["overruns"]}, '
          f'sync={result["sync_events"]}, done={result["done_events"]}')
    return 0 if result['pass_exact'] else 1

if __name__=='__main__':raise SystemExit(main())
