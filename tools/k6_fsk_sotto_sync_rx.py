#!/usr/bin/env python3
"""Offline *payload-independent* Flux/K6 native-FSK framing receiver.

Acquires frame timing/FSK sign from the fixed 16x0x55 preamble + 0x076b3e36
hardware sync bits, not from a transmitted packet payload, manifest, or known
voice data. Demodulates 64 data bytes and accepts only a valid Flux L packet
CRC/whitening/header. All payload bytes come from RF symbol decisions.

This is an offline SDR I/Q decoder, not a radio, and does not attempt any TX.
Blind means unknown payload and frame times, NOT unknown PHY parameters:
4800-bps bitrate, FSK sign convention, 64-byte frame size, and the public
preamble/sync are configured. False-lock/weak-SNR tolerance are unproven.
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
from scipy.signal import butter, find_peaks, fftconvolve, sosfiltfilt
from scipy.ndimage import uniform_filter1d
from modem import pager
from k6_fsk_iq_edges import read_iq,FS

BAUD=4800
PAYLOAD_BYTES=64
SYNC=pager.SYNC
PREAMBLE=bytes([pager.PREAMBLE_BYTE])*16
FRAME_MARK=PREAMBLE+SYNC
MARK_BITS=np.unpackbits(np.frombuffer(FRAME_MARK,dtype=np.uint8))
MARK_LEN=len(MARK_BITS)
PAYLOAD_BITS=PAYLOAD_BYTES*8
assert MARK_LEN==160


def _match(stream, marker):
    """Normalized signed correlation of physical frequency against sync bits."""
    template=marker.astype(np.float64)*2-1
    template-=template.mean()
    denom=float(template@template)
    x=np.asarray(stream,dtype=np.float64)
    product=fftconvolve(x,template[::-1],mode='valid')
    c=np.concatenate(([0.],np.cumsum(x)))
    c2=np.concatenate(([0.],np.cumsum(x*x)))
    n=len(template)
    v=np.maximum(c2[n:]-c2[:-n]-(c[n:]-c[:-n])**2/n,1e-9)
    return product/np.sqrt(v*denom)


def find_frames(iq, *, fs=FS, baud=BAUD,
                rate_factors=(.998,1.,1.002),
                sampling_phases=(.125,.375,.625,.875),
                min_correlation=.79,
                min_separation_hz=550,
                max_frame_candidates=300, payload_bytes=PAYLOAD_BYTES,
                unpack_packet=None):
    """List CRC-accepted packets found by searching *only* universal framing.

    Candidate matching and payload extraction use no expected voice bytes, no
    reference manifest, no previous packet start time. CRC is independent
    validation of the symbol-decoded payload, not a synthetic packet source.
    """
    arr=np.asarray(iq)
    if arr.ndim!=1 or arr.size<fs*.16:
        raise ValueError('Missing or too-short I/Q data')
    filt=sosfiltfilt(butter(3,8000,fs=fs,output='sos'),arr)
    phase=np.angle(filt[1:]*np.conj(filt[:-1]))*fs/(2*np.pi)
    freq=uniform_filter1d(phase,size=max(3,round(.16*fs/baud)))
    grid=np.arange(len(freq),dtype=np.float64)
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    if not isinstance(payload_bytes,int) or not 1 <= payload_bytes <= 256:
        raise ValueError('invalid payload_bytes')
    payload_bits=payload_bytes*8
    candidates=[]
    for factor in rate_factors:
        rate=baud*float(factor)
        samples_per_bit=fs/rate
        for frac in sampling_phases:
            positions=(np.arange(int(len(freq)/samples_per_bit),dtype=np.float64)+frac)*samples_per_bit
            observations=np.interp(positions,grid,freq)
            if len(observations)<MARK_LEN+payload_bits:
                continue
            correlation=_match(observations,MARK_BITS)
            # Keep room for every bit of data. Max abs handles discriminator
            # polarity reversals without relying on known payload data.
            max_start=len(observations)-MARK_LEN-payload_bits
            peaks,_=find_peaks(np.abs(correlation[:max_start+1]),
                               height=min_correlation,distance=MARK_LEN//2)
            for k in peaks:
                value=float(correlation[k]);abs_score=abs(value)
                t_marker=(k+frac)/rate
                prefix=observations[k:k+MARK_LEN]
                zero=prefix[MARK_BITS==0];one=prefix[MARK_BITS==1]
                lower=float(np.median(zero));upper=float(np.median(one))
                separation=abs(upper-lower)
                if separation<min_separation_hz:continue
                pol=1 if upper>lower else -1
                threshold=(lower+upper)/2
                framed=(prefix-threshold)*pol>0
                marker_errors=int(np.count_nonzero(framed!=MARK_BITS))
                if marker_errors>12:continue
                d=observations[k+MARK_LEN:k+MARK_LEN+payload_bits]
                bits=((d-threshold)*pol>0).astype(np.uint8)
                decoded=np.packbits(bits).tobytes()
                packet=(unpack_packet(decoded) if unpack_packet is not None else
                        p.unpack(decoded) if payload_bytes==PAYLOAD_BYTES else None)
                candidates.append({
                    'marker_start_s':round(t_marker,7),
                    'payload_start_s':round(t_marker+MARK_LEN/rate,7),
                    'baud_estimate':round(rate,3),
                    'sampling_phase':float(frac),
                    'sync_correlation':round(abs_score,6),
                    'sync_bit_errors':marker_errors,
                    'frequency_separation_hz':round(separation,1),
                    'raw_payload_hex':decoded.hex(),
                    'crc_ok':packet is not None,
                    'msg_id':packet['msg_id'] if packet else None,
                    'seq':packet['seq'] if packet else None,
                    'voice_frames':len(packet['frames']) if packet else 0,
                })
    candidates.sort(key=lambda x:(x['payload_start_s'],-x['sync_correlation']))
    # Time-based deduplication; prefer a CRC-valid candidate over one slightly
    # better correlated sync clock, but never return two for one physical frame.
    groups=[]
    for c in candidates:
        if groups and abs(c['payload_start_s']-groups[-1][-1]['payload_start_s'])<.015:
            groups[-1].append(c)
        else:
            groups.append([c])
    selected=[]
    for group in groups:
        group.sort(key=lambda x:(not x['crc_ok'],x['sync_bit_errors'],-x['sync_correlation']))
        selected.append(group[0])
    valid=[c for c in selected if c['crc_ok']]
    return dict(frames_found=len(selected),crc_valid=len(valid),
                candidates_considered=len(candidates),
                packets=selected[:max_frame_candidates],
                accepted=[c for c in valid],
                byte_payloads=[bytes.fromhex(c['raw_payload_hex']) for c in valid])


def analyze_file(path:Path, *,duration=1.8):
    with path.open('rb') as fd: digest=hashlib.file_digest(fd,'sha256').hexdigest()
    iq=read_iq(path,duration=duration,offset=280500.)
    raw=find_frames(iq)
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    assembler=pager.Assembler(profile)
    for packet in raw['byte_payloads']:assembler.push(packet)
    recovered={}
    for msg_id in assembler.messages:
        bits,good=assembler.frames(msg_id)
        recovered[str(msg_id)]=dict(codec_frames_good=int(good.sum()),
                                    codec_frames_total=len(good),
                                    voice_bytes_sha256=hashlib.sha256(bits).hexdigest(),
                                    codec_bytes_hex=bits.hex())
    return dict(experiment='FSK L64 blind sync and CRC16 payload-independent SDR decode',
                capture_path=str(path),capture_sha256=digest,
                knows_payload_truth=False,uses_known_phy_preamble_and_sync=True,
                requires_known_frame_start=False,requires_known_message_id=False,
                baud_nominal=BAUD,payload_bytes_per_frame=PAYLOAD_BYTES,
                preamble_hex=PREAMBLE.hex(),sync_hex=SYNC.hex(),
                frames_detected=raw['frames_found'],crc_valid=raw['crc_valid'],
                candidates_considered=raw['candidates_considered'],
                packets=raw['packets'],reconstructed_messages=recovered,
                limitations=['Offline fixed-FSK radio and 64-byte packet profile only.',
                             'Known 16x55 preamble + public sync, not learned from Sotto payload.',
                             'CRC alone cannot prove robust long-run BER or false-alarm rate.',
                             'No live audio, autonomous radio RX or chip-level FSK streaming demonstrated.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--duration',type=float,default=1.8)
    args=p.parse_args()
    if args.out.exists():p.error('Refusing to overwrite analysis')
    result=analyze_file(args.iq,duration=args.duration)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print('Blind framing:',result['frames_detected'],'candidates,',result['crc_valid'],'CRC-valid frames')
    for c in result['packets']:
        print('  t=',c['payload_start_s'],'s  corr=',c['sync_correlation'],
              'CRC',c['crc_ok'],'ID',c['msg_id'],'seq',c['seq'])


if __name__=='__main__':main()
