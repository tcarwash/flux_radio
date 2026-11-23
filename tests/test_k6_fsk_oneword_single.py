"""Frozen stand-alone in-flight FIFO append test. Offline/no radio."""
from pathlib import Path
from tools.k6_fsk_oneword_single_bench import frozen_build,SOURCE
from tools.k6_fsk_oneword_analyze import truth


def test_source_and_compiled_image_frozen():
    assert frozen_build()['code_bytes']<4096
    source=SOURCE.read_text()
    assert '#define FSK_NUM_FRAMES 1u' in source
    assert 'ret=send_frame(api,1u);' in source
    assert 'wait_until(a,start,70u)' in source
    assert 'W(R_FIFO,word_for(frame,48u));' in source
    assert '#define FSK_MAX_TX_MS 1900u' in source
    assert 'fd_read(R_FIFO)' not in source
    assert 'R_FIFO_LEVEL' not in source


def test_exact_safe_fifo_capacity_and_distinct_tail():
    assert 48+1<=64
    payload=bytes.fromhex(truth()[1]['payload_bytes_hex'])
    assert len(payload)==98
    assert payload[-2:].hex()=='3ca5'
    assert payload[:2].hex()=='aaaa'
