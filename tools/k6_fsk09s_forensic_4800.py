#!/usr/bin/env python3
"""Revisit archived FSK-09S C/D/E/F using exact PROGRAMMED 4,800-bps clock.

OFFLINE; never touches radio/SDR/serial or invokes a hardware driver. Uses
only public 16-byte 0x55 preamble + 076b3e36 sync for frame acquisition and
threshold, with known 4800-bps native modem clock. Expected payloads are
compared only AFTER RF symbol decisions and are NOT used to train/acquire.
A possible one-word shift is diagnostic ONLY and can never make FAIL a PASS.
The older prospective score JSON is preserved without alteration.
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
from k6_fsk_oneword_analyze import truth

CAPTURE_ROOT=Path('/home/tyler/flux-bench/tx-injection-20261122')
FILES={
 'C':(CAPTURE_ROOT/'fsk09s-oneword/fsk09s-oneword-run01.npy',98),
 'D':(CAPTURE_ROOT/'fsk09s-oneword/fsk09s-single-run01.npy',98),
 'E':(CAPTURE_ROOT/'fsk09s-fourbatch/fsk09s-fourbatch-run01.npy',128),
 'F':(CAPTURE_ROOT/'fsk09s-threebatch/fsk09s-threebatch-run01.npy',120),
}


def expected(case:str):
    if case=='C':return [bytes.fromhex(t['payload_bytes_hex']) for t in truth()]
    if case=='D':return [bytes.fromhex(truth()[1]['payload_bytes_hex'])]
    n=64 if case=='E' else 60
    words,_=make_probe_vector(word_count=n,seed=0xACE1)
    return [b''.join(int(x).to_bytes(2,'little') for x in words)]


def analyze_capture(case:str,path:Path,bytes_per_frame:int)->dict:
    rf=read_iq(path,duration=2.,offset=280500.)
    signals=find_frames(rf,payload_bytes=bytes_per_frame,
                        rate_factors=(1.,),min_correlation=.95)
    truths=expected(case)
    results=[]
    for i,pkt in enumerate(signals['packets']):
        received=bytes.fromhex(pkt['raw_payload_hex'])
        desired=truths[i] if i<len(truths) else b''
        errors=sum((a^b).bit_count() for a,b in zip(received,desired)) if desired else None
        # With a leading stale/control word, all but the final intended word
        # can fit in a finite, already programmed length. This post-hoc shift
        # must NOT be treated as a successful bit-exact packet.
        shifted=received[2:]
        expected_shift=desired[:-2]
        shifted_error=sum((a^b).bit_count() for a,b in zip(shifted,expected_shift)) if desired else None
        results.append(dict(frame_index=i,payload_start_s=pkt['payload_start_s'],
                            sync_corr=pkt['sync_correlation'],sync_bit_errors=pkt['sync_bit_errors'],
                            acquired_baud=pkt['baud_estimate'],first_word_le=received[:2].hex(),
                            recovered_last_word_le=received[-2:].hex(),
                            expected_last_word_le=desired[-2:].hex() if desired else None,
                            strict_full_frame_payload_bit_errors=errors,
                            posthoc_one_word_shift_bit_errors=shifted_error,
                            posthoc_one_word_shift_matching_words=((len(shifted)//2) if shifted_error==0 else 0),
                            posthoc_accepted_as_pass=False,
                            actual_rf_payload_sha256=hashlib.sha256(received).hexdigest(),
                            expected_payload_sha256=hashlib.sha256(desired).hexdigest() if desired else None))
    with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    return dict(case=case,source_iq=str(path),source_iq_sha256=digest,
                programmed_baud=4800,acquisition_uses_payload_truth=False,
                acquisition_framing='16-bytes-0x55 plus 076b3e36 hardware sync',
                acquisition_rate_factors=[1.],
                source_was_recorded_before_forensic_analysis=True,
                no_hardware_operation=True,
                expected_frames=len(truths),detected_frames=len(results),
                frames=results,
                limitations=['This is post-hoc analysis, not a precommitted PHY pass.',
                             'One leading word displaces final word in programmed finite frame.',
                             'No evidence of >64 words, continuous refill or FIFO occupancy status.',
                             'Detector assumes the preconfigured exact 4800-bps symbol clock.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True,type=Path)
    a=p.parse_args()
    if a.out.exists():p.error('Do not overwrite old evidence')
    reports={key:analyze_capture(key,*val) for key,val in FILES.items()}
    a.out.write_text(json.dumps(reports,indent=2)+'\n')
    for key,r in reports.items():
        print('FSK-09S-'+key,'frames',r['detected_frames'])
        for x in r['frames']:
            print('  first',x['first_word_le'],'strict_bit_errors',x['strict_full_frame_payload_bit_errors'],
                  'one-word-shift-errors',x['posthoc_one_word_shift_bit_errors'],
                  'matching_shifted_words',x['posthoc_one_word_shift_matching_words'])


if __name__=='__main__':main()
