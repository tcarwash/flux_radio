"""Offline validation for private microphone guard-module generator (no private bytes in Git)."""
from tools.k6_fsk_guard33_mic_private import compile_private
from tools.k6_fsk_guard33_bench import source_packets,ORIGINAL
import pytest


def sample_four_public_packets():
    packets=source_packets(ORIGINAL)[:4]
    return b''.join(
        b''.join(w.to_bytes(2,'little') for w in group)
        for group in packets)


def test_public_fixture_builds_four_packet_guarded_m0_module():
    source,info=compile_private(sample_four_public_packets())
    assert info['rf_was_transmitted'] is False
    assert info['rf_frames']==4
    assert info['rf_words_per_frame']==33
    assert info['compiled_module_bytes']<4096
    assert source.count('0xa55au')==4
    assert 'static const uint16_t sotto_packet_words[4][33]' in source
    assert '#define FSK_NUM_FRAMES 4u' in source
    assert '#define FSK_MAX_TX_MS 1900u' in source
    assert '#include "k6_mod.h"' in source
    assert len(info['packets'])==4
    assert [p['seq'] for p in info['packets']]==list(range(4))


def test_private_generator_refuses_truncated_or_invalid_data():
    with pytest.raises(ValueError):
        compile_private(b'\x00'*256)
    with pytest.raises(ValueError):
        compile_private(sample_four_public_packets()[:-1])
    corrupted=bytearray(sample_four_public_packets())
    corrupted[30]^=1
    with pytest.raises(ValueError):
        compile_private(bytes(corrupted))
