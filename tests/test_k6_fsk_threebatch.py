"""FSK-09S-E three distinct timed append batches, no hardware access."""
from pathlib import Path
from tools.k6_fsk_threebatch_bench import frozen_build,SOURCE
from tools.k6_fsk_lab_vector import make_probe_vector
from modem import k6mod

ROOT=Path(__file__).resolve().parents[1]


def test_gated_compilation_exact():
    img=frozen_build()
    assert 0<img['compiled_code_bytes']<4096
    s=SOURCE.read_text()
    assert '#define FSK_NUM_FRAMES 1u' in s
    assert '#define FSK_WORDS 60u' in s
    assert '#define FSK_MAX_TX_MS 1900u' in s
    assert s.count('W(R_LENGTH,0x7700u);')==2
    assert 'ret=send_frame(api,1u);' in s
    assert '#define FSK_BOUNDARY_ENABLE 0' in (ROOT/'c/k6/mod/fsk_threebatch_append.c').read_text()


def test_total_written_never_exceeds_previously_proven_fifo():
    s=SOURCE.read_text()
    assert 48+3*4==60<64
    assert 'for(i=0;i<48u;i++)' in s
    assert 'for(batch=0;batch<3u;batch++)' in s
    assert 'for(j=0;j<4u;j++)' in s
    assert 'wait_until(a,start,70u+25u*batch)' in s
    assert 'W(R_FIFO,word_for(frame,48u+4u*batch+j));' in s
    assert 'fd_read(R_FIFO)' not in s
    assert 'R_FIFO_LEVEL' not in s


def test_60word_fifo_vector_has_unique_tail():
    w,_=make_probe_vector(word_count=60,seed=0xACE1)
    assert len(w)==60
    assert len(set(w[48:]))>10
    assert len(set(w[:48]))>6
