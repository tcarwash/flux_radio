#!/usr/bin/env python3
"""Proposed Flux DV V1 session control in guarded 66-byte native-FSK bursts.

Separate 0xE2 control magic: legacy voice 0xA9 format is UNCHANGED.
This is an OFFLINE PROTOTYPE, not a proven RF or receiver capability.
64-byte public PN9/CRC16 payload + sacrificial 0xA55A final word.
CRC is accidental-error detection, NOT authentication.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from modem import pager

MAGIC = 0xE2
VERSION = 1
BEGIN = 1
END = 2
SEGMENT_MAX = 144
SEAL = b"FDV1"
GUARD = bytes([0x5A, 0xA5])
L64 = pager.profile("L", "1200", depth=1, parity=False, short_last=True)


@dataclass(frozen=True)
class Session:
    kind: int
    tag: int
    first_id: int
    last_id: int = 0
    last_frames: int = 0
    segments: int = 0

    def validate(self):
        if not (self.kind in (BEGIN, END) and 1 <= self.tag <= (1 << 64)-1
                and 0 <= self.first_id <= 255 and 0 <= self.last_id <= 255):
            raise ValueError("Invalid session kind, public tag or voice ID")
        if self.kind == BEGIN:
            if self.last_id != 0 or self.last_frames != 0 or self.segments != 0:
                raise ValueError("BEGIN must not guess the eventual session end")
        elif not (1 <= self.segments <= SEGMENT_MAX and
                  self.last_id >= self.first_id and
                  self.last_id-self.first_id+1 == self.segments and
                  1 <= self.last_frames <= 8):
            raise ValueError("END requires nonwrapping final ID/count/last frame length")


def encode_control(session: Session) -> bytes:
    """Returns a guarded 66-byte frame, to be queued for RF separately."""
    session.validate()
    plain = bytearray(64)
    plain[:4] = bytes([MAGIC, VERSION, session.kind, 0])
    plain[4:12] = session.tag.to_bytes(8, "big")
    plain[12:16] = bytes([session.first_id, session.last_id,
                          session.last_frames, session.segments])
    plain[16:20] = SEAL
    plain[62:64] = pager.crc16_ccitt(plain[:62]).to_bytes(2, "big")
    wire = bytes(x ^ y for x, y in zip(plain, pager.pn9(64)))
    return wire + GUARD


def decode_control(packet64: bytes) -> Session | None:
    """Strict CRC, reserved-byte, fixed-v1 validation; None if not control."""
    if len(packet64) != 64:
        return None
    plain = bytes(x ^ y for x, y in zip(packet64, pager.pn9(64)))
    if (plain[0] != MAGIC or plain[1] != VERSION or
        plain[2] not in (BEGIN, END) or plain[3] != 0 or
        plain[16:20] != SEAL or any(plain[20:62]) or
        pager.crc16_ccitt(plain[:62]) != int.from_bytes(plain[62:64], "big")):
        return None
    info = Session(kind=plain[2], tag=int.from_bytes(plain[4:12], "big"),
                   first_id=plain[12], last_id=plain[13],
                   last_frames=plain[14], segments=plain[15])
    try:
        info.validate()
    except ValueError:
        return None
    return info


def select_guarded(physical: bytes) -> dict:
    """Detect unique public-format/CRC-valid voice OR control after offset 0/2.

    No known TX bytes or microphone hashes enter selection. Unknown magic,
    multiple possible CRC matches, or corrupted payload fail closed.
    """
    if len(physical) != 66:
        raise ValueError("Exactly 66 received physical bytes required")
    candidates = []
    for off in (0, 2):
        packet = physical[off:off+64]
        info = decode_control(packet)
        if info is not None:
            decoded = dict(type="control", kind=info.kind, tag=info.tag,
                           first_id=info.first_id, last_id=info.last_id,
                           last_frames=info.last_frames, segments=info.segments)
        else:
            voice = L64.unpack(packet)
            if voice is None:
                continue
            decoded = dict(type="voice", msg_id=voice["msg_id"],
                           seq=voice["seq"], total_codec_frames=voice["total"],
                           codec_frames=len(voice["frames"]),
                           depth=voice["depth"])
        candidates.append(dict(valid=True, ambiguous=False, packet_type=decoded["type"],
                               offset_bytes=off, packet_hex=packet.hex(),
                               packet_sha256=hashlib.sha256(packet).hexdigest(),
                               **decoded))
    if len(candidates) != 1:
        return dict(valid=False, ambiguous=len(candidates)>1,
                    candidate_count=len(candidates))
    return candidates[0]


def packet_in_observation(row: dict) -> tuple[dict, bytes]:
    """Revalidate a blind SDR-reported packet, rejecting forged JSON flags."""
    if row.get("valid") is not True or row.get("ambiguous") is not False:
        raise ValueError("Unqualified RF observation")
    if row.get("selected_baud") != 4800 or row.get("sync_errors") != 0:
        raise ValueError("RF timing or public sync is not qualified")
    raw = bytes.fromhex(row["packet_hex"])
    if len(raw) != 64:
        raise ValueError("Wrong packet length")
    if row.get("packet_sha256") != hashlib.sha256(raw).hexdigest():
        raise ValueError("RF packet digest metadata mismatch")
    info = decode_control(raw)
    if info is not None:
        parsed = dict(packet_type="control", kind=info.kind, tag=info.tag,
                      first_id=info.first_id, last_id=info.last_id,
                      last_frames=info.last_frames, segments=info.segments)
    else:
        voice = L64.unpack(raw)
        if voice is None:
            raise ValueError("No valid CRC voice/control packet")
        parsed = dict(packet_type="voice", msg_id=voice["msg_id"], seq=voice["seq"],
                      total_codec_frames=voice["total"])
    for k, v in parsed.items():
        if k in row and row[k] != v:
            raise ValueError("Untrusted reported header disagrees with checked CRC")
    if row.get("offset_bytes") not in (0, 2):
        raise ValueError("Guard selection offset not established")
    if "physical_hex" in row:
        selected = select_guarded(bytes.fromhex(row["physical_hex"]))
        if not selected["valid"] or selected["packet_hex"] != row["packet_hex"] or selected["offset_bytes"] != row["offset_bytes"]:
            raise ValueError("Physical prefix selection and CRC disagree")
    return parsed, raw


def replay_onair(report: dict, *, buffer_ms: int = 160) -> dict:
    """Fail closed unless matching over-air START and END control both exist.

    Physical session identity is a public tag, not signed/authenticated.
    Legacy FSK-14 remains supported by the explicit legacy receiver,
    and is NOT implicitly treated as having an on-air session.
    """
    from tools.k6_dv_guard33_playout import replay
    rows = report.get("frames", [])
    if not isinstance(rows, list) or len(rows) > SEGMENT_MAX+4:
        raise ValueError("Physical recording exceeds prototype session bound")
    starts, ends, voices = [], [], []
    for row in rows:
        if row.get("valid") is not True:
            continue  # an RF erasure, NOT an unverified control
        parsed, raw = packet_in_observation(row)
        if parsed["packet_type"] == "voice":
            voices.append(row)
        else:
            record = (row, parsed, raw)
            (starts if parsed["kind"] == BEGIN else ends).append(record)
    if not starts or not ends:
        raise ValueError("Missing on-air BEGIN or END; session incomplete")
    # A captured recording can contain repeated, byte-identical BEGIN/END.
    # No mixing of different control tags or conflicting same-type controls.
    def choose(items: list[tuple]) -> tuple:
        raw_values = {raw for _, _, raw in items}
        if len(raw_values) != 1:
            raise ValueError("Conflicting/overlapping on-air control records")
        return min(items, key=lambda record: record[0]["payload_start_s"])
    firstrow, first, _ = choose(starts)
    lastrow, last, _ = choose(ends)
    if (first["tag"] != last["tag"] or first["first_id"] != last["first_id"]
            or not float(firstrow["payload_start_s"]) < float(lastrow["payload_start_s"])):
        raise ValueError("START/END tags, IDs or time order disagree")
    for row in voices:
        if (first["first_id"] <= row["msg_id"] <= last["last_id"] and
                not (float(firstrow["payload_start_s"]) <
                     float(row["payload_start_s"]) <
                     float(lastrow["payload_start_s"]))):
            raise ValueError("Speech segment appears outside BEGIN/END")
    voice_report = dict(frames=voices)
    replayed = replay(voice_report, mode="stream", start_id=first["first_id"],
                      end_id=last["last_id"], last_frames=last["last_frames"],
                      buffer_ms=buffer_ms)
    replayed.update(session_tag_hex=f"{first['tag']:016x}",
                    session_control_version=VERSION, onair_begin_seen=True,
                    onair_end_seen=True, onair_control_frames=len(starts)+len(ends),
                    onair_session_bounded_by_end=True,
                    session_control_not_authenticated=True)
    return replayed


def receive_iq(iq) -> dict:
    """Optional physical SDR IQ -> uniquely selected voice/controls, no TX."""
    from tools.k6_fsk_sotto_sync_rx import find_frames
    def probe(raw: bytes):
        x = select_guarded(raw)
        if not x["valid"]:
            return None
        # find_frames has an existing voice-oriented callback contract:
        # msg_id, seq, and a sized frames collection must be present.
        # Return only CRC-derived public metadata; controls carry zero voice.
        return dict(msg_id=x.get("msg_id"), seq=x.get("seq"),
                    frames=[None] * x.get("codec_frames", 0))
    obs = find_frames(iq, payload_bytes=66, rate_factors=(1.,),
                      min_correlation=.95, unpack_packet=probe)
    frames = []
    for candidate in obs["packets"]:
        selected = select_guarded(bytes.fromhex(candidate["raw_payload_hex"]))
        frames.append(dict(**selected, physical_hex=candidate["raw_payload_hex"],
                           payload_start_s=candidate["payload_start_s"],
                           selected_baud=candidate["baud_estimate"],
                           sync_errors=candidate["sync_bit_errors"]))
    return dict(experiment="Proposed FDV1 on-air DV control / guarded voice",
                no_tx=True, frames_detected=obs["frames_found"], frames=frames)


def main():
    import argparse
    from pathlib import Path
    from tools.k6_dv_guard33_playout import write_private
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rx-json", type=Path, required=True)
    p.add_argument("--out-private-dir", type=Path, required=True)
    p.add_argument("--prefix", required=True)
    p.add_argument("--buffer-ms", type=int, default=160)
    p.add_argument("--decoder", type=Path)
    a = p.parse_args()
    plan = replay_onair(json.loads(a.rx_json.read_text()), buffer_ms=a.buffer_ms)
    summary = write_private(plan, a.out_private_dir, a.prefix, a.decoder)
    print("ON-AIR CONTROL PROTOTYPE, modeled receiver only:",
          summary["received_packets"], "voice packets,",
          summary["on_time_codec_frames"], "/", summary["total_codec_frames"],
          "frames on-time, no K6 radio or RF activity")


if __name__ == "__main__":
    main()
