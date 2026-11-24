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


def test_maximum_bounded_session_stays_within_playout_ring(built):
    _,lib=built
    # 144 voice segments x 8 codec superframes. This does not establish
    # real-time decoding, only bounded queue behavior and exact frame count.
    rx=SottoRxStream(lib,capacity_blocks=16,max_frames=1152)
    pulled=0
    try:
        for segment in range(144):
            for i in range(8):
                rx.feed(b'\x23\x14\x9a\xbe\x6f\x80')
            while rx.speaker_next() is not None:
                pulled+=1
            assert rx.queued_blocks==0 and rx.peak_blocks<=16
        rx.finish()
        while rx.speaker_next() is not None:
            pulled+=1
        assert rx.frames==1152
        assert pulled==rx.output_blocks==2304
        assert rx.peak_blocks==16
        assert rx.ctx is None and rx.state=='finished'
    finally:
        rx.close()


def test_frame_limit_preempts_any_additional_codec_input(built):
    _,lib=built
    rx=SottoRxStream(lib,max_frames=3)
    try:
        for i in range(3):
            rx.feed(b'\x23\x14\x9a\xbe\x6f\x80')
        with pytest.raises(BufferError,match='session limit'):
            rx.feed(b'\x23\x14\x9a\xbe\x6f\x80')
        assert rx.state=='aborted' and rx.ctx is None
        assert rx.speaker_next() is None
    finally:
        rx.close()


def test_paced_decoder_is_bit_exact_with_four_pcm_blocks(built):
    from tools.k6_sotto_rx_stream import SottoRxPaced
    artifact,lib=built
    bits=artifact['reference']
    paced=SottoRxPaced(lib,capacity_frames=16)
    audio=[]
    try:
        for off in range(0,len(bits),48):
            paced.enqueue_packet(bits[off:off+48])
            audio.extend(collect(paced))
            assert paced.queued_pcm_blocks<=4
            assert paced.queued_encoded_frames<=16
        paced.end()
        audio.extend(collect(paced))
        assert paced.state=='finished'
        assert paced.peak_pcm_blocks==4
        assert paced.peak_encoded_frames<=8
        assert paced.queued_pcm_blocks==paced.queued_encoded_frames==0
        assert paced.decoder.played_blocks==paced.decoder.output_blocks
        assert paced.decoder.output_blocks==2*len(bits)//6
        np.testing.assert_array_equal(np.concatenate(audio),decode_pcm(lib,bits))
    finally:paced.close()


def test_paced_receiver_backpressure_and_ptt_abort(built):
    from tools.k6_sotto_rx_stream import SottoRxPaced
    _,lib=built
    packet=bytes(range(48))
    r=SottoRxPaced(lib,capacity_frames=8)
    r.enqueue_packet(packet)
    assert r.queued_encoded_frames==8 and r.queued_pcm_blocks==0
    with pytest.raises(BufferError,match='ring overrun'):
        r.enqueue_packet(packet)
    assert r.state=='aborted' and r.queued_encoded_frames==r.queued_pcm_blocks==0
    assert r.speaker_next() is None and r.decoder.ctx is None
    with pytest.raises(RuntimeError,match='closed'):
        r.enqueue_packet(packet)
    r=SottoRxPaced(lib)
    r.enqueue_packet(packet)
    assert r.speaker_next() is not None
    r.abort()
    assert r.speaker_next() is None and r.queued_encoded_frames==0
    assert r.decoder.ctx is None


def test_paced_rx_invalid_or_missing_end_is_fail_closed(built):
    from tools.k6_sotto_rx_stream import SottoRxPaced
    _,lib=built
    r=SottoRxPaced(lib)
    with pytest.raises(ValueError,match='validated'):
        r.enqueue_packet(b'123')
    assert r.state=='aborted' and r.decoder.ctx is None
    r=SottoRxPaced(lib)
    r.enqueue_packet(b'123456')
    with pytest.raises(ValueError,match='priming'):
        r.end()
    assert r.state=='aborted' and r.decoder.ctx is None
    r=SottoRxPaced(lib)
    r.enqueue_packet(bytes(range(48)))
    assert r.speaker_next() is not None
    # No authenticated END means no decoder flush or invented completion.
    assert r.state=='rx'
    assert len(collect(r))>=1
    assert r.speaker_next() is None
    r.abort()
    assert r.decoder.ctx is None


def test_paced_max_length_session_without_large_pcm_ring(built):
    from tools.k6_sotto_rx_stream import SottoRxPaced
    _,lib=built
    r=SottoRxPaced(lib,capacity_frames=8)
    cnt=0
    try:
        for _ in range(144):
            r.enqueue_packet(b'12345678'*6)
            cnt+=len(collect(r))
            assert r.peak_pcm_blocks<=4
            assert r.peak_encoded_frames<=8
        r.end()
        cnt+=len(collect(r))
        assert cnt==2304 and r.state=='finished'
        assert r.peak_pcm_blocks==4
        assert r.queued_pcm_blocks==r.queued_encoded_frames==0
    finally:r.close()
