"""Zero-hardware guarded-Sotto playout tests with independent CRC/parsing.

Synthetic data is public deterministic random payload. The optional private
physical capture test never prints or commits the recorded microphone bytes.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from modem import pager
from tools.k6_dv_guard33_playout import replay, write_private
from tools.k6_fsk_guardword_rx import select_l64

PROFILE = pager.profile("L", "1200", depth=1, parity=False, short_last=True)
CAPTURE = Path("/home/tyler/flux-bench/micvoice-20261123/fsk14-mic-guard-private-20261123/fsk14-mic-guard-analysis-run01.json")
SOURCE = Path("/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-sotto1200.bit")


def record(voice: bytes, message: int, *, seq: int = 0, total: int | None = None,
           timestamp: float = 1.0, shifted: bool = False) -> dict:
    if total is None:
        total = len(voice) // 6
    if seq == 0 and total == len(voice) // 6:
        packet = PROFILE.pack(voice, msg_id=message)[0]
    else:
        # Model a multi-sequence legacy message without using TX truth in RX.
        packet = PROFILE.pack(voice, msg_id=message)[seq]
    raw = packet + b"\x5a\xa5"
    physical = (b"\xf8\x00" + raw)[:66] if shifted else raw
    selected = select_l64(physical)
    assert selected["valid"] and selected["offset_bytes"] == (2 if shifted else 0)
    return dict(selected, payload_start_s=timestamp, selected_baud=4800, sync_errors=0)


def stream_report(parts: list[bytes], *, start: int = 112) -> dict:
    frames = [record(chunk, start + i, timestamp=1 + i * .32,
                     shifted=bool(i % 2)) for i, chunk in enumerate(parts)]
    return dict(frames=frames)


def test_replay_4_real_shape_segments_normal_and_prefix_shift():
    chunks = [bytes(((i * 48 + j) % 251 for j in range(n)))
              for i, n in enumerate((48, 48, 48, 12))]
    plan = replay(stream_report(chunks), mode="stream", start_id=112,
                  end_id=115, buffer_ms=160)
    assert plan["encoded_bytes"] == b"".join(chunks)
    assert plan["total_codec_frames"] == 26
    assert plan["on_time_codec_frames"] == 26
    assert plan["missing_packets"] == 0
    assert plan["concealed_codec_frames"] == 0
    assert plan["received_packets"] == 4
    assert plan["physical_frame_airtime_ms"] == 110


def test_legacy_26_frames_with_common_message_id():
    voice = bytes(i % 251 for i in range(26 * 6))
    pk = PROFILE.pack(voice, msg_id=93)
    rows = []
    for i, packet in enumerate(pk):
        physical = packet + b"\x5a\xa5"
        if i % 2 == 0:
            physical = (b"\xf8\x00" + physical)[:66]
        rows.append(dict(select_l64(physical), payload_start_s=1+i*.152,
                         selected_baud=4800, sync_errors=0))
    result = replay(dict(frames=rows), mode="legacy", start_id=93)
    assert result["encoded_bytes"] == voice
    assert result["total_codec_frames"] == 26
    assert result["concealed_codec_frames"] == 0


def test_stream_duplicate_and_out_of_order_does_not_reorder_audio():
    parts = [bytes([i+1])*48 for i in range(3)]
    frames = stream_report(parts)["frames"]
    late_duplicate = dict(frames[0], payload_start_s=2.0)
    row = dict(frames=[frames[2], late_duplicate, frames[1], frames[0]])
    r = replay(row, mode="stream", start_id=112, end_id=114)
    assert r["encoded_bytes"] == b"".join(parts)
    assert r["received_packets"] == 3
    assert r["on_time_codec_frames"] == 24


def test_conflicting_crc_good_duplicate_refused():
    report = stream_report([b"a"*48])
    report["frames"].append(record(b"b"*48, 112))
    with pytest.raises(ValueError, match="Conflicting"):
        replay(report, mode="stream", start_id=112, end_id=112)


def test_missing_mid_stream_segment_produces_exact_320ms_concealment():
    parts = [bytes([i+1])*48 for i in range(3)]
    report = stream_report(parts)
    report["frames"].pop(1)
    result = replay(report, mode="stream", start_id=112, end_id=114)
    assert result["missing_packets"] == 1
    assert result["concealed_codec_frames"] == 8
    assert result["longest_concealment_ms"] == 320
    assert result["encoded_bytes"] == parts[0] + bytes(48) + parts[2]
    assert result["playout_mask"] == bytes([1]*8 + [0]*8 + [1]*8)


def test_late_stream_packet_concealed_by_slot_deadlines():
    parts = [b"a"*48, b"b"*48]
    row = stream_report(parts)
    row["frames"][1]["payload_start_s"] = 2.5
    result = replay(row, mode="stream", start_id=112, end_id=113, buffer_ms=0)
    assert result["late_codec_frames"] == 8
    assert result["concealed_codec_frames"] == 8


def test_missing_tail_requires_external_frame_count():
    row = stream_report([b"a"*48, b"z"*12])
    row["frames"].pop()
    with pytest.raises(ValueError, match="Missing final"):
        replay(row, mode="stream", start_id=112, end_id=113)
    plan = replay(row, mode="stream", start_id=112, end_id=113, last_frames=2)
    assert plan["total_codec_frames"] == 10
    assert plan["concealed_codec_frames"] == 2


def test_start_missing_refused_in_both_modes():
    row = stream_report([b"a"*48, b"b"*48])
    row["frames"].pop(0)
    with pytest.raises(ValueError, match="No first"):
        replay(row, mode="stream", start_id=112, end_id=113)
    with pytest.raises(ValueError, match="No first"):
        replay(dict(frames=[]), mode="legacy", start_id=93)


def test_nonwrapped_epoch_and_invalid_end_conditions():
    row = stream_report([b"a"*48], start=255)
    with pytest.raises(ValueError, match="non-wrapping"):
        replay(row, mode="stream", start_id=255, end_id=0)
    with pytest.raises(ValueError, match="non-wrapping"):
        replay(row, mode="stream", start_id=255, end_id=256)
    with pytest.raises(ValueError, match="Stream needs"):
        replay(row, mode="stream", start_id=255)


def test_bad_crc_and_untrusted_header_flags_not_accepted():
    row = stream_report([b"a"*48])
    old = copy.deepcopy(row["frames"][0])
    row["frames"][0]["packet_hex"] = "00"*64
    with pytest.raises(ValueError, match="Invalid CRC"):
        replay(row, mode="stream", start_id=112, end_id=112)
    row["frames"][0] = dict(old, msg_id=113)
    with pytest.raises(ValueError, match="Invalid CRC"):
        replay(row, mode="stream", start_id=112, end_id=112)
    row["frames"][0] = dict(old, valid=False)
    with pytest.raises(ValueError, match="No first"):
        replay(row, mode="stream", start_id=112, end_id=112)


def test_partial_before_last_and_mismatched_tail_refused():
    report = stream_report([b"a"*12, b"b"*12])
    with pytest.raises(ValueError, match="Partial segment"):
        replay(report, mode="stream", start_id=112, end_id=113)
    row = stream_report([b"a"*48, b"b"*12])
    with pytest.raises(ValueError, match="contradicts"):
        replay(row, mode="stream", start_id=112, end_id=113, last_frames=8)


def test_private_write_enforces_0700_and_exclusive_outputs(tmp_path):
    plan = replay(stream_report([b"a"*48]), mode="stream", start_id=112, end_id=112)
    tmp_path.chmod(0o750)
    with pytest.raises(PermissionError):
        write_private(plan, tmp_path, "trial")
    tmp_path.chmod(0o700)
    summary = write_private(plan, tmp_path, "trial")
    assert summary["total_codec_frames"] == 8
    for suffix in (".bit", ".good", ".json"):
        p = tmp_path / ("trial" + suffix)
        assert p.stat().st_mode & 0o777 == 0o600
    assert "encoded_bytes" not in json.loads((tmp_path/"trial.json").read_text())
    with pytest.raises(FileExistsError):
        write_private(plan, tmp_path, "trial")


@pytest.mark.skipif(not CAPTURE.exists() or not SOURCE.exists(),
                    reason="private original RF packet evidence not present")
def test_actual_4_of_4_k6_microphone_guarded_rf_host_playout():
    report = json.loads(CAPTURE.read_text())
    result = replay(report, mode="legacy", start_id=93, buffer_ms=160)
    assert result["encoded_bytes"] == SOURCE.read_bytes()
    assert result["total_codec_frames"] == 26
    assert result["on_time_codec_frames"] == 26
    assert result["concealed_codec_frames"] == 0
    assert result["received_packets"] == 4
    assert result["ignored_physical_observations"] == 0

@pytest.mark.skipif(not SOURCE.exists(),
                    reason="private recorded K6 microphone Sotto stream unavailable")
def test_real_c_stream_queue_to_guard_receiver_to_playout(tmp_path):
    """Actual C queue -> synthetic guarded physical RF -> CRC RX -> playout.

    The 0x00F8 insertion is injected based on FSK-13/14's physical fault, but
    this test has NO new RF transmission, ADC sample acquisition or K6 RX.
    """
    import ctypes
    import subprocess
    root = Path(__file__).resolve().parents[1]
    src = tmp_path / "live_queue.c"
    src.write_text("""
#include <stdint.h>
#include "dv_guard33_stream.h"
static dv_guard33_stream_t stream;
int begin(void) { return dv_stream_begin(&stream, 112); }
int push(const uint8_t *f) { return dv_stream_push(&stream, f); }
int finish(void) { return dv_stream_finish(&stream); }
int ack(void) { return dv_stream_ack(&stream); }
int peek(uint8_t *p) {
    const uint16_t *w=dv_stream_peek(&stream);
    if (!w) return 0;
    for (int i=0;i<33;i++) {
        p[i*2] = (uint8_t)(w[i] & 255);
        p[i*2+1] = (uint8_t)(w[i] >> 8);
    }
    return 66;
}
""")
    libfile = tmp_path / "q.so"
    subprocess.run(["clang", "-O2", "-std=c11", "-Wall", "-Wextra", "-Werror",
                    "-fPIC", "-shared", str(src),
                    "-I" + str(root / "c/k6/mod"), "-o", str(libfile)],
                   check=True, capture_output=True)
    lib = ctypes.CDLL(str(libfile))
    lib.push.argtypes = [ctypes.POINTER(ctypes.c_uint8)]
    lib.peek.argtypes = [ctypes.POINTER(ctypes.c_uint8)]
    assert lib.begin() == 0
    voice = SOURCE.read_bytes()
    assert len(voice) == 156
    physical = []
    for i in range(26):
        frame = (ctypes.c_uint8 * 6).from_buffer_copy(voice[i*6:i*6+6])
        status = lib.push(frame)
        assert status == (1 if (i+1) % 8 == 0 else 0)
        if status == 1:
            out = (ctypes.c_uint8 * 66)()
            assert lib.peek(out) == 66
            physical.append(bytes(out))
            assert lib.ack() == 0
    assert lib.finish() == 0
    out = (ctypes.c_uint8 * 66)()
    assert lib.peek(out) == 66
    physical.append(bytes(out))
    assert lib.ack() == 0
    assert lib.peek(out) == 0
    assert len(physical) == 4
    rows = []
    for i, wire in enumerate(physical):
        assert wire[-2:] == bytes([0x5a, 0xa5])
        # The alternate-frame leading-word fault was seen physically.
        displaced = (bytes([0xf8, 0x00]) + wire)[:66] if i % 2 == 0 else wire
        selected = select_l64(displaced)
        assert selected["valid"] and selected["msg_id"] == 112+i
        rows.append(dict(selected, payload_start_s=1.0 + 0.32*i,
                         selected_baud=4800, sync_errors=0))
    result = replay(dict(frames=rows), mode="stream", start_id=112,
                    end_id=115, last_frames=2, buffer_ms=160)
    assert result["encoded_bytes"] == voice
    assert result["on_time_codec_frames"] == 26
    assert result["concealed_codec_frames"] == 0
    assert result["missing_packets"] == 0
