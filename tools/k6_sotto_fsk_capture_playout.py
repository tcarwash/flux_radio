#!/usr/bin/env python3
"""Replay independently SDR-decoded Sotto L packets into 40-ms playout slots.

Consumes the *blind* FSK sync/CRC receiver's JSON, not the TX manifest.
Physical packet times come from IQ demodulation. Playback deadlines use a
chosen jitter reserve and are an optimistic *offline* scheduler. Host codec
execution is separate from RF timing, no K6 microphone/codec proven.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import subprocess
import wave

from modem import pager

FRAME_S=.040
SLOTS=8
SOTTO_FRAME_BYTES=6
ON_AIR_PACKET_S=64*8/4800


def replay(report:dict, *,buffer_ms:int=160,frame_limit:int|None=None) -> dict:
    if buffer_ms<0 or (frame_limit is not None and frame_limit<=0):
        raise ValueError('Invalid playout configuration')
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packets=[]
    for record in report['packets']:
        if not record['crc_ok']:continue
        raw=bytes.fromhex(record['raw_payload_hex'])
        parsed=profile.unpack(raw)
        if parsed is None:raise ValueError('A reportedly CRC-valid packet fails recheck')
        packets.append(dict(packet=raw,msg_id=parsed['msg_id'],seq=parsed['seq'],
                            arrival_s=record['payload_start_s']+ON_AIR_PACKET_S))
    if not packets:raise ValueError('No CRC-good packets in blind receiver report')
    msg_id=packets[0]['msg_id']
    packets=[p for p in packets if p['msg_id']==msg_id]
    by_seq={}
    for item in packets:
        old=by_seq.get(item['seq'])
        if old is None or item['arrival_s']<old['arrival_s']:
            by_seq[item['seq']]=item
    if 0 not in by_seq:
        raise ValueError('Initial packet missing: no unambiguous playout anchor')
    if frame_limit is None:frame_limit=(1+max(by_seq))*SLOTS
    assembled=pager.Assembler(profile)
    for x in sorted(by_seq.values(),key=lambda x:x['seq']):
        assembled.push(x['packet'])
    compressed,good=assembled.frames(msg_id)
    if frame_limit>len(good):raise ValueError('Playout exceeds advertised clip size')
    playout_start=by_seq[0]['arrival_s']+buffer_ms/1000
    mask=[]
    for i in range(frame_limit):
        idx=i//SLOTS
        arrival=by_seq[idx]['arrival_s'] if idx in by_seq else float('inf')
        mask.append(bool(good[i]) and arrival<=playout_start+i*FRAME_S+1e-7)
    longest=0;run=0
    for ok in mask:
        if ok:run=0
        else:run+=1;longest=max(longest,run)
    stream=compressed[:SOTTO_FRAME_BYTES*frame_limit]
    return dict(message_id=msg_id,frame_limit=frame_limit,
         frame_s=FRAME_S,buffer_ms=buffer_ms,
         actual_first_packet_payload_start_s=round(by_seq[0]['arrival_s']-ON_AIR_PACKET_S,6),
         first_packet_reception_end_s=round(by_seq[0]['arrival_s'],6),
         first_playout_s=round(playout_start,6),
         ontime_codec_frames=sum(mask),concealed_codec_frames=len(mask)-sum(mask),
         longest_concealment_ms=longest*40,
         rf_packet_reception_end_s=[round(by_seq[k]['arrival_s'],6) for k in sorted(by_seq)],
         packet_seq_received=sorted(by_seq),
         encoded_bytes_sha256=hashlib.sha256(stream).hexdigest(),
         encoded_bytes=stream,
         playout_mask=bytes(mask))


def decode_wav(codec_bytes:bytes,good:bytes,*,decoder:Path,outbase:Path):
    if len(codec_bytes)!=len(good)*6:raise ValueError('Bad Sotto 1200 frame shape')
    src=outbase.with_suffix('.bit');src.write_bytes(codec_bytes)
    quality=outbase.with_suffix('.good');quality.write_bytes(good)
    pcm=outbase.with_suffix('.raw')
    subprocess.run([str(decoder),'-1200',str(src),str(pcm),str(quality)],check=True,timeout=60)
    out=outbase.with_suffix('.wav')
    with wave.open(str(out),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(8000)
        w.writeframes(pcm.read_bytes())
    return dict(wav_path=str(out),wav_sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
         wav_seconds=round(len(pcm.read_bytes())/16000,3),
         pcm_sha256=hashlib.sha256(pcm.read_bytes()).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--blind-report',required=True,type=Path)
    p.add_argument('--out-base',required=True,type=Path)
    p.add_argument('--buffer-ms',type=int,default=160)
    p.add_argument('--frames',type=int,default=None)
    p.add_argument('--decoder',type=Path)
    args=p.parse_args()
    report=json.loads(args.blind_report.read_text())
    result=replay(report,buffer_ms=args.buffer_ms,frame_limit=args.frames)
    compressed=result.pop('encoded_bytes');good=result.pop('playout_mask')
    paths=dict(bit_path=str(args.out_base.with_suffix('.bit')),
               good_mask_path=str(args.out_base.with_suffix('.good')))
    if args.decoder:
        paths=decode_wav(compressed,good,decoder=args.decoder,outbase=args.out_base)
    else:
        args.out_base.with_suffix('.bit').write_bytes(compressed)
        args.out_base.with_suffix('.good').write_bytes(good)
    result.update(paths)
    output=args.out_base.with_suffix('.json')
    if output.exists():raise FileExistsError('Refuse to overwrite old playout report')
    output.write_text(json.dumps(result,indent=2)+'\n')
    print('Playout:',result['ontime_codec_frames'],'of',result['frame_limit'],'Sotto frames on time;',
          'longest concealment',result['longest_concealment_ms'],'ms; file',paths)


if __name__=='__main__':main()
