#!/usr/bin/env python3
"""Create *private* K6 microphone-origin Sotto RF module fixture, offline.

The 256 voice-containing packet bytes and any C source embedding them stay
outside Git in an explicitly user-controlled private bench directory. The
committed repository holds only the generator and nonreversible SHA hashes.
This tool never touches the radio, serial/SDR, or hardware queue.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from modem import k6mod,pager

ROOT=Path(__file__).resolve().parents[1]
TEMPLATE=ROOT/'c/k6/mod/fsk_sotto_l_payload.c'
PRIVATE_BASE=Path('/home/tyler/flux-bench/micvoice-20261123')
MSG_ID=93
EXPECTED_PACKETS=4
FSK_BITS=4800


def sha(data:bytes):return hashlib.sha256(data).hexdigest()


def attest_packets(raw:bytes):
    if len(raw)!=4*64:raise ValueError('Exactly four 64-byte mic-origin packets required')
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packets=[raw[i*64:(i+1)*64] for i in range(EXPECTED_PACKETS)]
    decoded=[p.unpack(b) for b in packets]
    if any(d is None or d['msg_id']!=MSG_ID or d['seq']!=i or d['total']!=26
           or d['parity'] is not None or d['depth']!=1 for i,d in enumerate(decoded)):
        raise ValueError('Mic-origin packets lack expected 26-frame Sotto-1200 L64 CRC sequence')
    assembled=pager.Assembler(p)
    for x in packets:assembled.push(x)
    payload,good=assembled.frames(MSG_ID)
    if len(payload)!=156 or not good.all() or len(good)!=26:
        raise ValueError('Not exact 26-frame Sotto compressed speech')
    return packets,payload


def source_from_packets(packets):
    s=TEMPLATE.read_text()
    expected='static const uint16_t sotto_packet_words[2][32] = {'
    if s.count(expected)!=1 or s.count('#define FSK_NUM_FRAMES 2u')!=1:
        raise RuntimeError('The reviewed existing FSK-11 template changed')
    s=s.replace('FSK-11 real Sotto-1200 L packet RF qualification.',
                'FSK-12 PRIVATE K6 microphone-to-Sotto-1200 4-packet RF qualification.')
    s=s.replace('exactly TWO','exactly FOUR').replace('two frames sent; RX restored','four packets sent; RX restored')
    s=s.replace('FSK_NUM_FRAMES 2u','FSK_NUM_FRAMES 4u')
    # K6 public ABI includes are rooted in the repo source tree. Private
    # sources are intentionally generated OUTSIDE that tree. The test
    # vector header is unused by this fixed real-Sotto payload.
    s=s.replace('#include "../k6_mod.h"','#include "k6_mod.h"')
    s=s.replace('#include "fsk_lab_vector.h"','')
    start=s.index(expected)
    stop=s.index('static uint16_t word_for',start)
    buf=['static const uint16_t sotto_packet_words[4][32] = {']
    for pkt in packets:
        words=[int.from_bytes(pkt[j:j+2],'little') for j in range(0,64,2)]
        buf.append('    {')
        for k in range(0,32,8):
            buf.append('        '+', '.join(f'0x{x:04x}u' for x in words[k:k+8])+',')
        buf.append('    },')
    buf.append('};')
    s=s[:start]+'\n'.join(buf)+'\n'+s[stop:]
    if ('#define FSK_BOUNDARY_ENABLE 0' not in s or
        '#define FSK_MAX_TX_MS 1900u' not in s or
        'W(R_LENGTH,0x3f00u);' not in s):
        raise RuntimeError('Template safety parameters changed')
    return s


def make_fixture(packet_file:Path,private_dir:Path):
    target=private_dir.resolve()
    if target==ROOT.resolve() or ROOT.resolve() in target.parents or PRIVATE_BASE.resolve() not in target.parents:
        raise ValueError('Private fixture must live under the local /home/tyler/flux-bench/micvoice-20261123/ folder, never Git')
    packets,compressed=attest_packets(packet_file.read_bytes())
    inert=source_from_packets(packets)
    armed=(inert.replace('#define FSK_BOUNDARY_ENABLE 0','#define FSK_BOUNDARY_ENABLE 1')
                .replace('#define FSK_BOUNDARY_FIXTURE_ACK 0','#define FSK_BOUNDARY_FIXTURE_ACK 1')
                .replace('#define FSK_BOUNDARY_WATCHDOG_ACK 0','#define FSK_BOUNDARY_WATCHDOG_ACK 1'))
    target.mkdir(mode=0o700,parents=False,exist_ok=False)
    source=target/'fsk12-k6mic-armed.c'
    inertfile=target/'fsk12-k6mic-inert.c'
    for path,data in [(source,armed),(inertfile,inert)]:
        path.write_text(data)
        path.chmod(stat.S_IRUSR|stat.S_IWUSR)
    executable,entry=k6mod.build(str(source))
    if entry!=0 or not 0<len(executable)<4096 or k6mod.source_level(str(source))!=3:
        raise ValueError('Frozen FX module build result not valid')
    metadata=dict(stage='PRIVATE SOURCE; NO RF RUN',
        packet_file_sha256=sha(packet_file.read_bytes()),
        mic_sotto_compressed_sha256=sha(compressed),
        per_packet_sha256=[sha(x) for x in packets],
        packet_count=4,packet_len_bytes=64,message_id=MSG_ID,
        codec_superframes=26,voice_duration_s=1.04,
        programmed_modem_baud=FSK_BITS,
        inert_source_sha256=sha(inert.encode()),
        armed_source_sha256=sha(armed.encode()),
        armed_compiled_code_sha256=sha(executable),
        armed_compiled_code_bytes=len(executable),
        template_sha256=sha(TEMPLATE.read_bytes()),
        no_hardware_access=True,
        private_fixture_path=str(target))
    p=target/'fsk12-private-fixture-meta.json'
    p.write_text(json.dumps(metadata,indent=2)+'\n')
    p.chmod(stat.S_IRUSR|stat.S_IWUSR)
    return metadata


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packet-file',required=True,type=Path)
    p.add_argument('--private-dir',required=True,type=Path)
    a=p.parse_args()
    result=make_fixture(a.packet_file,a.private_dir)
    print(json.dumps({k:v for k,v in result.items() if 'path' not in k},indent=2))
    print('PRIVATE fixture written; no hardware access; do not commit its C files')

if __name__=='__main__':main()
