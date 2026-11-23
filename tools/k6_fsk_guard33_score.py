#!/usr/bin/env python3
"""Frozen prospective FSK-13 whole-frame scorer (offline only).

The receiver has already recovered physical bytes from public preamble/sync,
and independently selected a unique CRC-valid L64 window. This scorer uses
the known FSK-10B manifest ONLY AFTER RF reception, as a strict BER/identity
pass gate. A firmware return status is separately required by the protocol.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_guard33_bench import frozen_build


def assess(result:dict,expected_hashes:list[str]) -> dict:
    frames=result.get('frames',[])
    failures=[]
    if len(expected_hashes)!=8:failures.append('incorrect expected manifest length')
    if result.get('frames_detected')!=8 or len(frames)!=8:
        failures.append('frame count not eight')
    if result.get('crc_valid')!=8 or result.get('crc_ambiguous')!=0:
        failures.append('CRC-invalid or ambiguous frame(s)')
    counts={'normal':0,'prefix_displaced_guard':0}
    seq=[]
    for i,f in enumerate(frames):
        if f.get('sync_errors')!=0:failures.append(f'frame {i}: sync bit errors')
        if f.get('selected_baud')!=4800.:failures.append(f'frame {i}: baud mismatch')
        if not f.get('valid') or f.get('ambiguous'):
            failures.append(f'frame {i}: no unique CRC-valid L64 candidate')
            continue
        if f.get('offset_bytes') not in (0,2):
            failures.append(f'frame {i}: unexpected position')
        counts['normal' if f.get('offset_bytes')==0 else 'prefix_displaced_guard']+=1
        if f.get('msg_id')!=73 or f.get('seq')!=i:
            failures.append(f'frame {i}: wrong voice message/sequence')
        seq.append(f.get('seq'))
        if i>=len(expected_hashes) or f.get('packet_sha256')!=expected_hashes[i]:
            failures.append(f'frame {i}: payload not bit-identical to frozen L64')
        if f.get('codec_frames')!=8:
            failures.append(f'frame {i}: unexpected Sotto frame count')
    return {
        'experiment':'FSK-13 eight packet L64 guard PHY RF acceptance',
        'rf_payload_bits_per_frame':66*8,
        'audio_packet_bits_validated':64*8*8,
        'result':'STRICT_RX_PASS' if not failures else 'FAIL_OR_INCONCLUSIVE',
        'frame_offsets':counts,'sequence':seq,
        'failed_requirements':failures,
        'notes':['Only valid after the independent public-sync RF receiver.',
                 'Full PASS also requires one successful module invocation and restored slot/VFO.',
                 'No indefinite FIFO refill, autonomous K6 RX/audio output or live DV proven.'],
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rx',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('refuse to overwrite scored evidence')
    expected=frozen_build()['expected_packet_sha256']
    rx=json.loads(a.rx.read_text())
    scored=assess(rx,expected)
    scored['receiver_report']=str(a.rx)
    scored['capture']=rx.get('capture')
    scored['iq_sha256']=rx.get('iq_sha256')
    a.out.write_text(json.dumps(scored,indent=2)+'\n')
    print(scored['result'],scored['frame_offsets'],scored['failed_requirements'])


if __name__=='__main__':main()
