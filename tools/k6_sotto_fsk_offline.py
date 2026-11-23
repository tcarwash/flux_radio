"""Offline Sotto-1200 packet voice model using Flux's existing pager.py XL wire format.

Only compressed six-byte Sotto frames are transported. The native-FSK link is
represented by measured 1024-bit payload plus average inter-frame restart time,
NOT by a validated RF receiver/transmitter. No serial or hardware access.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import sys

# Also support direct: python3 tools/k6_sotto_fsk_offline.py --sotto-file ...
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modem import pager

FRAME_S = 0.040
FRAME_BYTES = 6
NATIVE_PACKET_BYTES = 128
NATIVE_BAUD = 4800
RF_RESTART_S = 0.046719
RF_PACKET_S = (NATIVE_PACKET_BYTES * 8 / NATIVE_BAUD) + RF_RESTART_S
assert abs(RF_PACKET_S - 0.26005233333333333) < 1e-9


def source_frames(n: int = 375) -> bytes:
    """Synthetic codec-sized data, only for self-contained unit tests.

    Run --sotto-file to use existing real encoded Sotto-1200 material instead.
    """
    return bytes((17 * i + 31 * j + (i >> 3)) & 255
                 for i in range(n) for j in range(FRAME_BYTES))


@dataclass
class VoiceResult:
    input_frames: int
    payload_profile: str
    rf_frame_ms: float
    voice_s: float
    depth: int
    parity: bool
    packets: int
    data_packets: int
    rf_air_s: float
    air_fraction: float
    max_tx_queue_s: float
    startup_s: float
    capture_to_playout_ms: float
    playout_buffer_ms: int
    jitter_ms: int
    loss_probability: float
    corrupted_probability: float
    dropped_packets: int
    corrupted_packets: int
    duplicate_packets: int
    crc_rejected: int
    parity_reconstructed_packets: int
    ontime_frames: int
    concealed_frames: int
    eventually_good_frames: int
    late_recoverable_frames: int
    longest_concealment_ms: int
    frame_good_fraction: float
    frame_values_match: bool


def simulate(
    frames: bytes,
    *,
    depth: int = 1,
    parity: bool = False,
    payload: str = "XL",
    playout_buffer_ms: int = 150,
    jitter_ms: int = 0,
    pipeline_ms: int = 20,
    loss_probability: float = 0.0,
    corrupted_probability: float = 0.0,
    duplicate_probability: float = 0.0,
    seed: int = 0,
    drop_indices: tuple[int, ...] = (),
    corrupt_indices: tuple[int, ...] = (),
) -> VoiceResult:
    """Schedule encoded frames through the existing XL CRC/whitening/parity format.

    Transport assumptions: isolated native 128-byte FSK frames every 260.052 ms
    while busy, a flawless receiver for un-erased frames, and independently
    generated per-packet delivery jitter. This explicitly does not model RF BER,
    sync acquisition or dynamic preamble gaps.
    """
    if len(frames) == 0 or len(frames) % FRAME_BYTES:
        raise ValueError("Provide a nonempty whole number of Sotto six-byte frames")
    if not (0 <= loss_probability <= 1 and 0 <= corrupted_probability <= 1
            and 0 <= duplicate_probability <= 1):
        raise ValueError("Probabilities must be in [0, 1]")
    if min(jitter_ms, playout_buffer_ms, pipeline_ms) < 0:
        raise ValueError("Timings must be nonnegative")
    if depth not in pager.DEPTHS:
        raise ValueError("Interleave depth must be 1, 2, 4 or 8")
    if payload not in ("XL", "L"):
        raise ValueError("Use the existing L (64B) or XL (128B) pager profile")
    # The 46.719-ms restart was physically observed for XL only.
    # Applying it to the 64B L profile is explicitly an extrapolation.
    rf_packet_s = pager.PROFILES[payload].payload * 8 / NATIVE_BAUD + RF_RESTART_S

    rng = random.Random(seed)
    n_frames = len(frames) // FRAME_BYTES
    profile = pager.profile(payload, "1200", depth=depth, parity=parity, short_last=True)
    encoded = profile.pack(frames, msg_id=33)
    if not all(len(p) == profile.payload for p in encoded):
        raise AssertionError("Unexpected native FSK payload size")

    events: list[tuple[float, int, bytes]] = []
    dropped = corrupted = duplicated = 0
    t_tx_end = 0.0
    t_first_ready = None
    max_queue = 0.0
    data_packets = 0
    for idx, payload in enumerate(encoded):
        header = profile.unpack(payload)
        assert header is not None  # guarantee CRC and serializer self-consistency
        if header["parity"] is not None:
            max_frame_idx = min((header["group"] + 1) * profile.group_frames,
                                n_frames) - 1
        else:
            data_packets += 1
            valid = [j for j in profile.packet_frames(header["seq"], n_frames)
                     if j < n_frames]
            assert valid
            max_frame_idx = max(valid)
            if t_first_ready is None:
                t_first_ready = (max_frame_idx + 1) * FRAME_S
        t_ready = (max_frame_idx + 1) * FRAME_S
        t_start = max(t_ready, t_tx_end)
        max_queue = max(max_queue, t_start - t_ready)
        t_tx_end = t_start + rf_packet_s
        arriving = t_tx_end + pipeline_ms / 1000 + rng.uniform(0, jitter_ms / 1000)
        if idx in drop_indices or rng.random() < loss_probability:
            dropped += 1
            continue
        if idx in corrupt_indices or rng.random() < corrupted_probability:
            corrupted += 1
            b = bytearray(payload)
            b[10] ^= 1  # CRC invalid, should be rejected by existing pager.Assembler
            payload = bytes(b)
        events.append((arriving, idx, payload))
        if rng.random() < duplicate_probability:
            duplicated += 1
            events.append((arriving + .015, idx, payload))
    events.sort(key=lambda x: (x[0], x[1]))

    assert t_first_ready is not None
    # Explicit pre-roll from completion of first frame's RF transmission,
    # plus pipeline delay. No loss-adaptive start that conceals erased first packets.
    t_play_start = (t_first_ready + rf_packet_s + pipeline_ms / 1000
                    + playout_buffer_ms / 1000)
    recv = pager.Assembler(profile)
    event_cursor = 0
    ontime = 0
    missing = 0
    longest = run = 0
    all_match = True
    for frame_idx in range(n_frames):
        t_play = t_play_start + frame_idx * FRAME_S
        while event_cursor < len(events) and events[event_cursor][0] <= t_play + 1e-10:
            recv.push(events[event_cursor][2])
            event_cursor += 1
        if 33 in recv.messages:
            decoded, good = recv.frames(33)
            valid = bool(good[frame_idx])
            if valid:
                all_match &= (decoded[FRAME_BYTES * frame_idx:
                                       FRAME_BYTES * (frame_idx + 1)] ==
                              frames[FRAME_BYTES * frame_idx:
                                     FRAME_BYTES * (frame_idx + 1)])
        else:
            valid = False
        if valid:
            ontime += 1
            longest = max(longest, run)
            run = 0
        else:
            missing += 1
            run += 1
    longest = max(longest, run)
    while event_cursor < len(events):
        recv.push(events[event_cursor][2])
        event_cursor += 1
    final_good = int(recv.frames(33)[1].sum()) if 33 in recv.messages else 0
    return VoiceResult(
        input_frames=n_frames, payload_profile=profile.name,
        rf_frame_ms=round(rf_packet_s * 1000, 3),
        voice_s=round(n_frames * FRAME_S, 3),
        depth=depth, parity=parity, packets=len(encoded),
        data_packets=data_packets,
        rf_air_s=round(len(encoded) * rf_packet_s, 3),
        air_fraction=round(len(encoded) * rf_packet_s / (n_frames * FRAME_S), 4),
        max_tx_queue_s=round(max_queue, 3),
        startup_s=round(t_play_start, 3),
        capture_to_playout_ms=round((t_play_start - FRAME_S) * 1000, 1),
        playout_buffer_ms=playout_buffer_ms, jitter_ms=jitter_ms,
        loss_probability=loss_probability, corrupted_probability=corrupted_probability,
        dropped_packets=dropped, corrupted_packets=corrupted,
        duplicate_packets=duplicated, crc_rejected=recv.bad,
        parity_reconstructed_packets=recv.rebuilt, ontime_frames=ontime,
        concealed_frames=missing, eventually_good_frames=final_good,
        late_recoverable_frames=max(0, final_good - ontime),
        longest_concealment_ms=longest * 40,
        frame_good_fraction=round(ontime / n_frames, 5),
        frame_values_match=all_match,
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sotto-file", type=Path, help="6-byte per 40-ms Sotto 1200 bitstream")
    p.add_argument("--depth", type=int, default=1, choices=pager.DEPTHS)
    p.add_argument("--payload", choices=("XL", "L"), default="XL")
    p.add_argument("--parity", action="store_true")
    p.add_argument("--buffer-ms", type=int, default=150)
    p.add_argument("--jitter-ms", type=int, default=0)
    p.add_argument("--loss", type=float, default=0.0)
    p.add_argument("--corruption", type=float, default=0.0)
    p.add_argument("--duplicates", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=0)
    opts = p.parse_args()
    stream = opts.sotto_file.read_bytes() if opts.sotto_file else source_frames()
    result = simulate(
        stream, depth=opts.depth, parity=opts.parity,
        payload=opts.payload,
        playout_buffer_ms=opts.buffer_ms, jitter_ms=opts.jitter_ms,
        loss_probability=opts.loss, corrupted_probability=opts.corruption,
        duplicate_probability=opts.duplicates, seed=opts.seed)
    print(json.dumps({"Sotto_source": str(opts.sotto_file) if opts.sotto_file else
                      "SYNTHETIC six-byte slots (not codec-produced)",
                      "source_sha256": hashlib.sha256(stream).hexdigest(),
                      **asdict(result)}, indent=2))


if __name__ == "__main__":
    main()
