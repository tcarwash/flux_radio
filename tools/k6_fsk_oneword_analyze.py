#!/usr/bin/env python3
"""Post-capture FSK-09S-C analysis with payload-INDEPENDENT native FSK sync.

Only the public FD6818B native 16*0x55 preamble + 0x076b3e36 sync trains
frame starts and discriminator levels. No test-payload bits are used for
acquisition. The fixed trial's expected control/append bits are used only
AFTER independent demodulation for mismatch scoring. This analyzer replaces
an ambiguous periodic-training decoder discovered faulty on the RF capture;
it was developed after the first run, so report that distinction explicitly.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_lab_vector import build_vector,bits_from_words,make_probe_vector
from k6_fsk_sotto_sync_rx import find_frames
from k6_fsk_iq_edges import read_iq

WORDS=49


def truth():
    source,_=make_probe_vector(word_count=64,seed=0xACE1)
    first=tuple(source[:48])
    frames=(first+(0x3ca5,),tuple((~w)&0xffff for w in first)+(0xa53c,))
    return [build_vector(word_count=WORDS,data_bits=bits_from_words(x),baud=4800,gain=21)[1]
            for x in frames]


def score(observed,expected,index):
    b=bytes.fromhex(observed['raw_payload_hex'])
    control=bytes.fromhex(expected['payload_bytes_hex'])
    n=min(len(b),len(control))
    diffs=[(b[i]^control[i]).bit_count() for i in range(n)]
    shifted=bytes.fromhex(observed['raw_payload_hex'])
    # Under a 1-word leading shift, the first 48 words may match after
    # skipping first observed word; do not treat that as full 49-word pass.
    shifted_prefix=shifted[2:98]
    shifted_bits=sum((x^y).bit_count() for x,y in zip(shifted_prefix,control[:96]))
    return dict(frame='control_full_49word_preload' if index==0 else '48_preload_plus_one_midtx_append',
                frame_payload_start_s=observed['payload_start_s'],
                synchronization_correlation=observed['sync_correlation'],
                public_preamble_sync_bit_errors=observed['sync_bit_errors'],
                raw_received_last_word_le=b[-2:].hex(),
                expected_last_word_le=control[-2:].hex(),
                expected_payload_sha256=expected['payload_sha256_le_words'],
                complete_payload_bit_errors=sum(diffs),
                last_word_match=b[-2:]==control[-2:],
                first_wrong_byte_index=next((i for i,x in enumerate(diffs) if x),None),
                first_received_word_le=b[:2].hex(),
                one_word_shift_first_48words_bit_errors=shifted_bits,
                received_payload_bytes_hex=b.hex(),
                acquisition_used_test_payload=False)


def analyze_file(path:Path):
    iq=read_iq(path,duration=2.0,offset=280500.)
    found=find_frames(iq,payload_bytes=98,min_correlation=.95)
    matched=found['packets']
    expected=truth()
    results=[score(observed,expected[i],i) for i,observed in enumerate(matched[:2])]
    sep=results[1]['frame_payload_start_s']-results[0]['frame_payload_start_s'] if len(results)==2 else None
    success=(len(results)==2 and all(v['complete_payload_bit_errors']==0 for v in results)
             and all(v['last_word_match'] for v in results))
    return dict(experiment='FSK-09S-C single-word in-flight FIFO write vs full preload control',
                result='CONTROL_AND_APPEND_PASS' if success else 'INCONCLUSIVE_OR_FAIL',
                recording=str(path),iq_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                decoder_change='Post-RF: replaced ambiguous periodic known-payload sync with public fixed framing',
                acquisition_uses_only_public_native_fsk_preamble_and_sync=True,
                native_fsk_bits_per_frame=WORDS*16,payload_words_per_frame=WORDS,
                one_word_delayed_by_ms=70,first_frame_fully_preloaded=True,
                second_frame_initial_words=48,second_frame_live_append_words=1,
                candidates_considered=found['candidates_considered'],
                independent_framing_candidates=found['frames_found'],
                payload_start_separation_ms=round(sep*1000,3) if sep else None,
                frames=results,
                limits=['One-word physical TX test, not true streaming or FIFO occupancy discovery.',
                        'The original prefix-trained decoder was ambiguous; sync-only reanalysis is post hoc.',
                        'No subsequent RF write automatically authorized by any negative outcome.',
                        'Dummy-loaded uncalibrated SDR, not real-world signal performance.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    opts=p.parse_args()
    if opts.out.exists():p.error('Refuse to overwrite a previous analysis')
    a=analyze_file(opts.iq)
    opts.out.write_text(json.dumps(a,indent=2)+'\n')
    print('FSK-09S-C',a['result'],'frames',len(a['frames']),'spacing',a['payload_start_separation_ms'])
    for f in a['frames']:
        print(f['frame'],'errors',f['complete_payload_bit_errors'],'tail',f['raw_received_last_word_le'],
              'expected tail',f['expected_last_word_le'],
              'shifted-48word-errors',f['one_word_shift_first_48words_bit_errors'])


if __name__=='__main__':main()
