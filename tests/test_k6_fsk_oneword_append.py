"""FSK-09S-C one-word RF FIFO append safety/bit-order offline regressions."""
from pathlib import Path
import hashlib
import numpy as np
from tools.k6_fsk_oneword_bench import frozen_build,SOURCE
from tools.k6_fsk_oneword_analyze import truth,WORDS

ROOT=Path(__file__).resolve().parents[1]


def test_exact_armed_code_frozen_and_inert_default():
    assert frozen_build()['code_bytes']<4096
    a=SOURCE.read_text()
    i=(ROOT/'c/k6/mod/fsk_oneword_append.c').read_text()
    assert '#define FSK_BOUNDARY_ENABLE 0' in i
    assert '#define FSK_BOUNDARY_ENABLE 1' in a
    assert '#define FSK_BOUNDARY_WATCHDOG_ACK 1' in a
    assert '#define FSK_BOUNDARY_FIXTURE_ACK 1' in a
    assert a.count('W(R_LEN,0x6100u);')==0
    assert a.count('W(R_LENGTH,0x6100u);')==2
    assert a.count('W(R_FIFO,word_for(frame,48u));')==1
    assert '#define FSK_MAX_TX_MS 1900u' in a
    assert '#define FSK_FRAME_TIMEOUT_MS 700u' in a


def test_single_append_never_needs_unverified_occupancy():
    max_written=48+1
    demonstrated_preload=64
    assert max_written<demonstrated_preload
    assert max_written==WORDS
    s=SOURCE.read_text()
    assert 'wait_until(a,start,70u)' in s
    assert 'if(a->fd_read(R_STATUS)&1u)return -18;' in s
    assert 'for(i=0;i<(frame ? FSK_WORDS-1u : FSK_WORDS);i++)' in s
    assert 'W(R_FIFO,word_for(frame,48u));' in s
    assert 'R_FIFO_LEVEL' not in s
    assert '#define R_IRQ ' not in s
    assert 'fd_read(R_FIFO)' not in s


def test_two_distinct_49word_payloads_and_heldout_markers():
    a,b=truth()
    assert a['payload_words']==b['payload_words']==49
    assert a['payload_bits']==b['payload_bits']==784
    assert a['native_baud']==b['native_baud']==4800
    # The updated independent decoder uses only public preamble/sync,
    # not an easily ambiguous periodic prefix from the payload.
    assert b"\x07\x6b\x3e\x36"==bytes.fromhex("076b3e36")
    assert a['payload_bytes_hex'][-4:]=='a53c'
    assert b['payload_bytes_hex'][-4:]=='3ca5'
    assert a['payload_sha256_le_words']!=b['payload_sha256_le_words']
    assert a['payload_bytes_hex'][:64]!=b['payload_bytes_hex'][:64]
    for p in (a,b):
        packed=bytes.fromhex(p['payload_bytes_hex'])
        assert len(packed)==98
        assert hashlib.sha256(packed).hexdigest()==p['payload_sha256_le_words']
