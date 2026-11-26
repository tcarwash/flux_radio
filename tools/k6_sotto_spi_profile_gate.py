#!/usr/bin/env python3
"""Build-only K6 Sotto SPI diagnostic compatibility gate.

Compares a clean uninstrumented rebuild to the exact known fielded firmware.
Fails closed unless they match byte-for-byte before allowing any instrumented
candidate to be marked READY_FOR_ATTENDED_REVIEW. This tool never flashes or
opens serial or any RF hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

FLASH_LIMIT=122880
EXPECTED_FIELD_SHA256='8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d'

def digest(data: bytes)->str:
    return hashlib.sha256(data).hexdigest()

def audit(field:bytes, baseline:bytes, candidate:bytes,
          field_layout:dict, baseline_layout:dict, candidate_layout:dict)->dict:
    problems=[]
    if digest(field)!=EXPECTED_FIELD_SHA256:
        problems.append('reference firmware is not the pinned known-good KUL2 image')
    if baseline!=field:
        problems.append('uninstrumented clean rebuild differs from known-good firmware')
    for name,b in [('reference',field),('baseline',baseline),('candidate',candidate)]:
        if b.count(b'KUL2')<1:
            problems.append(f'{name} lacks packed KUL2 module-library support')
        if not b or len(b)>FLASH_LIMIT:
            problems.append(f'{name} exceeds flash size or is empty')
    if baseline.count(b'SPB1') or candidate.count(b'SPB1')!=1:
        problems.append('SPI timing probe signature not isolated to instrumented image')
    for key in ('arena_base','arena_bytes','ram0_used','ram0_free','stack_reservation'):
        vals=[field_layout.get(key),baseline_layout.get(key),candidate_layout.get(key)]
        if any(v is None for v in vals) or vals[0]!=vals[1] or vals[0]!=vals[2]:
            problems.append(f'RAM/stack memory contract mismatch: {key} = {vals}')
    return {
        'status':'READY_FOR_ATTENDED_REVIEW' if not problems else 'STOP_DO_NOT_FLASH',
        'reference_sha256':digest(field),
        'baseline_sha256':digest(baseline),
        'candidate_sha256':digest(candidate),
        'reference_size':len(field),
        'baseline_size':len(baseline),
        'candidate_size':len(candidate),
        'candidate_flash_free':FLASH_LIMIT-len(candidate),
        'baseline_matches_reference':baseline==field,
        'ram0_contract_matches':all(
            field_layout.get(k)==baseline_layout.get(k)==candidate_layout.get(k)
            for k in ('arena_base','arena_bytes','ram0_used','ram0_free','stack_reservation')),
        'safety_failures':problems,
        'note':'Build-only audit; READY does not authorize flash or key PTT.',
    }

def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference',required=True,type=Path)
    p.add_argument('--baseline',required=True,type=Path)
    p.add_argument('--candidate',required=True,type=Path)
    p.add_argument('--reference-layout',required=True,type=Path)
    p.add_argument('--baseline-layout',required=True,type=Path)
    p.add_argument('--candidate-layout',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path,help='New JSON manifest; cannot overwrite')
    a=p.parse_args()
    report=audit(a.reference.read_bytes(),a.baseline.read_bytes(),a.candidate.read_bytes(),
                 json.loads(a.reference_layout.read_text()),
                 json.loads(a.baseline_layout.read_text()),
                 json.loads(a.candidate_layout.read_text()))
    report['paths']={
        'reference':str(a.reference.resolve()),
        'baseline':str(a.baseline.resolve()),
        'candidate':str(a.candidate.resolve())}
    with a.output.open('x') as f:
        f.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2,sort_keys=True))
    if report['status']!='READY_FOR_ATTENDED_REVIEW':
        p.exit(2,'STOP: preflash qualification rejected the candidate\n')

if __name__=='__main__':
    main()
