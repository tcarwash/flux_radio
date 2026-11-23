#!/usr/bin/env python3
"""Post-RF independent native FSK public sync, one 98-byte payload vs saved truth.

Only 16x0x55 + 076b3e36 known hardware framing is used to acquire the
radio payload and frequency decision levels. The expected late marker is
used only for scoring after demodulation.
"""
from __future__ import annotations
import argparse
import json
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_iq_edges import read_iq
from k6_fsk_sotto_sync_rx import find_frames
from k6_fsk_oneword_analyze import truth


def analyze_file(path:Path):
    iq=read_iq(path,duration=2.,offset=280500.)
    x=find_frames(iq,payload_bytes=98,min_correlation=.95)
    expected=bytes.fromhex(truth()[1]['payload_bytes_hex'])
    packets=x['packets']
    results=[]
    for r in packets:
        raw=bytes.fromhex(r['raw_payload_hex'])
        errors=sum((a^b).bit_count() for a,b in zip(raw,expected))
        results.append(dict(sync_start_s=r['marker_start_s'],
                            frame_payload_start_s=r['payload_start_s'],
                            correlation=r['sync_correlation'],
                            preamble_sync_errors=r['sync_bit_errors'],
                            payload_bit_errors=errors,
                            received_tail_word_le=raw[-2:].hex(),
                            expected_tail_word_le=expected[-2:].hex(),
                            first_word_le=raw[:2].hex(),
                            last_word_exact=raw[-2:]==expected[-2:],
                            first_48_words_exact=raw[:96]==expected[:96],
                            received_payload_hex=raw.hex()))
    pass_all=len(results)==1 and results[0]['payload_bit_errors']==0 and results[0]['last_word_exact']
    return dict(experiment='FSK-09S-D stand-alone single 48+1 in-frame FIFO append',
                result='ONE_WORD_INFLIGHT_APPEND_PASS' if pass_all else 'INCONCLUSIVE_OR_FAIL',
                recording=str(path),iq_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                candidates_considered=x['candidates_considered'],
                independent_frame_syncs=x['frames_found'],
                receiver_training='only universal hardware preamble + sync (not payload)',
                modem_baud=4800,payload_bytes=98,total_fifo_writes=49,
                first_fifo_writes_before_tx=48,delay_to_one_write_after_tx_ms=70,
                frames=results,
                limitations=['Finite single-frame result, not repeated FIFO streaming.',
                             'Success requires complete RF-verified 49-word payload, not register success.',
                             'Air-coupled SDR is uncalibrated; unknown BER in real channel.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('Refuse overwrite')
    result=analyze_file(a.iq)
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print('FSK-09S-D',result['result'],'synced',result['independent_frame_syncs'])
    for x in result['frames']:
        print('complete errors',x['payload_bit_errors'],'first 48 exact?',x['first_48_words_exact'],'tail observed',x['received_tail_word_le'],'expected',x['expected_tail_word_le'])


if __name__=='__main__':main()
