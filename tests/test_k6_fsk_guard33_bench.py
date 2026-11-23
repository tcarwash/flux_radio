"""Frozen eight-packet FSK L64+one-word guard bench, offline preflight only."""
import subprocess
from pathlib import Path
from tools.k6_fsk_guard33_bench import frozen_build,SOURCE,ORIGINAL,source_packets
from tools.k6_fsk_guardword_rx import select_l64
from modem import pager

ROOT=Path(__file__).resolve().parents[1]
INERT=ROOT/'c/k6/mod/fsk_guard33_sotto.c'


def test_frozen_packet_source_guard_preserves_original_l64():
    info=frozen_build()
    assert info['code_bytes']==1478
    assert info['packets']==8
    old=source_packets(ORIGINAL)
    new=source_packets(SOURCE)
    assert all(len(o)==32 and len(n)==33 and n[-1]==0xA55A
               for o,n in zip(old,new))
    assert all(o==n[:32] for o,n in zip(old,new))
    for i,words in enumerate(old):
        packet=b''.join(w.to_bytes(2,'little') for w in words)
        assert select_l64(packet+bytes.fromhex('5aa5'))['valid']
        assert select_l64(bytes.fromhex('f800')+packet)['valid']


def test_inert_default_and_control_invariants():
    s=INERT.read_text()
    assert '#define FSK_BOUNDARY_ENABLE 0' in s
    assert '#define FSK_BOUNDARY_FIXTURE_ACK 0' in s
    assert '#define FSK_BOUNDARY_WATCHDOG_ACK 0' in s
    armed=SOURCE.read_text()
    assert armed.count('W(R_LENGTH,0x4100u);')==2
    assert 'W(R_CONTROL,0x80f8u);' in armed
    assert 'W(R_CONTROL,0x08f8u);' in armed
    assert 'W(R_CONTROL,0x00f8u);' in armed
    assert 'for(i=0;i<FSK_WORDS;i++)' in armed
    assert '#define FSK_MAX_TX_MS 1900u' in armed
    assert 'fd_read(R_FIFO)' not in armed


def test_requires_a_explicit_physical_lease_and_dummy_load():
    p=subprocess.run(['python3','tools/k6_fsk_guard33_bench.py',
                      '--prefix','/tmp/inert-guard33-test'],
                     cwd=ROOT,capture_output=True,text=True,timeout=10)
    assert p.returncode!=0
    assert 'exclusive hardware lease' in p.stderr.lower()


def test_all_original_public_packets_crc_pass():
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    for original in source_packets(ORIGINAL):
        packet=b''.join(int(w).to_bytes(2,'little') for w in original)
        assert profile.unpack(packet) is not None
