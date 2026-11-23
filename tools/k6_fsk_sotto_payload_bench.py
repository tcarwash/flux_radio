#!/usr/bin/env python3
"""FSK-11 single guarded TX of two real Sotto-1200 L packets using shared K6 lock.

Exact frozen payload and compiled C hash preflight. Inherits one-shot module
backup, 1900-ms watchdog, serial+SDR exclusivity and restoration from FSK-09.
Only the user-confirmed conducted dummy-load/SDR bench may arm this module.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from k6_fsk_boundary_bench import run_transaction
from modem import k6mod, pager

HERE=Path(__file__).resolve().parents[1]
SOURCE=HERE/'c/k6/mod/fsk_sotto_l_payload_armed.c'
MANIFEST=HERE/'tests/data/k6/fsk11_sotto_l_payload_manifest.json'
EXPECTED_SOURCE_SHA='08b02d1a437096d3b260e8d7d043ed2d7de5f872e04bab60fae3340823e4a657'
EXPECTED_CODE_SHA='2f042fe965d30f0972b80dc61166d03b887902bc556c7557f0434ac7deeb0224'


def frozen_build():
    m=json.loads(MANIFEST.read_text())
    if (hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=EXPECTED_SOURCE_SHA or
        m['module_source_sha256']!=EXPECTED_SOURCE_SHA or
        m['module_code_sha256']!=EXPECTED_CODE_SHA):
        raise RuntimeError('FSK-11 frozen Sotto source/manifest mismatch')
    src=SOURCE.read_text()
    for s in ('#define FSK_BOUNDARY_ENABLE 1',
              '#define FSK_BOUNDARY_FIXTURE_ACK 1',
              '#define FSK_BOUNDARY_WATCHDOG_ACK 1',
              '#define FSK_NUM_FRAMES 2u',
              '#define FSK_WORDS 32u',
              '#define FSK_MAX_TX_MS 1900u',
              'W(R_LENGTH,0x3f00u);'):
        if s not in src:raise RuntimeError('Missing safety/PHY invariant '+s)
    code,entry=k6mod.build(str(SOURCE))
    if not entry==0 or k6mod.source_level(str(SOURCE))!=3 or hashlib.sha256(code).hexdigest()!=EXPECTED_CODE_SHA:
        raise RuntimeError('FSK-11 compiled module image differs from approved fixed packet')
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    payloads=[bytes.fromhex(x) for x in m['packet_hex']]
    if len(payloads)!=2 or any(len(x)!=64 for x in payloads):
        raise RuntimeError('FSK-11 frozen payload length mismatch')
    for i,packet in enumerate(payloads):
        q=p.unpack(packet)
        if q is None or q['seq']!=i or q['msg_id']!=33:
            raise RuntimeError('FSK-11 Sotto profile header/CRC mismatch')
        if hashlib.sha256(packet).hexdigest()!=m['packet_sha256'][i]:
            raise RuntimeError('FSK-11 Sotto payload SHA mismatch')
    return dict(code_sha256=EXPECTED_CODE_SHA,source_sha256=EXPECTED_SOURCE_SHA,code_bytes=len(code),
                packet_sha256=m['packet_sha256'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    args=p.parse_args()
    if not args.dummy_load_confirmed:p.error('Physical dummy-load confirmation required')
    print('FSK-11 preflight',frozen_build(),flush=True)
    result=run_transaction(args.port,args.prefix,SOURCE,
                    args.prefix.with_name(args.prefix.name+'-slot30-before.k6m'),
                    armed=True,expected_result=64,
                    experiment='Flux FSK-11 two CRC-valid Sotto-1200 L 64B native 2-FSK frames',
                    watchdog_ms=1900)
    if not result['transmission_confirmed_by_firmware']:
        raise RuntimeError('Sotto RF module incomplete; inspect saved IQ and reports')
    print('FSK-11 TX complete; radio original module/VFO restored',flush=True)


if __name__=='__main__':main()
