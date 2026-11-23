#!/usr/bin/env python3
"""Offline independent decode of FSK-09S-C known control vs in-flight append.

Uses ONLY the first 256 payload bits of each 49-word frame to estimate
symbol timing/levels; the appended final word is in the held-out 528 bits.
No radio, serial or device operations.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import numpy as np
from k6_fsk_lab_vector import build_vector,bits_from_words,make_probe_vector
from k6_fsk_lab_decode import decode_samples
from k6_fsk_sotto_payload_analyze import rf_recover_bits
from k6_fsk_iq_edges import read_iq

WORDS=49
TRAIN_BITS=256


def truth():
    first,_=make_probe_vector(word_count=64,seed=0xACE1)
    before=tuple(first[:48])
    words=(before+(0x3ca5,),tuple((~x)&0xffff for x in before)+(0xa53c,))
    return [build_vector(word_count=WORDS,data_bits=bits_from_words(w),baud=4800,gain=21)[1]
            for w in words]


def analyze_file(path:Path):
    iq=read_iq(path,duration=2.,offset=280500.)
    truths=truth();out=[];decisions=[]
    for i,m in enumerate(truths):
        fit=decode_samples(iq,m,train_bits=TRAIN_BITS,lowpass_hz=8000.)
        if fit.get('locked'):
            rec=rf_recover_bits(iq,fit,nbits=WORDS*16)
            observed=np.packbits(rec).tobytes()
            ref=bytes.fromhex(m['payload_bytes_hex'])
            errors=int(np.count_nonzero(rec!=np.unpackbits(np.frombuffer(ref,dtype=np.uint8))))
            ending=observed[-2:].hex()
            tail_match=observed[-2:]==ref[-2:]
            decisions.append(observed)
        else:
            errors=None;ending=None;tail_match=False
        out.append(dict(frame=('49word_full_preload_control' if i==0 else '48word_preload_plus_one_live_append'),
                        expected_last_word_le=m['payload_bytes_hex'][-4:],
                        recovered_last_word_le=ending,
                        last_word_match=tail_match,
                        total_bit_errors=errors,
                        payload_sha256=m['payload_sha256_le_words'],
                        known_only_first_256_bits_used_for_sync=True,**fit))
    starts=[v.get('detected_first_payload_bit_center_s') for v in out]
    spacing=(starts[1]-starts[0]) if all(x is not None for x in starts) else None
    result='CONTROL_AND_APPEND_PASS' if all(x.get('locked') and x.get('total_bit_errors')==0
                                                for x in out) else 'INCONCLUSIVE_OR_FAIL'
    return dict(experiment='FSK-09S-C single-word in-frame append vs fully preloaded control',
                result=result,recording=str(path),
                iq_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                payload_baud=4800,payload_words=WORDS,
                configured_bytes=98,nominal_payload_s=WORDS*16/4800,
                one_word_delayed_by_ms=70,
                first_frame_fully_preloaded=True,second_frame_preloaded_words=48,
                append_words_after_start=1,
                packet_start_separation_ms=round(1000*spacing,3) if spacing else None,
                frames=out,
                caveats=['This test has distinct frame rearm; it is not continuous FIFO streaming.',
                         'Known 256-bit training prefix per frame; last 528 bits are held out.',
                         'Passing establishes a one-word write during TX only, not multiple sustained refills.',
                         'RF capture is from dummy-loaded air-coupled SDR, not calibrated.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('Refusing to overwrite existing analysis')
    z=analyze_file(a.iq)
    a.out.write_text(json.dumps(z,indent=2)+'\n')
    print(z['result'],'frame starts delta ms',z['packet_start_separation_ms'])
    for v in z['frames']:
        print(v['frame'],'end',v['recovered_last_word_le'],'expected',v['expected_last_word_le'],
              'match',v['last_word_match'],'heldout',v.get('held_out_tail_errors'),
              'errors',v['total_bit_errors'])


if __name__=='__main__':main()
