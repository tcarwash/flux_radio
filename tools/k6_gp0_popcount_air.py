#!/usr/bin/env python3
"""Independent GP0 RAW AIR receiver: exact pre-decimator 0..64 popcounts.

Each byte is the number of set PB1 samples in one 64-read group. Group count
is 4096 per session, marked by DA/version1 (never D9 PCM, A9 Sotto or FDV1).
Only CRC-valid, session-consistent, complete raw captures reconstruct bytes;
raw SDR IQ is retained separately, including failed/partial transmissions.
Never fabricates missing group counts. No radio/serial/PTT access.
"""
from __future__ import annotations
import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct

MAGIC=0xDA
VERSION=1
N_SAMPLES=4096
FS=64  # exact input bits per popcount group, NOT audio sample rate
SAMPLES_PER_PACKET=48
PACKETS=(N_SAMPLES+SAMPLES_PER_PACKET-1)//SAMPLES_PER_PACKET
HEADER=14
assert PACKETS==86
PAYLOAD_BYTES=64
WIRE_BYTES=66
GUARD=b'\x5a\xa5'


def dewhiten(wire:bytes):
    if len(wire)!=64: raise ValueError("expected exactly one 64-byte L packet")
    state=0x1ff
    out=bytearray()
    for byte in wire:
        out.append(byte^(state&255))
        for _ in range(8):
            state=(state>>1)|(((state^(state>>5))&1)<<8)
    return bytes(out)


def parse_physical(raw:bytes):
    """Parse a 64 or 66 byte RF payload, trying both validated bit-slip offsets.

    Return decoded metadata only on a unique CRC-valid D9 packet, else None.
    Unexpected bytes and other protocol magics are ignored, not executed.
    """
    if len(raw) not in (64,66):return None
    choices=(0,2) if len(raw)==66 else (0,)
    found=[]
    for off in choices:
        blob=dewhiten(raw[off:off+64])
        if blob[:2]!=bytes((MAGIC,VERSION)):continue
        if binascii.crc_hqx(blob[:62],0xffff)!=int.from_bytes(blob[62:64],"big"):continue
        seq,total=blob[2],blob[3]
        token=int.from_bytes(blob[4:8],"little")
        count=int.from_bytes(blob[8:10],"little")
        fs=int.from_bytes(blob[10:12],"little")
        duration_100us=int.from_bytes(blob[12:14],"little")
        if (total!=PACKETS or seq>=PACKETS or not token or count!=N_SAMPLES or
            fs!=FS or not (1<=duration_100us<=15000)):continue
        offset=seq*SAMPLES_PER_PACKET
        n=min(48,N_SAMPLES-offset)
        if any(blob[HEADER+n:62]) or any(v>64 for v in blob[HEADER:HEADER+n]):continue
        found.append(dict(seq=seq,total=total,token=token,samples=count,fs=fs,
                         capture_100us=duration_100us,payload=blob[HEADER:HEADER+n],
                         byte_offset=off))
    return found[0] if len(found)==1 else None


def assemble(packets):
    """Fail closed on missing/inconsistent/colliding packets."""
    parsed=[p for p in packets if p is not None]
    if not parsed:raise ValueError("no CRC-valid GP0 popcount packets")
    identity=(parsed[0]["token"],parsed[0]["samples"],parsed[0]["fs"],parsed[0]["capture_100us"])
    received={}
    for p in parsed:
        if (p["token"],p["samples"],p["fs"],p["capture_100us"])!=identity:
            raise ValueError("inconsistent GP0 RAW group session identity")
        seq=p["seq"]
        if seq in received and received[seq]!=p["payload"]:
            raise ValueError(f"conflicting duplicate GP0 RAW packet {seq}")
        received[seq]=p["payload"]
    missing=[i for i in range(PACKETS) if i not in received]
    if missing:raise ValueError(f"missing GP0 RAW packets: {missing}")
    data=b"".join(received[k] for k in range(PACKETS))
    if len(data)!=N_SAMPLES:raise ValueError("popcount length mismatch")
    return dict(token=identity[0],pdm_bits_per_group=identity[2],sample_count=identity[1],
                capture_wall_duration_ms=identity[3]/10.0,
                capture_duration_ticks_100us=identity[3],
                measured_gp0_groups_per_wall_sec=round(10000*N_SAMPLES/identity[3],2),
                input_packets=len(parsed),unique_packets=len(received),
                raw_popcount_sha256=hashlib.sha256(data).hexdigest()),data


def parse_framed_bytes(data:bytes):
    if len(data)%WIRE_BYTES:raise ValueError("physical frame file not 66-byte aligned")
    return [parse_physical(data[i:i+WIRE_BYTES]) for i in range(0,len(data),WIRE_BYTES)]


def parse_iq_complex64(path:Path):
    import numpy as np
    from tools.k6_fsk_sotto_sync_rx import find_frames
    iq=np.fromfile(path,dtype=np.complex64)
    if not np.isfinite(iq).all():raise ValueError("nonfinite IQ data")
    # The blind PHY sync is payload-independent; packet validation is ours.
    # find_frames needs its normal packet summary fields, not arbitrary PCM.
    def adapter(raw):
        p=parse_physical(raw)
        if not p:return None
        return dict(msg_id=p["token"]&255,seq=p["seq"],frames=[])
    r=find_frames(iq,fs=128000,payload_bytes=66,
                  rate_factors=(.996,.998,1.,1.002,1.004),
                  min_correlation=.80,unpack_packet=adapter,
                  max_frame_candidates=PACKETS+100)
    return [parse_physical(raw) for raw in r["byte_payloads"]],{
        "candidate_frames":r["frames_found"],"crc_accepted":r["crc_valid"],
        "candidate_hypotheses":r["candidates_considered"]}


def write_raw_new(path:Path,data:bytes):
    if len(data)!=N_SAMPLES or any(x>64 for x in data):
        raise ValueError('only complete bounded 4096-byte 0..64 popcount dumps may be saved')
    with path.open('xb') as f:f.write(data)
