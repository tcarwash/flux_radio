"""Bounded receive/playout uses the true pinned fixed-point Sotto decoder."""
from __future__ import annotations

import numpy as np
import pytest

from test_k6_sotto_e2e_mock import built  # reuse one expensive ARM/native fixture

from tools.k6_sotto_e2e_mock import decode_pcm
from tools.k6_sotto_rx_stream import SottoRxStream


def collect(rx):
    out=[]
    while (pcm:=rx.speaker_next()) is not None:
        assert pcm.shape == (160,) and pcm.dtype == np.dtype("<i2")
        out.append(pcm)
    return out


def test_streaming_matches_native_batch_without_session_buffer(built):
    artifact,lib=built
    compressed=artifact["reference"]
    assert len(compressed)%6==0 and len(compressed)>=48
    stream=SottoRxStream(lib,capacity_blocks=16)
    chunks=[]
    try:
        for start in range(0,len(compressed),48):
            payload=compressed[start:start+48]
            for i in range(0,len(payload),6):
                stream.feed(payload[i:i+6])
            assert stream.queued_blocks<=16
            chunks.extend(collect(stream))
        stream.finish()
        chunks.extend(collect(stream))
        assert stream.state=="finished" and stream.ctx is None
        assert stream.peak_blocks<=16 and stream.peak_blocks>=4
        assert stream.frames==len(compressed)//6
        assert stream.output_blocks==len(compressed)//3
        assert stream.played_blocks==stream.output_blocks
        assert stream.queued_blocks==0
        actual=np.concatenate(chunks)
        np.testing.assert_array_equal(actual,decode_pcm(lib,compressed))
    finally:
        stream.close()


def test_bounded_queue_overflow_fails_closed_and_drops_stale_audio(built):
    _,lib=built
    r=SottoRxStream(lib,capacity_blocks=4)
    six=b"123456"
    r.feed(six);r.feed(six);r.feed(six);r.feed(six)
    assert r.queued_blocks==4
    with pytest.raises(BufferError,match="overrun"):
        r.feed(six)
    assert r.state=="aborted" and r.ctx is None
    assert r.queued_blocks==0 and r.speaker_next() is None
    with pytest.raises(RuntimeError,match="not accepting"):
        r.feed(six)
    with pytest.raises(RuntimeError,match="not active"):
        r.finish()


def test_ptt_preempts_rx_pcm_and_decoder_state(built):
    _,lib=built
    r=SottoRxStream(lib)
    for _ in range(3):
        r.feed(b"123456")
    assert r.queued_blocks==2
    r.abort()
    assert r.ctx is None and r.state=="aborted" and r.speaker_next() is None
    r.close()
    assert r.queued_blocks==0
    assert r.output_blocks==2


def test_reject_malformed_frame_and_unsafe_capacity(built):
    _,lib=built
    for n in (0,1,2,3,129,True):
        with pytest.raises(ValueError):
            SottoRxStream(lib,capacity_blocks=n)
    r=SottoRxStream(lib)
    with pytest.raises(ValueError,match="six validated bytes"):
        r.feed(b"12345")
    assert r.ctx is None and r.speaker_next() is None
    r.close()


def test_finish_rejects_unprimed_or_undrained_stream(built):
    _,lib=built
    r=SottoRxStream(lib)
    r.feed(b"123456")
    with pytest.raises(BufferError,match="incomplete"):
        r.finish()
    assert r.state=="aborted" and r.ctx is None
    r=SottoRxStream(lib,capacity_blocks=4)
    for _ in range(4):
        r.feed(b"123456")
    with pytest.raises(BufferError,match="too full"):
        r.finish()
    assert r.ctx is None and r.queued_blocks==0
