#!/usr/bin/env python3
"""Private-only FSK-13 guard frame module generator for real K6 microphone L64.

Never writes real microphone bytes to the public Git checkout. The input
is the *existing* private 4-packet/256-byte K6-mic Sotto L64 artifact.
Output is a mode-0600 private C transmitter, plus hash-only manifest.
This tool NEVER accesses the radio, SDR, serial device, or PTT.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import k6mod,pager

ROOT=Path(__file__).resolve().parents[1]
TEMPLATE=ROOT/'c/k6/mod/fsk_guard33_sotto_armed.c'
PRIVATE_PACKETS=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin')
EXPECTED_SHA='d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6'
WORDS=33
GUARD=0xA55A


def compile_private(packets:bytes):
    if len(packets)!=4*64:
        raise ValueError('expected exactly four 64-byte original L packets')
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    originals=[packets[i*64:(i+1)*64] for i in range(4)]
    identities=[]
    rows=[]
    for i,payload in enumerate(originals):
        parsed=p.unpack(payload)
        if parsed is None or len(parsed['frames'])<1:
            raise ValueError('input L64 packet '+str(i)+' is not CRC-valid')
        identities.append(dict(seq=parsed['seq'],msg_id=parsed['msg_id'],
                               frames=len(parsed['frames']),
                               packet_sha256=hashlib.sha256(payload).hexdigest()))
        data=payload+GUARD.to_bytes(2,'little')
        words=[int.from_bytes(data[k:k+2],'little') for k in range(0,len(data),2)]
        rows.append('    {\n'+''.join(
            '        '+', '.join(f'0x{w:04x}u' for w in words[j:j+8])+',\n'
            for j in range(0,len(words),8))+'    },\n')
    source=TEMPLATE.read_text()
    if '#define FSK_NUM_FRAMES 8u' not in source:
        raise RuntimeError('template changed: frame count')
    source=source.replace('#define FSK_NUM_FRAMES 8u','#define FSK_NUM_FRAMES 4u')
    match=re.search(
        r'static const uint16_t sotto_packet_words\[8\]\[33\] = \{.*?\n\};',
        source,re.S)
    if not match:raise RuntimeError('template changed: packet data')
    array='static const uint16_t sotto_packet_words[4][33] = {\n'+''.join(rows)+'};'
    source=source[:match.start()]+array+source[match.end():]
    # Private generated C lives outside the repository's c/k6/mod directory.
    # The k6 module builder includes c/k6, not the generated file's parent.
    source=source.replace('#include "../k6_mod.h"','#include "k6_mod.h"')
    source=source.replace('#include "fsk_lab_vector.h"\n','')
    if source.count('W(R_LENGTH,0x4100u);')!=2 or 'fd_read(R_FIFO)' in source:
        raise RuntimeError('invalid finite frame template')
    from tempfile import TemporaryDirectory
    with TemporaryDirectory() as temp:
        c=Path(temp)/'private_guard33.c'
        c.write_text(source)
        code,entry=k6mod.build(str(c))
    if entry!=0 or not 0<len(code)<4096:
        raise RuntimeError('private module does not fit the slot')
    manifest=dict(
        experiment='FSK-13 private K6-mic Sotto L64+guard offline artifact',
        privacy='only hashes in public logs; C source remains private',
        original_packet_file_sha256=hashlib.sha256(packets).hexdigest(),
        source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        compiled_module_sha256=hashlib.sha256(code).hexdigest(),
        compiled_module_bytes=len(code),api_level=3,entry=entry,
        rf_frames=4,rf_words_per_frame=33,guard_word='0xA55A',
        packets=identities,
        rf_was_transmitted=False,
    )
    return source,manifest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--private-packets',type=Path,default=PRIVATE_PACKETS)
    p.add_argument('--private-source',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    a=p.parse_args()
    data=a.private_packets.read_bytes()
    if hashlib.sha256(data).hexdigest()!=EXPECTED_SHA:
        p.error('private microphone capture packet hash mismatch')
    if a.private_source.exists() or a.manifest.exists():
        p.error('refuse overwriting private module/manifest')
    source,manifest=compile_private(data)
    a.private_source.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(a.private_source,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(source)
    fd=os.open(a.manifest,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(json.dumps(manifest,indent=2)+'\n')
    print('private Sotto guard module prepared (no RF):',
          manifest['compiled_module_bytes'],'bytes',
          'module SHA',manifest['compiled_module_sha256'],
          'source SHA',manifest['source_sha256'])


if __name__=='__main__':main()
