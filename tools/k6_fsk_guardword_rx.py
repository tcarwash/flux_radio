#!/usr/bin/env python3
"""Blind, guard-word-tolerant Flux L64 Sotto receiver for a 66-byte FSK frame.

Experimental finite-packet mitigation for FD6818B's intermittent unexpected
0x00F8 leading word. Both potential L64 windows are validated by their own
whitened packet CRC; no expected voice bytes or known TX payload are used.

A valid packet starting at offset 0 recovers the normal 32-word payload;
at offset 2 it recovers the packet after one unexpected leading word.
Ambiguous/no-CRC cases must NOT be accepted.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))

from modem import pager
from k6_fsk_iq_edges import read_iq
from k6_fsk_sotto_sync_rx import find_frames

PHYSICAL_BYTES=66
PACKET_BYTES=64
GUARD_WORD=0xA55A


def select_l64(payload: bytes) -> dict:
    """Return a CRC-verified L64 candidate or explicitly reject ambiguity."""
    if len(payload)!=PHYSICAL_BYTES:
        raise ValueError('expected exactly 66 RF data bytes')
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    candidates=[]
    for offset in (0,2):
        data=payload[offset:offset+PACKET_BYTES]
        packet=p.unpack(data)
        if packet is not None:
            candidates.append((offset,data,packet))
    if len(candidates)!=1:
        return dict(valid=False, ambiguous=len(candidates)>1,
                    candidate_count=len(candidates),
                    physical_first_word_le=payload[:2].hex())
    offset,data,packet=candidates[0]
    return dict(valid=True, ambiguous=False, candidate_count=1,
                offset_bytes=offset, displaced_prefix=offset==2,
                physical_first_word_le=payload[:2].hex(),
                packet_hex=data.hex(),
                packet_sha256=hashlib.sha256(data).hexdigest(),
                msg_id=packet['msg_id'],seq=packet['seq'],
                codec_frames=len(packet['frames']))


def receive(iq) -> dict:
    # Entire physical 66-byte frame, fixed programmed nominal 4800-baud.
    # Sync acquisition is based exclusively on the modem's public header.
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    def unpack_guard(frame):
        decision=select_l64(frame)
        return profile.unpack(bytes.fromhex(decision['packet_hex'])) if decision['valid'] else None
    # Use CRC only to choose among independently acquired public-sync
    # sampling-phase candidates; never use known TX packet bytes.
    candidates=find_frames(iq,payload_bytes=PHYSICAL_BYTES,
                           rate_factors=(1.,),min_correlation=.95,
                           unpack_packet=unpack_guard)
    frames=[]
    for c in candidates['packets']:
        payload=bytes.fromhex(c['raw_payload_hex'])
        decision=select_l64(payload)
        frames.append(dict(payload_start_s=c['payload_start_s'],
                           sync_corr=c['sync_correlation'],
                           sync_errors=c['sync_bit_errors'],
                           selected_baud=c['baud_estimate'],
                           **decision))
    return dict(experiment='FSK L64 + 16-bit FIFO guard word',
                acquisition='public 16x55 plus 076b3e36, fixed 4800 baud',
                rf_payload_bytes=PHYSICAL_BYTES,packet_bytes=PACKET_BYTES,
                frames_detected=candidates['frames_found'],
                crc_valid=sum(bool(f['valid']) for f in frames),
                crc_ambiguous=sum(bool(f['ambiguous']) for f in frames),
                frames=frames,
                limitations=['Offline decoder only, not a demonstrated RF success.',
                             'CRC-checked offset selection, not raw leading-word removal.',
                             'No sustained FIFO refill, loss recovery, or live audio demonstrated.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--duration',type=float,default=2.4)
    a=p.parse_args()
    if a.out.exists():p.error('refuse overwrite')
    with a.iq.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    result=receive(read_iq(a.iq,duration=a.duration,offset=280500.))
    result['capture']=str(a.iq)
    result['iq_sha256']=digest
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print('FSK guard frames',result['frames_detected'],'CRC valid',result['crc_valid'],
          'ambiguous',result['crc_ambiguous'])


if __name__=='__main__':main()
