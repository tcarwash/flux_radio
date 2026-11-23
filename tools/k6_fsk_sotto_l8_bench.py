#!/usr/bin/env python3
"""FSK-10B, one guarded eight-packet L Sotto burst on the shared Flux fixture.

The existing FSK-09 RF harness holds the canonical nonblocking serial lock
through slot backup, module TX, RTL-SDR IQ capture and verified restoration.
There is NO automatic TX retry, even on lock collision, timeout or abort.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from modem import k6mod,pager
from k6_fsk_boundary_bench import run_transaction

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/fsk_sotto_l8_burst_armed.c'
MANIFEST=ROOT/'tests/data/k6/fsk10b_sotto_l8_payload_manifest.json'
EXPECTED_SOURCE_SHA='3893db74e037edaa4c55229efc19bbdbc3267fd462e344aa4f149e639c38bb83'
EXPECTED_CODE_SHA='31ef35b4b7810544835c1ea2e105dd834236ff9cc402863fdfaf5e3a7a825c60'


def frozen_build():
    m=json.loads(MANIFEST.read_text())
    s=SOURCE.read_text()
    if (hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=EXPECTED_SOURCE_SHA
            or m['armed_source_sha256']!=EXPECTED_SOURCE_SHA
            or m['compiled_code_sha256']!=EXPECTED_CODE_SHA):
        raise ValueError('Exact precommitted FSK-10B source/manifest mismatch')
    invariants=(
        '#define FSK_BOUNDARY_ENABLE 1',
        '#define FSK_BOUNDARY_FIXTURE_ACK 1',
        '#define FSK_BOUNDARY_WATCHDOG_ACK 1',
        '#define FSK_NUM_FRAMES 8u',
        '#define FSK_WORDS 32u',
        '#define FSK_MAX_TX_MS 1900u',
        '#define FSK_FREQUENCY_HZ 433500000u',
        'W(R_LENGTH,0x3f00u);',
    )
    for key in invariants:
        if key not in s:raise ValueError('Missing mandatory bounded RF source invariant '+key)
    code,entry=k6mod.build(str(SOURCE))
    if (len(code)!=m['compiled_code_bytes'] or len(code)>4096 or entry!=0
        or k6mod.source_level(str(SOURCE))!=3
        or hashlib.sha256(code).hexdigest()!=EXPECTED_CODE_SHA):
        raise ValueError('Compiled K6 native module does not match precommitted code')
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packets=[bytes.fromhex(x) for x in m['payloads_hex']]
    if len(packets)!=8 or [profile.unpack(x)['seq'] for x in packets]!=list(range(8)):
        raise ValueError('Unexpected frozen real Sotto packet contents/sequencing')
    if any(len(x)!=64 or hashlib.sha256(x).hexdigest()!=m['packet_sha256'][i]
           or profile.unpack(x)['msg_id']!=m['message_id']
           for i,x in enumerate(packets)):
        raise ValueError('Frozen packet CRC, hash or length mismatch')
    arr=s.split('static const uint16_t sotto_packet_words[8][32] = {',1)[1].split('static uint16_t word_for',1)[0]
    values=[int(x,16) for x in re.findall(r'0x([0-9a-fA-F]{4})u',arr)]
    if len(values)!=256 or b''.join(v.to_bytes(2,'little') for v in values)!=b''.join(packets):
        raise ValueError('Compiled C FIFO words do not equal exact frozen Sotto bytes')
    return dict(module_code_sha256=EXPECTED_CODE_SHA,source_sha256=EXPECTED_SOURCE_SHA,
                code_bytes=len(code),packet_sha256=m['packet_sha256'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',type=Path,required=True)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    args=p.parse_args()
    if not args.dummy_load_confirmed:
        p.error('Dummy-load/SRD fixture confirmation required, no RF by default')
    print('FSK-10B frozen source',frozen_build(),flush=True)
    result=run_transaction(args.port,args.prefix,SOURCE,
           args.prefix.with_name(args.prefix.name+'-slot30-before.k6m'),
           armed=True,expected_result=8*32,
           experiment='Flux FSK-10B one finite eight-packet CRC-valid Sotto native-FSK burst',
           watchdog_ms=1900)
    if not result['transmission_confirmed_by_firmware']:
        raise RuntimeError('RF module incomplete; inspect saved restoration/SDR evidence')
    print('FSK-10B completed; original module and VFO restored',flush=True)


if __name__=='__main__':main()
