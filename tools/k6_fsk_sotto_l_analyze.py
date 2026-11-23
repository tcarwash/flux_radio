#!/usr/bin/env python3
"""Read-only heldout-bit RF analysis of FSK-10 2x64 byte capture."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_lab_vector import build_vector,bits_from_words,make_probe_vector
from k6_fsk_lab_decode import decode_samples
from k6_fsk_iq_edges import read_iq

BAUD=4800
NWORDS=32
TRAIN=256
NOMINAL=512/BAUD


def truth():
    orig,_=make_probe_vector(word_count=64,seed=0xACE1)
    a=tuple(list(orig[32:60])+[0]*4)
    b=tuple([0]*4+[0xFFFF]*4+[(~orig[32+i]) &0xFFFF for i in range(8,32)])
    assert len(a)==len(b)==32
    return [build_vector(word_count=32,baud=BAUD,gain=21,data_bits=bits_from_words(words))[1]
            for words in (a,b)]


def analyze(path:Path):
    iq=read_iq(path,duration=1.8,offset=280500.)
    manifests=truth()
    frames=[decode_samples(iq,m,train_bits=TRAIN,lowpass_hz=8000.) for m in manifests]
    result={
        'experiment':'FSK-10 two native 64-byte bursts',
        'offline_analysis':True,
        'recording':str(path),
        'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'fsk_baud_nominal':BAUD,
        'payload_bytes_per_frame':64,
        'frames':frames,
        'payload_sha256s':[m['payload_sha256_le_words'] for m in manifests],
        'result':'INCONCLUSIVE',
        'caveats':['Only two fixed known payloads; this does not establish general random data reception.',
                   'Neither actual voice nor continuous FIFO streaming is tested.',
                   'The SDR is air-coupled to a dummy-loaded radio; no RF mask/power calibration.']}
    if all(f.get('locked') and f.get('held_out_tail_evaluated') for f in frames):
        starts=[f['detected_first_payload_bit_center_s'] for f in frames]
        if starts[1]>starts[0] and starts[1]-starts[0]<.5:
            diff=starts[1]-starts[0]
            result['payload_start_spacing_ms']=round(1000*diff,3)
            result['nonpayload_rearm_ms']=round(1000*(diff-NOMINAL),3)
            result['measured_two_frame_payload_ber_heldout_bits']=[f['held_out_tail_errors'] for f in frames]
            result['heldout_bits_per_frame']=512-TRAIN
            result['result']='ACQUIRED' if all(f['held_out_tail_errors']==0 for f in frames) else 'ACQUIRED_WITH_ERRORS'
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--iq',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():p.error('Refuse to overwrite prior analysis')
    report=analyze(args.iq)
    args.out.write_text(json.dumps(report,indent=2)+'\n')
    print('Result',report['result'],'spacing_ms',report.get('payload_start_spacing_ms'),
          'gap_ms',report.get('nonpayload_rearm_ms'),
          'heldout_errors',report.get('measured_two_frame_payload_ber_heldout_bits'))

if __name__=='__main__':main()
