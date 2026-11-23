#!/usr/bin/env python3
"""FSK-12 physical archive: forensic-only 0x00F8 displacement classifier.

This is NOT the original prospective RF receiver; preserve its FAIL verdict.
Only inspect archived SDR .npy and private locally stored source packets.
Never transmit, access radio serial, or publish audio/packet contents.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))

from k6_fsk12_mic_rf import packets_from_file
from k6_fsk_iq_edges import read_iq
from k6_fsk_sotto_sync_rx import find_frames
from modem import pager


def errors(received:bytes, truth:bytes)->int:
    if len(received)!=len(truth):raise ValueError("Payload length mismatch")
    return sum((x^y).bit_count() for x,y in zip(received,truth))


def packet_diagnosis(received:bytes, expected:bytes):
    if len(expected)!=64 or len(received)!=64:
        raise ValueError("Only fixed 64-byte L packets")
    displaced=b"\xf8\x00"+expected[:62]
    p=pager.profile("L","1200",depth=1,parity=False,short_last=True)
    return {
        "first_received_word_le": received[:2].hex(),
        "original_expected_packet_crc_valid": p.unpack(expected) is not None,
        "received_packet_crc_valid": p.unpack(received) is not None,
        "strict_bit_errors": errors(received,expected),
        "posthoc_control_prefix_bit_errors": errors(received,displaced),
        "posthoc_62_payload_bytes_exact": received[2:] == expected[:62],
        "last_two_expected_bytes_missing_from_declared_frame": received == displaced,
        "posthoc_only_not_strict_pass": True,
    }


def forensic_report(iq_path:Path, packets_path:Path)->dict:
    expected=packets_from_file(packets_path)
    with iq_path.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    iq=read_iq(iq_path,duration=2.0,offset=280500.)
    detected=find_frames(iq,rate_factors=(1.,),min_correlation=.95)
    rows=[]
    for i,frame in enumerate(detected['packets']):
        if i>=len(expected):break
        recovered=bytes.fromhex(frame['raw_payload_hex'])
        rows.append({
            "frame_index":i,
            "payload_start_s":frame["payload_start_s"],
            "sync_correlation":frame["sync_correlation"],
            **packet_diagnosis(recovered,expected[i]),
        })
    match=(len(rows)==4 and detected['frames_found']==4 and
           all(r["last_two_expected_bytes_missing_from_declared_frame"] and
               r["posthoc_control_prefix_bit_errors"]==0 for r in rows))
    return {
        "experiment":"FSK-12 mic-origin Sotto: 0x00F8 offline forensic analysis",
        "prospective_strict_verdict_unchanged":"FAIL_OR_INCONCLUSIVE",
        "diagnosis":"CONTROL_PREFIX_DISPLACED_CRC" if match else "NOT_FULLY_EXPLAINED",
        "public_preamble_sync_only":True,
        "fixed_native_baud":4800,
        "iq_sha256":digest,
        "packet_file_sha256":hashlib.sha256(packets_path.read_bytes()).hexdigest(),
        "frames_detected":detected['frames_found'],
        "crc_valid_as_received":sum(r['received_packet_crc_valid'] for r in rows),
        "total_strict_bit_errors":sum(r['strict_bit_errors'] for r in rows),
        "total_posthoc_prefix_bit_errors":sum(r['posthoc_control_prefix_bit_errors'] for r in rows),
        "frames":rows,
        "caveats":[
            "All reported post-hoc recovered mic bytes exclude each missing last CRC word.",
            "This does not prove usable packet transport, complete codec bitstream, or live DV.",
            "Do not run a physical test or auto-correct packets based solely on this report.",
        ],
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--packets',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('No overwrite of evidence')
    d=forensic_report(a.iq,a.packets)
    a.out.write_text(json.dumps(d,indent=2)+"\n")
    print(d['diagnosis'],"frame count",d['frames_detected'],
          "strict bit errors",d['total_strict_bit_errors'],
          "posthoc displaced bit errors",d['total_posthoc_prefix_bit_errors'])


if __name__=="__main__":
    main()
