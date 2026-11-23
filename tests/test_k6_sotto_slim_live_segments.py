"""Incremental slim Sotto streaming via self-contained existing L64 packets."""
from pathlib import Path
import pytest
from modem import pager
from tools.k6_sotto_slim_live_segments import build_segments,times_from_csv

STREAM=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/mic-incremental-slim.bit')
TIMELINE=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/mic-incremental-timeline.csv')


def make_frames(n):return bytes((i*19+5)&255 for i in range(6*n))


def test_incremental_segments_need_no_final_call_length_before_first_packet():
    src=make_frames(26)
    t=[.09+.04*i for i in range(24)]+[1.04,1.04]
    packets,result=build_segments(src,t,start_message_id=112,playout_buffer_ms=160)
    assert len(packets)==4 and result['codec_frames']==26
    assert result['chunks']==4
    assert result['new_message_per_segment'] and result['source_total_not_in_first_header']
    assert result['reassembled_compressed_exact']
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    decoded=[p.unpack(raw) for raw in packets]
    assert [q['msg_id'] for q in decoded]==[112,113,114,115]
    assert [q['total'] for q in decoded]==[8,8,8,2]
    assert [q['seq'] for q in decoded]==[0,0,0,0]
    assert all(q is not None for q in decoded)
    assert result['ontime_frames']==26 and result['late_frames']==0
    assert result['first_playout_s']==.681
    assert abs(result['max_tx_queue_wait_s']-.121)<.002
    bad=bytearray(packets[0]);bad[17]^=0x10
    assert p.unpack(bad) is None


def test_exact_mult_of_eight_has_no_end_of_talk_flag():
    src=make_frames(16)
    packets,result=build_segments(src,[.09+.04*j for j in range(16)],start_message_id=7)
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    assert len(packets)==2
    assert all(p.unpack(x)['total']==8 for x in packets)
    assert any('end-of-PTT' in t for t in result['limits'])


def test_invalid_stream_shape_timestamps_or_message_wrap_refused():
    with pytest.raises(ValueError,match='Incomplete'):
        build_segments(b'\x01\x02', [.04])
    with pytest.raises(ValueError,match='Missing'):
        build_segments(make_frames(2),[.01])
    with pytest.raises(ValueError,match='nondecreasing'):
        build_segments(make_frames(2),[.20,.10])
    with pytest.raises(ValueError,match='wrap'):
        build_segments(make_frames(9),[.1+.04*i for i in range(9)],start_message_id=255)
    with pytest.raises(ValueError,match='Negative'):
        build_segments(make_frames(8),[.1+.04*i for i in range(8)],playout_buffer_ms=-1)


def test_severe_packet_generation_overrun_is_visible():
    # Artificially rush 40ms frames to release multiple chunks at once.
    packets,r=build_segments(make_frames(40),[.01*j for j in range(1,41)],
                            start_message_id=1,playout_buffer_ms=0)
    assert len(packets)==5
    assert r['max_tx_queue_wait_s']>0
    # Encoding faster than real-time can create a TX queue but does not
    # itself imply playback underrun: voice has arrived ahead of deadline.
    assert r['late_frames']==0


def test_encoder_stall_can_cause_late_playback():
    raw=make_frames(40)
    times=[.09+.04*i+(.5 if i>=16 else 0.) for i in range(40)]
    _,r=build_segments(raw,times,start_message_id=1,playout_buffer_ms=0)
    assert r['late_frames']>0


def test_real_k6_mic_host_incremental_timeline():
    if not STREAM.exists() or not TIMELINE.exists():pytest.skip('Mic-derived streaming fixture not present')
    raw=STREAM.read_bytes();t=times_from_csv(TIMELINE,len(raw)//6)
    assert len(t)==26 and t[0]==.09 and t[-1]==1.04
    packets,r=build_segments(raw,t,start_message_id=112,playout_buffer_ms=160)
    assert r['chunks']==4 and r['codec_frames']==26
    assert r['ontime_frames']==26 and r['late_frames']==0
    assert [c['source_frame_count'] for c in r['chunk_data']]==[8,8,8,2]
    assert [c['modeled_tx_start_s'] for c in r['chunk_data']]==[.37,.69,1.01,1.161]
    assert r['first_playout_s']==.681
