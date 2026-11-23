#!/usr/bin/env python3
"""FSK-12 microphone-derived four-packet IQ qualification; OFFLINE ONLY.

Receiver demodulates ONLY using the public modem 16x0x55 preamble + 076b3e36
sync, nominal 4800-bps clock, and Flux L64 CRC checking. It does NOT receive
an expected packet, message id, sequence or voice sample when acquiring bits.
After the CRC-validated receiver output is frozen, compare public hashes of
these actual received bytes and compressed frames. Writes optional WAV only
in designated PRIVATE bench folder. NEVER opens a radio/serial/SDR device.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import pager
from tools.k6_fsk_iq_edges import read_iq
from tools.k6_fsk_sotto_sync_rx import find_frames
from tools.k6_sotto_fsk_capture_playout import decode_wav

MANIFEST=Path(__file__).resolve().parents[1]/'tests/data/k6/fsk12_k6mic_public_fingerprint.json'


def sha(data:bytes)->str:return hashlib.sha256(data).hexdigest()


def score_iq(iq,manifest:dict):
    rf=find_frames(iq,rate_factors=(1.,),min_correlation=.79)
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    good=[]
    for v in rf['accepted']:
        packet=bytes.fromhex(v['raw_payload_hex'])
        info=p.unpack(packet)
        if info is None:raise ValueError('Accepted CRC but failed own independent CRC parse')
        good.append((info,packet))
    ordered=sorted(good,key=lambda x:x[0]['seq'])
    firsts=[x[0]['seq'] for x in ordered]
    # Post-hoc scoring only: frozen hashes/expected message id are never
    # consulted by the RF detector, FSK thresholding, CRC or word decoding.
    packet_hashes=[sha(x[1]) for x in ordered]
    packet_hash_match=[a==b for a,b in zip(packet_hashes,manifest['per_packet_sha256'])]
    strict=(len(ordered)==4 and firsts==[0,1,2,3] and
            all(q['msg_id']==manifest['message_id'] and q['total']==26
                for q,_ in ordered) and
            len(packet_hash_match)==4 and all(packet_hash_match))
    assembler=pager.Assembler(p)
    for _,raw in ordered:assembler.push(raw)
    recovered=b'';frame_quality=b''
    if ordered:
        compressed,valid=assembler.frames(ordered[0][0]['msg_id'])
        recovered=bytes(compressed)
        frame_quality=bytes(valid)
    good_compressed=strict and sha(recovered)==manifest['mic_sotto_156_byte_compressed_sha256']
    result=dict(experiment='FSK-12 archived SDR I/Q => blind fixed-clock Sotto 1200 microphone packets',
                result='PASS' if good_compressed else 'FAIL_OR_INCONCLUSIVE',
                programmed_baud=4800,
                uses_only_public_hardware_preamble_sync_for_acquisition=True,
                uses_expected_payload_for_acquisition=False,
                packet_profile='Flux L64 with CRC16',
                independent_syncs=rf['frames_found'],
                crc_valid_frames=rf['crc_valid'],
                packet_sequences=firsts,
                observed_packet_sha256=packet_hashes,
                posthoc_packet_hash_matches=packet_hash_match,
                source_message_id=ordered[0][0]['msg_id'] if ordered else None,
                valid_voice_superframes=sum(frame_quality),
                voice_superframes_recovered=len(frame_quality),
                compressed_audio_sha256=sha(recovered),
                compressed_hash_matches=good_compressed,
                packet_payload_start_times_s=[v['payload_start_s'] for v in rf['accepted']],
                no_hardware_access=True,
                limitations=['Programmed fixed baud, known public framing, controlled RF fixture',
                             'No live K6-encoder speed or continuous FIFO streaming demonstrated'])
    return result,recovered,frame_quality


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',required=True,type=Path)
    p.add_argument('--out-base',required=True,type=Path,help='PRIVATE bench directory prefix')
    p.add_argument('--decoder',required=True,type=Path)
    p.add_argument('--duration',type=float,default=3.0)
    a=p.parse_args()
    paths=[a.out_base.with_suffix(ext) for ext in ('.json','.bit','.wav','.good','.raw')]
    if any(f.exists() for f in paths):p.error('Refusing to overwrite previous evidence')
    if not a.out_base.parent.is_dir():p.error('Output bench directory missing')
    manifest=json.loads(MANIFEST.read_text())
    iq=read_iq(a.iq,duration=a.duration,offset=280500.)
    result,compressed,good=score_iq(iq,manifest)
    if result['result']=='PASS':
        result.update(decode_wav(compressed,good,decoder=a.decoder,outbase=a.out_base))
    result['raw_iq_sha256']=sha(a.iq.read_bytes())
    a.out_base.with_suffix('.json').write_text(json.dumps(result,indent=2)+'\n')
    print(result['result'],result['crc_valid_frames'],'CRC packets;',result['valid_voice_superframes'],'valid Sotto frames')

if __name__=='__main__':main()
