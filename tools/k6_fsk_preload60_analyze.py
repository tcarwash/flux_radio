#!/usr/bin/env python3
"""Prospective OFFLINE receiver for the separate FSK-09S-G preload-only control.

Acquisition: public preamble/sync, frozen nominal 4800 baud, *no* payload
training. A shifted/0x00F8 frame is always a strict FAIL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from k6_fsk_iq_edges import read_iq
from k6_fsk_lab_vector import make_probe_vector
from k6_fsk_sotto_sync_rx import find_frames


def analyze_file(path: Path):
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    iq = read_iq(path, duration=2.0, offset=280500.)
    frames = find_frames(
        iq, payload_bytes=120, rate_factors=(1.0,), min_correlation=.95,
    )
    words, _ = make_probe_vector(word_count=60, seed=0xACE1)
    expected = b"".join(w.to_bytes(2, "little") for w in words)
    found = []
    for p in frames["packets"]:
        received = bytes.fromhex(p["raw_payload_hex"])
        errs = sum((a ^ b).bit_count() for a, b in zip(received, expected))
        found.append({
            "payload_start_s": p["payload_start_s"],
            "public_sync_correlation": p["sync_correlation"],
            "public_sync_bit_errors": p["sync_bit_errors"],
            "selected_baud": p["baud_estimate"],
            "payload_errors": errs,
            "first_word_le": received[:2].hex(),
            "last_word_le": received[-2:].hex(),
            "last_word_exact": received[-2:] == expected[-2:],
            "strict_exact": errs == 0,
            "recovered_payload_hex": received.hex(),
        })
    ok = len(found) == 1 and found[0]["strict_exact"]
    return {
        "experiment": "FSK-09S-G standalone fully preloaded 60-word control",
        "result": "STRICT_PASS" if ok else "FAIL_OR_INCONCLUSIVE",
        "capture": str(path),
        "iq_sha256": digest,
        "acquisition_uses_payload": False,
        "public_preamble_sync": "16x55 plus 076b3e36",
        "nominal_baud_frozen": 4800,
        "payload_words": 60,
        "preloaded_before_tx": 60,
        "after_start_writes": 0,
        "expected_payload_sha256": hashlib.sha256(expected).hexdigest(),
        "frame_candidates": frames["frames_found"],
        "candidates_considered": frames["candidates_considered"],
        "frames": found,
        "non_goals": "No in-flight append, FIFO headroom, >64-word streaming or continuous DV tested",
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if a.out.exists():
        p.error("refuse to overwrite an existing evidence report")
    result = analyze_file(a.iq)
    a.out.write_text(json.dumps(result, indent=2) + "\n")
    print(result["result"], "frames", result["frame_candidates"])
    for frame in result["frames"]:
        print("payload errors", frame["payload_errors"], "first", frame["first_word_le"])


if __name__ == "__main__":
    main()
