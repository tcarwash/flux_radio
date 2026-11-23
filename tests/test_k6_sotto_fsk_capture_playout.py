"""Offline playout from *blind* physical packet reports."""
import copy
import json
from pathlib import Path
from tools.k6_sotto_fsk_capture_playout import replay

CAPTURE=Path('/home/tyler/flux-bench/tx-injection-20261122/fsk10b-l8/fsk10b-l8-blind-rx-run01.json')

def test_eight_real_received_packets_have_no_playout_holes():
    if not CAPTURE.exists():return
    a=replay(json.loads(CAPTURE.read_text()),buffer_ms=160,frame_limit=64)
    assert a['ontime_codec_frames']==64
    assert a['concealed_codec_frames']==0
    assert a['longest_concealment_ms']==0
    assert len(a['encoded_bytes'])==384
    assert a['message_id']==73


def test_replay_packet_erasure_is_visible_not_silent():
    if not CAPTURE.exists():return
    record=copy.deepcopy(json.loads(CAPTURE.read_text()))
    record['packets']=[p for p in record['packets'] if p['seq']!=3]
    a=replay(record,buffer_ms=160,frame_limit=64)
    assert a['ontime_codec_frames']==56
    assert a['concealed_codec_frames']==8
    assert a['longest_concealment_ms']==320


def test_replay_rejects_missing_initial_anchor():
    if not CAPTURE.exists():return
    record=copy.deepcopy(json.loads(CAPTURE.read_text()))
    record['packets']=[p for p in record['packets'] if p['seq']!=0]
    import pytest
    with pytest.raises(ValueError,match='Initial packet'):
        replay(record,frame_limit=64)
