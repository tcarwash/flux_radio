#!/usr/bin/env python3
"""FSK-13: guarded 8-packet, 33-word/packet native FSK L64+guard RF test.

One bounded dummy-load bench only. The sacrificial final FIFO word means
an intermittent *extra leading word* may displace the guard instead of the
Sotto CRC, without altering the 64-byte L packet format.
"""
from __future__ import annotations
import argparse
import hashlib
import re
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from modem import k6mod,pager
from k6_fsk_boundary_bench import run_transaction

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/fsk_guard33_sotto_armed.c'
SOURCE_SHA='332ee375001bccf688b30db390dee12311b132c4b63d53ff7d06f0f831c2af0a'
CODE_SHA='cd4dd68674658cdf5a9b44cc40af32f2679d9f1c09580017beebd4def1a86295'
ORIGINAL=ROOT/'c/k6/mod/fsk_sotto_l8_burst_armed.c'
PACKETS=8
WORDS=33


def source_packets(src:Path):
    raw=src.read_text()
    array=re.search(
        r'static const uint16_t sotto_packet_words\[8\]\[(?:32|33)\]\s*=\s*\{(.*?)\n\};',
        raw,re.S)
    if not array:raise ValueError('missing fixed source packet array')
    groups=re.findall(r'\{(.*?)\}',array.group(1),re.S)
    if len(groups)!=8:raise ValueError('unexpected packet count')
    out=[]
    for g in groups:
        words=[int(w,16) for w in re.findall(r'0x([0-9a-fA-F]{1,4})u',g)]
        out.append(words)
    return out


def frozen_build():
    b=SOURCE.read_bytes()
    if hashlib.sha256(b).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('FSK-13 source changed; refuse TX')
    s=b.decode()
    for marker in ('#define FSK_BOUNDARY_ENABLE 1',
                   '#define FSK_BOUNDARY_FIXTURE_ACK 1',
                   '#define FSK_BOUNDARY_WATCHDOG_ACK 1',
                   '#define FSK_NUM_FRAMES 8u',
                   '#define FSK_WORDS 33u',
                   '#define FSK_MAX_TX_MS 1900u',
                   '#define FSK_FRAME_TIMEOUT_MS 700u',
                   'W(R_LENGTH,0x4100u);',
                   'W(R_CONTROL,0x80f8u);',
                   'W(R_CONTROL,0x08f8u);'):
        if marker not in s:raise RuntimeError('missing safety invariant '+marker)
    if s.count('W(R_LENGTH,0x4100u);')!=2 or 'fd_read(R_FIFO)' in s:
        raise RuntimeError('invalid FIFO setup')
    orig=source_packets(ORIGINAL)
    padded=source_packets(SOURCE)
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    if len(orig)!=PACKETS or len(padded)!=PACKETS:
        raise RuntimeError('unexpected packet count')
    for i,(old,new) in enumerate(zip(orig,padded)):
        if len(old)!=32 or new!=old+[0xA55A]:
            raise RuntimeError('packet words changed at '+str(i))
        packet=b''.join(w.to_bytes(2,'little') for w in old)
        if profile.unpack(packet) is None:
            raise RuntimeError('invalid L64 Sotto packet '+str(i))
    code,entry=k6mod.build(str(SOURCE))
    if len(code)!=1478 or entry!=0 or k6mod.source_level(str(SOURCE))!=3 or hashlib.sha256(code).hexdigest()!=CODE_SHA:
        raise RuntimeError('compiled FSK-13 mismatch; refuse TX')
    return dict(source_sha256=SOURCE_SHA,code_sha256=CODE_SHA,code_bytes=len(code),
                packets=PACKETS,physical_words_each=WORDS,
                expected_packet_sha256=[hashlib.sha256(
                    b''.join(w.to_bytes(2,'little') for w in old)).hexdigest()
                    for old in orig])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',required=True,type=Path)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    p.add_argument('--operator-lease-confirmed',action='store_true')
    a=p.parse_args()
    if not a.dummy_load_confirmed or not a.operator_lease_confirmed:
        p.error('Requires dummy-load confirmation and an independently acknowledged exclusive hardware lease')
    print('Frozen FSK-13 guarded DV transport',frozen_build(),flush=True)
    report=run_transaction(
        a.port,a.prefix,SOURCE,
        a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
        armed=True,expected_result=PACKETS*WORDS,
        experiment='FSK-13: eight Sotto L64 packets in 33-word frame with sacrificial guard word',
        watchdog_ms=1900)
    if not report['transmission_confirmed_by_firmware']:
        raise RuntimeError('Unexpected module return; do not repeat')
    print('FSK-13 session complete; original signed slot and VFOs restored',flush=True)


if __name__=='__main__':
    main()
