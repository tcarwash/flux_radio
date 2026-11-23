#!/usr/bin/env python3
"""Offline native-FSK public-sync independent reception for three-batch append.

Uses only chip 16x55 preamble + 076b3e36 hardware sync to fit frequency
levels, symbol clock and packet start. Expected 60-word data are consulted
ONLY after RF symbol decisions; last 12 appended words must match exactly.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_iq_edges import read_iq
from k6_fsk_sotto_sync_rx import find_frames
from k6_fsk_lab_vector import make_probe_vector


def analyze_file(path:Path):
    iq=read_iq(path,duration=2.,offset=280500.)
    frames=find_frames(iq,payload_bytes=120,min_correlation=.95)
    expected_words,_=make_probe_vector(word_count=60,seed=0xACE1)
    expected=b''.join(x.to_bytes(2,'little') for x in expected_words)
    out=[]
    for packet in frames['packets']:
        received=bytes.fromhex(packet['raw_payload_hex'])
        errors=sum((a^b).bit_count() for a,b in zip(received,expected))
        preload=received[:48*2]
        appended=received[48*2:]
        out.append(dict(sync_start_s=packet['marker_start_s'],
                        payload_start_s=packet['payload_start_s'],
                        preamble_sync_correlation=packet['sync_correlation'],
                        public_sync_bit_errors=packet['sync_bit_errors'],
                        payload_bit_errors=errors,
                        original_48_word_preload_exact=preload==expected[:96],
                        all_12_appended_words_exact=appended==expected[96:],
                        appended_12_words_bit_errors=sum((a^b).bit_count() for a,b in zip(appended,expected[96:])),
                        first_received_word_le=received[:2].hex(),
                        last_received_word_le=received[-2:].hex(),
                        last_expected_word_le=expected[-2:].hex(),
                        complete_payload_hex=received.hex()))
    success=(len(out)==1 and out[0]['payload_bit_errors']==0 and out[0]['all_12_appended_words_exact'])
    return dict(experiment='FSK-09S-F three in-flight 4-word FIFO batches in one 60-word hardware frame',
                result='THREE_BATCH_INFLIGHT_PASS' if success else 'INCONCLUSIVE_OR_FAIL',
                iq_path=str(path),iq_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                known_public_preamble_sync_only=True,
                expected_payload_sha256=hashlib.sha256(expected).hexdigest(),
                payload_baud=4800,programmed_fifo_words=60,preloaded_words=48,
                write_batch_sizes=[4,4,4],nominal_batch_timings_ms=[70,95,120],
                independent_frame_candidates=frames['frames_found'],
                candidates_considered=frames['candidates_considered'],frames=out,
                caveats=['Total 60 words remain 4 below the demonstrated 64-word preload capacity.',
                         'This tests three four-word in-flight append batches, NOT >64-word FIFO streaming.',
                         'Control is previously measured 64-word fully preloaded frame, not same-session.',
                         'SDR is uncalibrated air-coupled dummy-load fixture.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',required=True,type=Path)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    if a.out.exists():p.error('Refusing to overwrite report')
    result=analyze_file(a.iq)
    a.out.write_text(json.dumps(result,indent=2)+'\n')
    print('FSK-09S-F',result['result'],'frames',result['independent_frame_candidates'])
    for x in result['frames']:
        print('total errors',x['payload_bit_errors'],'append tail errors',x['appended_12_words_bit_errors'],
              'initial 48 exact',x['original_48_word_preload_exact'],
              'all three-batch 12 words exact',x['all_12_appended_words_exact'])

if __name__=='__main__':main()
