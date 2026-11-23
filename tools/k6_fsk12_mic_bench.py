#!/usr/bin/env python3
"""Future one-shot FSK-12 mic-origin Sotto L64 RF test; QUEUE-LEASE GATED.

This module does NOTHING to hardware by default. Source/expected payloads
live only in a private bench folder; Git includes only SHA commitments. Before
TX, require explicit operator-confirmed dummy load, an acknowledged shared
issue #11 hardware lease, and the canonical nonblocking Flux serial flock.
The proven FSK transaction independently verifies exact signed slot30 backup,
short TX watchdog, SDR IQ, VFO and slot restoration before releasing lock.
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
MANIFEST=ROOT/'tests/data/k6/fsk12_k6mic_public_fingerprint.json'
FIXTURE=Path('/home/tyler/flux-bench/micvoice-20261123/fsk12-private-20261123')
SOURCE=FIXTURE/'fsk12-k6mic-armed.c'
SOURCE_PACKETS=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin')


def sha(data):return hashlib.sha256(data).hexdigest()


def frozen_build():
    m=json.loads(MANIFEST.read_text())
    data=SOURCE_PACKETS.read_bytes()
    src=SOURCE.read_bytes()
    private=json.loads((FIXTURE/'fsk12-private-fixture-meta.json').read_text())
    if sha(data)!=m['source_packets_file_sha256'] or sha(src)!=m['private_armed_source_sha256']:
        raise ValueError('Private source/voice packet data differ from prospective SHA commitment')
    if (private['armed_source_sha256']!=m['private_armed_source_sha256'] or
       private['armed_compiled_code_sha256']!=m['private_armed_compiled_code_sha256'] or
       private['template_sha256']!=m['generator_template_sha256']):
        raise ValueError('Private fixture manifest disagrees with public hashes')
    s=src.decode()
    for marker in ('#define FSK_BOUNDARY_ENABLE 1',
                   '#define FSK_BOUNDARY_FIXTURE_ACK 1',
                   '#define FSK_BOUNDARY_WATCHDOG_ACK 1',
                   '#define FSK_NUM_FRAMES 4u',
                   '#define FSK_WORDS 32u',
                   '#define FSK_MAX_TX_MS 1900u',
                   '#define FSK_FRAME_TIMEOUT_MS 700u',
                   '#define FSK_FREQUENCY_HZ 433500000u',
                   'W(R_LENGTH,0x3f00u);'):
        if marker not in s:raise ValueError('Missing required guarded radio parameter: '+marker)
    anchor='static const uint16_t sotto_packet_words[4][32] = {'
    if s.count(anchor)!=1:raise ValueError('Unexpected private payload array')
    payload_region=s.split(anchor,1)[1].split('static uint16_t word_for',1)[0]
    words=[int(x,16) for x in re.findall(r'0x([0-9a-fA-F]{4})u',payload_region)]
    if len(words)!=4*32 or b''.join(x.to_bytes(2,'little') for x in words)!=data:
        raise ValueError('C FIFO code values differ from private voice packet bytes')
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    assembler=pager.Assembler(p)
    for i in range(4):
        packet=data[64*i:64*(i+1)]
        info=p.unpack(packet)
        if info is None or (info['msg_id'],info['seq'],info['total'])!=(93,i,26) or info['parity'] is not None:
            raise ValueError('Private packets fail exact Sotto L64 CRC/sequence')
        if sha(packet)!=m['per_packet_sha256'][i]:raise ValueError('Private voice packet hash mismatch')
        assembler.push(packet)
    voice,good=assembler.frames(93)
    if not (len(voice)==156 and good.all() and sha(voice)==m['mic_sotto_156_byte_compressed_sha256']):
        raise ValueError('Mic Sotto superframes are incomplete or altered')
    code,entry=k6mod.build(str(SOURCE))
    if (not 0<len(code)<4096 or entry!=0 or k6mod.source_level(str(SOURCE))!=3 or
        len(code)!=m['compiled_code_bytes'] or sha(code)!=m['private_armed_compiled_code_sha256']):
        raise ValueError('Private compiled radio module differs from prospective code SHA')
    return dict(packet_count=4,crc_valid=4,voice_superframes=26,
                compiled_code_bytes=len(code),code_sha256=sha(code),source_sha256=sha(src))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',required=True,type=Path)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    p.add_argument('--operator-lease-confirmed',action='store_true',help='Operator explicitly confirms exclusive K6/SDR bench is released')
    p.add_argument('--queue-lease-acknowledged',action='store_true',help='A separate shared #11 issue ACK authorizes this specific FSK-12 physical transaction')
    a=p.parse_args()
    # NEVER allow an unattended invocation with default flags to claim a lease.
    if not all((a.dummy_load_confirmed,a.operator_lease_confirmed,a.queue_lease_acknowledged)):
        p.error('Refusing RF: dummy load + operator bench lease + explicit experiment queue #11 ACK all required')
    print('FSK-12 private module frozen preflight:',frozen_build(),flush=True)
    result=run_transaction(a.port,a.prefix,SOURCE,
          a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
          armed=True,expected_result=4*32,
          experiment='Flux FSK-12 true K6 microphone-origin 4x64B Sotto1200 native FSK RF qualification',
          watchdog_ms=1900)
    if not result['transmission_confirmed_by_firmware']:
        raise RuntimeError('No RF confirmation; inspect saved signed restoration, IQ, no automatic retry')
    print('FSK-12 one-shot TX complete; signed slot and original VFO verified restored',flush=True)


if __name__=='__main__':main()
