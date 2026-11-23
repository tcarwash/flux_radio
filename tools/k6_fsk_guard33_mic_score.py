#!/usr/bin/env python3
"""Prospective strict 4xL64 microphone guard RF scorer, OFFLINE only.

Receiver already selects public preamble/sync and an *unknown-content*
unique CRC-valid 64-byte L packet from the 66-byte physical frame.
Compare packet SHA-256 with PRIVATE source only after CRC reception.
No microphone/voice bytes are printed or committed.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_guard33_mic_bench import frozen_build


def assess(rx:dict,expected_hashes:list[str]) -> dict:
    fail=[]
    rows=rx.get('frames',[])
    if len(expected_hashes)!=4:
        fail.append('private manifest expected exactly four source packets')
    if len(rows)!=4 or rx.get('frames_detected')!=4:
        fail.append('not exactly four independent public-sync physical RF frames')
    if rx.get('crc_valid')!=4 or rx.get('crc_ambiguous')!=0:
        fail.append('CRC invalid or ambiguous')
    offsets={'normal':0,'prefix_displaced_guard':0}
    sequences=[]
    for i,frame in enumerate(rows):
        if frame.get('sync_errors')!=0:
            fail.append(f'frame {i} public sync errors')
        if frame.get('selected_baud')!=4800:
            fail.append(f'frame {i} unexpected modem baud')
        if frame.get('valid') is not True or frame.get('ambiguous') is not False:
            fail.append(f'frame {i} no unique valid packet')
            continue
        offset=frame.get('offset_bytes')
        if offset not in (0,2):
            fail.append(f'frame {i} unexpected frame offset')
        else:
            offsets['normal' if offset==0 else 'prefix_displaced_guard']+=1
        if frame.get('msg_id')!=93 or frame.get('seq')!=i:
            fail.append(f'frame {i} wrong microphone message ID/sequence')
        sequences.append(frame.get('seq'))
        if frame.get('total_codec_frames')!=26 or frame.get('codec_frames')!=8:
            fail.append(f'frame {i} wrong codec frame count metadata')
        if i>=len(expected_hashes) or frame.get('packet_sha256')!=expected_hashes[i]:
            fail.append(f'frame {i} not bit-identical to private microphone packet')
    return {
        'experiment':'FSK-14 actual K6-mic Sotto L64 over guarded FSK RF',
        'result':'STRICT_RX_PASS' if not fail else 'FAIL_OR_INCONCLUSIVE',
        'original_mic_audio_packet_bits_checked':len(expected_hashes)*64*8,
        'original_sotto_superframes':26,
        'physical_frame_bytes':66,
        'packet_offsets':offsets,
        'sequences':sequences,
        'failed_requirements':fail,
        'privacy':'hash-only truth comparison; source microphone bytes not emitted',
        'limits':['Requires separate firmware return 132 and radio state restoration for physical whole-test PASS.',
                  'Not a live microphone encode/decode running autonomously on K6.',
                  'Not >64-word FIFO streaming or continuous two-way DV.'],
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rx',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    if a.out.exists():p.error('refusing overwrite')
    frozen=frozen_build()
    data=json.loads(a.rx.read_text())
    score=assess(data,frozen['expected_packet_hashes'])
    score['capture']=data.get('capture')
    score['iq_sha256']=data.get('iq_sha256')
    a.out.write_text(json.dumps(score,indent=2)+'\n')
    print(score['result'],'CRC offsets',score['packet_offsets'],
          'failed requirements',score['failed_requirements'])


if __name__=='__main__':
    main()
