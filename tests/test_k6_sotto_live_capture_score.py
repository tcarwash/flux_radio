"""No hardware access: public pinned FDV1 fixture must round-trip the scorer."""
from __future__ import annotations
import pytest
from tools.k6_sotto_live_capture_score import score_frames
from tools.k6_sotto_rf_bench import fixture


def public_frames():
    codec, expected = fixture()
    return codec, [raw + b"\x5a\xa5" for raw in expected]


def test_sotto_public_control_voice_session_is_complete():
    codec, frames = public_frames()
    got, result = score_frames(frames)
    assert got == codec
    assert result["result"] == "PASS"
    assert result["frames"] == 6
    assert result["codec_frames"] == 26
    assert result["session"]["complete"]
    assert not result["on_radio_decoder_checked"]


def test_sotto_rejects_invalid_corruption():
    _, frames = public_frames()
    bad = list(frames)
    tmp = bytearray(bad[2]); tmp[17] ^= 1; bad[2] = bytes(tmp)
    with pytest.raises(ValueError, match="no complete"):
        score_frames(bad)


def test_sotto_rejects_missing_end_and_bad_lengths():
    _, frames = public_frames()
    with pytest.raises(ValueError, match="no complete"):
        score_frames(frames[:-1])
    with pytest.raises(ValueError, match="66 bytes"):
        score_frames([frames[0][:-1]])
