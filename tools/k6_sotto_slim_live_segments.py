#!/usr/bin/env python3
"""OFFLINE model of incremental 40ms slim Sotto frames -> self-contained L64 chunks.

Each 8-frame (320ms) voice chunk is encoded into its OWN single valid Flux
L64 packet with a fresh message ID. That avoids requiring the total length of
an as-yet unfinished PTT call in the first L64 header, without changing the
existing pager.py 64-byte wire format. A short final chunk is padded by the
current packer but carries only its actual frame count. No real-time K6 CPU,
TX/RX timing or hardware access; uses measured prior finite-FSK timing as an
OPTIMISTIC airtime model, ignoring any extra PA key-up latency.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import pager

FRAME_BYTES=6
FRAME_S=.04
FRAMES_PER_SEGMENT=8
PACKET_S=64*8/4800+.044375


def build_segments(compressed:bytes,times_s:list[float],*,start_message_id=112,
                   playout_buffer_ms=160):
    if not compressed or len(compressed)%FRAME_BYTES:raise ValueError('Incomplete compressed superframes')
    n=len(compressed)//FRAME_BYTES
    if len(times_s)!=n or not all(0<=x for x in times_s):raise ValueError('Missing voice frame release times')
    if any(times_s[i]<times_s[i-1] for i in range(1,len(times_s))):
        raise ValueError('Voice release time must be nondecreasing')
    if not 0<=start_message_id<=255:raise ValueError('Invalid starting message ID')
    count=(n+FRAMES_PER_SEGMENT-1)//FRAMES_PER_SEGMENT
    if count>256-start_message_id:raise ValueError('Message IDs would wrap; explicit session/epoch is required')
    if playout_buffer_ms<0:raise ValueError('Negative jitter buffer')
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    wire=[];chunks=[];receiver=b'';tx_available=0.
    for segment_index in range(count):
        start=segment_index*FRAMES_PER_SEGMENT
        end=min(start+FRAMES_PER_SEGMENT,n)
        msgid=start_message_id+segment_index
        voice=compressed[start*FRAME_BYTES:end*FRAME_BYTES]
        packets=profile.pack(voice,msg_id=msgid)
        if len(packets)!=1:raise ValueError('Expected one 64-byte packet per segment')
        packet=packets[0]
        if len(packet)!=64:raise ValueError('Incorrect packet length')
        parsed=profile.unpack(packet)
        if not parsed or parsed['seq']!=0 or parsed['total']!=end-start or parsed['msg_id']!=msgid:
            raise ValueError('Segment CRC/header verification failed')
        assembler=pager.Assembler(profile)
        assembler.push(packet)
        frames,good=assembler.frames(msgid)
        if not good.all() or frames!=voice:raise ValueError('Chunk decode is not exact')
        receiver+=frames
        ready_s=times_s[end-1]
        start_tx=max(ready_s,tx_available)
        end_tx=start_tx+PACKET_S
        tx_available=end_tx
        wire.append(packet)
        chunks.append(dict(chunk_index=segment_index,message_id=msgid,
             source_first_superframe=start,source_frame_count=end-start,
             payload_crc16_valid=True,packet_hash_sha256=hashlib.sha256(packet).hexdigest(),
             encoder_chunk_ready_s=round(ready_s,4),
             modeled_tx_start_s=round(start_tx,4),
             modeled_packet_delivery_s=round(end_tx,4),
             modeled_tx_queue_wait_s=round(start_tx-ready_s,4),
             modeled_packet_duration_s=round(PACKET_S,6)))
    if receiver!=compressed:raise ValueError('Whole talk segment reassembly mismatch')
    first_playout=chunks[0]['modeled_packet_delivery_s']+playout_buffer_ms/1000
    ontime=0;late=0
    for segment in chunks:
        for j in range(segment['source_first_superframe'],segment['source_first_superframe']+segment['source_frame_count']):
            deadline=first_playout+j*FRAME_S
            if segment['modeled_packet_delivery_s']<=deadline+1e-7:ontime+=1
            else:late+=1
    report=dict(experiment='incremental Sotto slim -> one-per-320ms Flux L64 talk segments',
        offline_model_only=True,no_hardware_access=True,
        codec_frames=n,source_duration_s=n*FRAME_S,
        chunks=len(chunks),new_message_per_segment=True,source_total_not_in_first_header=True,
        packet_duration_optimistic_s=round(PACKET_S,6),
        packets_sha256=[hashlib.sha256(p).hexdigest() for p in wire],
        compression_sha256=hashlib.sha256(compressed).hexdigest(),
        concatenated_packet_sha256=hashlib.sha256(b''.join(wire)).hexdigest(),
        reassembled_compressed_exact=True,
        receiver_frame_count=len(receiver)//FRAME_BYTES,
        playout_buffer_ms=playout_buffer_ms,
        first_playout_s=round(first_playout,4),
        ontime_frames=ontime,late_frames=late,
        max_tx_queue_wait_s=round(max(c['modeled_tx_queue_wait_s'] for c in chunks),4),
        chunk_data=chunks,
        limits=['Segments preserve current L64 header/CRC but require rising msg_id for order.',
                'No end-of-PTT indication when final chunk contains exactly eight frames.',
                'Model ignores fresh RF key-up, PTT guard intervals and real packet errors.',
                'Real-time K6 8kHz PA11 sample collection + Sotto encoding + SPI contention not measured.',
                'Only a perfect ideal receiver and measured former finite-packet restart are modeled.',
                'Message ID wrap / lost packets / two-way sequencing need protocol design.'])
    return wire,report


def times_from_csv(timeline:Path,expected_frames:int):
    with timeline.open(newline='') as fd:rows=list(csv.DictReader(fd))
    if len(rows)!=expected_frames:raise ValueError('Timeline has wrong number of frame events')
    if [int(x['superframe_index']) for x in rows]!=list(range(expected_frames)):
        raise ValueError('Out of order Sotto stream events')
    return [int(x['10ms_blocks_received'])*.01 for x in rows]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bits',required=True,type=Path)
    p.add_argument('--timeline',required=True,type=Path)
    p.add_argument('--out-dir',required=True,type=Path)
    p.add_argument('--first-msg-id',type=int,default=112)
    p.add_argument('--buffer-ms',type=int,default=160)
    a=p.parse_args()
    raw=a.bits.read_bytes()
    released=times_from_csv(a.timeline,len(raw)//FRAME_BYTES)
    packets,result=build_segments(raw,released,start_message_id=a.first_msg_id,
                                   playout_buffer_ms=a.buffer_ms)
    a.out_dir.mkdir(mode=0o700,parents=False,exist_ok=False)
    # Contains encoded voice bytes; never commit this private folder to Git.
    (a.out_dir/'segmented-slim-l64-packets.bin').write_bytes(b''.join(packets))
    (a.out_dir/'segmented-slim-l64-report.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Simulated',result['codec_frames'],'Sotto frames ->',result['chunks'],
          'L64 self-contained chunks; ideal ontime',result['ontime_frames'],
          'late',result['late_frames'],'first playout',result['first_playout_s'],'s; NO RF')

if __name__=='__main__':main()
