#!/usr/bin/env python3
"""Offline, bounded playout planner for CRC-verified 66-byte guarded Sotto bursts.

Accepts the *blind SDR* FSK-13/14 guard receiver's JSON (not a TX manifest).
Explicit wire format is mandatory: legacy = one message ID, multiple sequence
numbers; stream = each 320 ms segment has a new message ID and sequence zero.
Only the latter is produced by c/k6/mod/dv_guard33_stream.h.

All RF timing is from receiver payload-start timestamps; the caller's jitter
reserve is an assumption, NOT proven on-radio playback. No hardware access.
No private speech bytes are printed; users must choose a private directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import wave
from io import BytesIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modem import pager
from tools.k6_fsk_guard33_mic_playout import private_write

SLOT_S = 0.04
PHYSICAL_BYTES = 66
ON_AIR_S = PHYSICAL_BYTES * 8 / 4800
SEGMENT_SLOTS = 8
MAX_PACKETS = 144
PROFILE = pager.profile("L", "1200", depth=1, parity=False, short_last=True)
ZERO_FRAME = bytes(6)


def _verified_packets(report: dict) -> tuple[list[dict], int]:
    """CRC and guard-qualified physical observations; do not trust JSON flags."""
    observations = report.get("frames")
    if not isinstance(observations, list) or len(observations) > MAX_PACKETS:
        raise ValueError("Expected at most 144 bounded physical observations")
    packets = []
    ignored = 0
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError("Malformed physical observation")
        if row.get("valid") is not True or row.get("ambiguous") is not False:
            ignored += 1
            continue
        try:
            raw = bytes.fromhex(row["packet_hex"])
            decoded = PROFILE.unpack(raw)
            t = float(row["payload_start_s"])
            baud = float(row["selected_baud"])
        except (KeyError, ValueError, TypeError) as e:
            raise ValueError("Malformed supposedly CRC-valid observation") from e
        if (decoded is None or len(raw) != 64 or not math.isfinite(t) or t < 0
                or not math.isfinite(baud) or abs(baud - 4800) > 0.001
                or row.get("sync_errors") != 0
                or row.get("offset_bytes") not in (0, 2)
                or row.get("msg_id") != decoded["msg_id"]
                or row.get("seq") != decoded["seq"]
                or row.get("total_codec_frames") != decoded["total"]
                or row.get("packet_sha256", hashlib.sha256(raw).hexdigest())
                != hashlib.sha256(raw).hexdigest()
                or decoded["parity"] is not None or decoded["depth"] != 1
                or decoded["short"] is not False
                or decoded["total"] is None or decoded["total"] < 1):
            raise ValueError("Invalid CRC, header, timestamp, sync or guard metadata")
        packets.append({
            "raw": raw,
            "msg_id": decoded["msg_id"],
            "seq": decoded["seq"],
            "total": decoded["total"],
            "frames": decoded["frames"],
            "arrival_s": t + ON_AIR_S,
        })
    return packets, ignored


def _dedupe(items: list[dict], key: str) -> dict[int, dict]:
    """Reject conflicting CRC-valid repeats, keep earliest identical packet."""
    result = {}
    for item in items:
        k = item[key]
        prev = result.get(k)
        if prev is not None and prev["raw"] != item["raw"]:
            raise ValueError("Conflicting CRC-valid copies of same voice packet")
        if prev is None or item["arrival_s"] < prev["arrival_s"]:
            result[k] = item
    return result


def replay(report: dict, *, mode: str, start_id: int,
           end_id: int | None = None, last_frames: int | None = None,
           buffer_ms: int = 160) -> dict:
    """Return compressed Sotto frames, per-frame playout mask and RF metrics.

    Stream: consecutive msg IDs, seq=0, total=1..8. End ID is mandatory to
    avoid treating a missing final segment as a complete conversation. If that
    final packet was lost, last_frames must independently supply its length.

    Legacy: a single message ID, seq=0..N-1, every packet advertises total N*6
    codec bytes. The packet at seq0 is needed to anchor playout timing.

    No wrapping of 8-bit message IDs within a session, no inferred call start
    or end, and no invented FEC/session headers.
    """
    if mode not in ("stream", "legacy") or not 0 <= start_id <= 255:
        raise ValueError("Explicit wire mode and valid initial message ID required")
    if not isinstance(buffer_ms, int) or not 0 <= buffer_ms <= 5000:
        raise ValueError("Buffer must be 0..5000 ms")
    rows, ignored = _verified_packets(report)

    if mode == "legacy":
        if end_id is not None or last_frames is not None:
            raise ValueError("Legacy mode uses the packet's own total")
        by = _dedupe([r for r in rows if r["msg_id"] == start_id], "seq")
        if 0 not in by:
            raise ValueError("No first packet: cannot anchor playout")
        length = by[0]["total"]
        packet_count = (length + SEGMENT_SLOTS - 1) // SEGMENT_SLOTS
        if not 1 <= packet_count <= MAX_PACKETS:
            raise ValueError("Unbounded legacy clip size")
        for seq, p in by.items():
            if seq >= packet_count or p["total"] != length:
                raise ValueError("Contradictory legacy packet sequence/length")
        by_index = by
        observed_ids = [start_id]
    else:
        if (end_id is None or not start_id <= end_id <= 255
                or end_id - start_id + 1 > MAX_PACKETS):
            raise ValueError("Stream needs bounded non-wrapping start/end IDs")
        if last_frames is not None and not 1 <= last_frames <= 8:
            raise ValueError("Last codec frame count must be 1..8")
        selected = [r for r in rows if start_id <= r["msg_id"] <= end_id]
        if any(p["seq"] != 0 or not 1 <= p["total"] <= 8 for p in selected):
            raise ValueError("Stream requires seq0 and 1..8 frames per message")
        by_id = _dedupe(selected, "msg_id")
        if start_id not in by_id:
            raise ValueError("No first stream packet: cannot anchor playout")
        if any(p["total"] != 8 for mid, p in by_id.items() if mid != end_id):
            raise ValueError("Partial segment before explicit end of stream")
        final = by_id.get(end_id)
        if final is not None:
            if last_frames is not None and last_frames != final["total"]:
                raise ValueError("Final segment contradicts signaled length")
            tail = final["total"]
        elif last_frames is not None:
            tail = last_frames
        else:
            raise ValueError("Missing final segment and no out-of-band tail length")
        by_index = {mid - start_id: p for mid, p in by_id.items()}
        packet_count = end_id - start_id + 1
        length = (packet_count - 1) * SEGMENT_SLOTS + tail
        observed_ids = sorted(by_id)

    first = by_index[0]["arrival_s"]
    first_playout = first + buffer_ms / 1000
    frames = []
    good = []
    missing = 0
    late = 0
    longest = run = 0
    for i in range(packet_count):
        p = by_index.get(i)
        if p is None:
            missing += 1
        nslots = min(SEGMENT_SLOTS, length - i * SEGMENT_SLOTS)
        for j in range(nslots):
            index = i * SEGMENT_SLOTS + j
            slot_time = first_playout + SLOT_S * index
            on_time = p is not None and p["arrival_s"] <= slot_time + 1e-7
            if p is not None and not on_time:
                late += 1
            frames.append(p["frames"][j] if p is not None else ZERO_FRAME)
            good.append(1 if on_time else 0)
            if on_time:
                run = 0
            else:
                run += 1
                longest = max(longest, run)
    compressed = b"".join(frames)
    return {
        "mode": mode,
        "initial_message_id": start_id,
        "final_message_id": end_id if mode == "stream" else start_id,
        "observed_message_ids": observed_ids,
        "observed_sequences": sorted(by_index),
        "received_packets": len(by_index),
        "ignored_physical_observations": ignored,
        "expected_packets": packet_count,
        "missing_packets": missing,
        "total_codec_frames": length,
        "on_time_codec_frames": sum(good),
        "concealed_codec_frames": length - sum(good),
        "late_codec_frames": late,
        "longest_concealment_ms": longest * 40,
        "first_packet_reception_end_s": round(first, 7),
        "first_playout_s": round(first_playout, 7),
        "buffer_ms": buffer_ms,
        "frame_duration_ms": 40,
        "physical_frame_airtime_ms": ON_AIR_S * 1000,
        "source": "Independent public-sync/CRC SDR report; no known TX bytes for selection",
        "limitations": [
            "Host-side offline scheduling; K6 RX, autonomous Sotto decode and speaker unproven",
            "Playout clock anchored to first received burst, NOT an observed original audio-capture clock",
            "Stream message ID session boundaries and any lost final tail length are out-of-band",
            "No FEC; late and erased frames are decoder concealment rather than recovery",
        ],
        "encoded_sha256": hashlib.sha256(compressed).hexdigest(),
        "encoded_bytes": compressed,
        "playout_mask": bytes(good),
    }


def write_private(result: dict, folder: Path, prefix: str, decoder: Path | None = None):
    """Write only to existing private 0700 dir, O_EXCL 0600; never clobber evidence."""
    if (not folder.is_dir() or folder.stat().st_mode & 0o077
            or not prefix or "/" in prefix or prefix in (".", "..")):
        raise PermissionError("Existing 0700 private directory and plain filename prefix required")
    names = [f"{prefix}.bit", f"{prefix}.good", f"{prefix}.json"]
    if decoder is not None:
        names.extend([f"{prefix}.raw", f"{prefix}.wav"])
    paths = [folder / n for n in names]
    if any(x.exists() for x in paths):
        raise FileExistsError("Refuse overwriting existing playout evidence")
    summary = {k: v for k, v in result.items() if k not in ("encoded_bytes", "playout_mask")}
    created = []
    try:
        for path, data in ((paths[0], result["encoded_bytes"]),
                           (paths[1], result["playout_mask"])):
            private_write(path, data)
            created.append(path)
        if decoder is not None:
            if not decoder.is_file():
                raise FileNotFoundError("Sotto decoder binary unavailable")
            # Precreate decoder's writable path as 0600, protecting the result
            # even when the decoder opens the destination with O_TRUNC.
            private_write(paths[3], b"")
            created.append(paths[3])
            subprocess.run([str(decoder), "-1200", str(paths[0]),
                            str(paths[3]), str(paths[1])],
                           check=True, capture_output=True, timeout=60)
            pcm = paths[3].read_bytes()
            if len(pcm) != result["total_codec_frames"] * 640:
                raise ValueError("Unexpected Sotto 8 kHz / 40 ms PCM length")
            data = BytesIO()
            with wave.open(data, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(8000)
                wav.writeframes(pcm)
            private_write(paths[4], data.getvalue())
            created.append(paths[4])
            summary["decoded_wav_sha256"] = hashlib.sha256(data.getvalue()).hexdigest()
        private_write(paths[2], (json.dumps(summary, indent=2) + "\n").encode())
        created.append(paths[2])
        return summary
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rx-json", required=True, type=Path)
    ap.add_argument("--out-private-dir", required=True, type=Path)
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--mode", choices=("stream", "legacy"), required=True)
    ap.add_argument("--start-id", type=int, required=True)
    ap.add_argument("--end-id", type=int)
    ap.add_argument("--last-frames", type=int)
    ap.add_argument("--buffer-ms", type=int, default=160)
    ap.add_argument("--decoder", type=Path)
    args = ap.parse_args()
    plan = replay(json.loads(args.rx_json.read_text()), mode=args.mode,
                  start_id=args.start_id, end_id=args.end_id,
                  last_frames=args.last_frames, buffer_ms=args.buffer_ms)
    result = write_private(plan, args.out_private_dir, args.prefix, args.decoder)
    print("CRC-valid guarded packets", result["received_packets"], "/",
          result["expected_packets"], "concealed codec frames",
          result["concealed_codec_frames"], "/", result["total_codec_frames"],
          "buffer ms", result["buffer_ms"], "NO physical radio activity")


if __name__ == "__main__":
    main()
