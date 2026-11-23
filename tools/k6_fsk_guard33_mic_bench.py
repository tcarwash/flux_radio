#!/usr/bin/env python3
"""FSK-14 private K6-microphone Sotto L64 + sacrificial guard RF trial.

One prospective finite transmission, *separate* from FSK-13's public-voice
fixture and lease. The real K6 microphone encoded packet payload and generated
C source stay PRIVATE. Public source and code SHA-256s are pinned here;
no audio bytes are printed or committed.

Requires operator-approved exclusive queue lease and confirmed dummy load.
The shared signed-slot transaction holds canonical serial_lock, verifies
build and operator VFO baseline and restores original slot30 + both VFOs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from modem import k6mod,pager
from k6_fsk_boundary_bench import run_transaction

PRIVATE=Path("/home/tyler/flux-bench/micvoice-20261123/fsk13-mic-guard-private-20261123")
SOURCE=PRIVATE/"guard33_k6mic_armed.c"
MANIFEST=PRIVATE/"guard33_k6mic_manifest.json"
PACKETS=Path("/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-l64-packets.bin")
SOURCE_SHA="fc5e27797dbbd60a564a168fc848f223c668c263e307a4b72166aa24c7d1bb02"
CODE_SHA="a8ad766185df22f0731ebec245bc9a274d5c908a0d1f96bc7e59bac48c536dee"
PACKET_SHA="d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6"
N_FRAMES=4
WORDS=33


def frozen_build():
    if hashlib.sha256(SOURCE.read_bytes()).hexdigest()!=SOURCE_SHA:
        raise RuntimeError('Private K6 mic C hash mismatch; refuse RF')
    src=SOURCE.read_text()
    expected=("#define FSK_BOUNDARY_ENABLE 1",
              "#define FSK_BOUNDARY_FIXTURE_ACK 1",
              "#define FSK_BOUNDARY_WATCHDOG_ACK 1",
              "#define FSK_NUM_FRAMES 4u",
              "#define FSK_WORDS 33u",
              "#define FSK_MAX_TX_MS 1900u",
              "#define FSK_FRAME_TIMEOUT_MS 700u",
              "W(R_LENGTH,0x4100u);",
              "W(R_CONTROL,0x80f8u);",
              "W(R_CONTROL,0x08f8u);")
    for check in expected:
        if check not in src:raise RuntimeError('Unsafe private module: '+check)
    if src.count('W(R_LENGTH,0x4100u);')!=2 or "fd_read(R_FIFO)" in src:
        raise RuntimeError('Unexpected FIFO source')
    code,entry=k6mod.build(str(SOURCE))
    if len(code)!=1206 or entry!=0 or k6mod.source_level(str(SOURCE))!=3 or hashlib.sha256(code).hexdigest()!=CODE_SHA:
        raise RuntimeError('Generated code hash/size mismatch; refuse RF')

    raw=PACKETS.read_bytes()
    if len(raw)!=4*64 or hashlib.sha256(raw).hexdigest()!=PACKET_SHA:
        raise RuntimeError('K6 microphone packet archive mismatch')
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    actual=[raw[i*64:(i+1)*64] for i in range(N_FRAMES)]
    pkts=[profile.unpack(pkt) for pkt in actual]
    if any(pkt is None or pkt['seq']!=i or pkt['msg_id']!=93
           for i,pkt in enumerate(pkts)):
        raise RuntimeError('Original K6 mic Sotto packet header/CRC mismatch')
    manifest=json.loads(MANIFEST.read_text())
    if (manifest['source_sha256']!=SOURCE_SHA or
        manifest['compiled_module_sha256']!=CODE_SHA or
        manifest['original_packet_file_sha256']!=PACKET_SHA or
        manifest['rf_frames']!=4 or manifest['rf_words_per_frame']!=33):
        raise RuntimeError('Private hash-only manifest mismatch')
    expected_hashes=[hashlib.sha256(pkt).hexdigest() for pkt in actual]
    if expected_hashes != [pkt['packet_sha256'] for pkt in manifest['packets']]:
        raise RuntimeError('Private packet hashes do not match manifest')
    return dict(source_sha256=SOURCE_SHA,code_sha256=CODE_SHA,
                packet_archive_sha256=PACKET_SHA,
                compiled_module_bytes=len(code),
                packet_count=4,words_per_frame=WORDS,
                expected_packet_hashes=expected_hashes)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefix",type=Path,required=True)
    p.add_argument("--port",default="/dev/ttyUSB0")
    p.add_argument("--dummy-load-confirmed",action="store_true")
    p.add_argument("--operator-lease-confirmed",action="store_true")
    a=p.parse_args()
    if not a.dummy_load_confirmed or not a.operator_lease_confirmed:
        p.error('requires exclusive operator-approved queue lease and confirmed dummy load')
    if not a.prefix.parent.exists() or a.prefix.parent.stat().st_mode & 0o077:
        p.error('private microphone capture directory must exist and deny group/other access')
    result=frozen_build()
    # Do not dump actual packet bytes, C source or voice to public logs.
    print('FSK-14 frozen microphone L64+guard',result,flush=True)
    report=run_transaction(
        a.port,a.prefix,SOURCE,
        a.prefix.with_name(a.prefix.name+'-slot30-before.k6m'),
        armed=True,expected_result=N_FRAMES*WORDS,
        experiment='FSK-14: real-K6-microphone Sotto packet RF using 33-word guarded PHY',
        watchdog_ms=1900)
    if not report['transmission_confirmed_by_firmware']:
        raise RuntimeError('unexpected RF module status; no automatic retry')
    print('FSK-14 guarded microphone RF completed; original signed slot/VFO restored',flush=True)


if __name__=="__main__":
    main()
