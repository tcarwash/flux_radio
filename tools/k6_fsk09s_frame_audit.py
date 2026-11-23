#!/usr/bin/env python3
"""Offline, archived-IQ-only FSK-09S FIFO framing audit.

Always retain the original prospective hardware verdict. This script separately
scores (a) the declared exact frame and (b) the *post-hoc* hypothesis that an
unexpected 0x00F8 word preceded the payload and displaced the last word.
Acquisition uses only public modem preamble/sync and a FIXED 4800-baud clock.
No serial, SDR device, GPIO, radio, or firmware interaction is performed.
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

DEFAULT_ARCHIVE = Path("/home/tyler/flux-bench/tx-injection-20261122")
FILES = {
    "c": ("fsk09s-oneword/fsk09s-oneword-run01.npy", 49),
    "d": ("fsk09s-oneword/fsk09s-single-run01.npy", 49),
    "e": ("fsk09s-fourbatch/fsk09s-fourbatch-run01.npy", 64),
    "f": ("fsk09s-threebatch/fsk09s-threebatch-run01.npy", 60),
}


def expected_frames(experiment: str) -> list[bytes]:
    if experiment not in FILES:
        raise ValueError("unknown experiment")
    n = FILES[experiment][1]
    words, _ = make_probe_vector(word_count=64, seed=0xACE1)

    def packed(items):
        return b"".join(int(x).to_bytes(2, "little") for x in items)

    if experiment == "c":
        # Preloaded control; inverted 48-word prefix plus 0xA53C delayed marker.
        return [packed(list(words[:48]) + [0x3CA5]),
                packed([(~x) & 0xFFFF for x in words[:48]] + [0xA53C])]
    if experiment == "d":
        return [packed([(~x) & 0xFFFF for x in words[:48]] + [0xA53C])]
    return [packed(words[:n])]


def bit_errors(observed: bytes, expected: bytes) -> int:
    if len(observed) != len(expected):
        raise ValueError("different payload lengths")
    return sum((a ^ b).bit_count() for a, b in zip(observed, expected))


def score_payload(observed: bytes, expected: bytes, *, preloaded_words=48) -> dict:
    strict = bit_errors(observed, expected)
    # Forensics only: never redefine prospective success using this score.
    artifact_hypothesis = bytes.fromhex("f800") + expected[:-2]
    shifted = bit_errors(observed, artifact_hypothesis)
    received_after_prefix = observed[2:]
    preload_bytes = preloaded_words * 2
    preload_matches = received_after_prefix[:preload_bytes] == expected[:preload_bytes]
    appended_bytes = received_after_prefix[preload_bytes:]
    append_matches = appended_bytes == expected[preload_bytes:-2]
    return {
        "first_received_word_le": observed[:2].hex(),
        "strict_bit_errors": strict,
        "strict_complete_frame_pass": strict == 0,
        "posthoc_leading_0x00f8_bit_errors": shifted,
        "posthoc_0x00f8_exact": shifted == 0,
        "posthoc_initial_48_words_exact": preload_matches,
        "posthoc_appended_words_exact": append_matches,
        "posthoc_recovered_appended_words": (len(appended_bytes) // 2) if append_matches else None,
        "final_declared_word_displaced_by_artifact": shifted == 0,
        "posthoc_only_not_original_test_pass": True,
    }


def audit(experiment: str, archive: Path = DEFAULT_ARCHIVE) -> dict:
    filename, word_count = FILES[experiment]
    capture = archive / filename
    with capture.open("rb") as fp:
        digest = hashlib.file_digest(fp, "sha256").hexdigest()
    iq = read_iq(capture, duration=1.8, offset=280500.)
    # Do not choose a symbol rate using payload truth. 4800 is the publicly
    # configured TX modem rate and was independently verified in prior captures.
    fixed = find_frames(iq, payload_bytes=word_count * 2,
                        min_correlation=.95, rate_factors=(1.,))
    # Original F analyzer searched .998/1.0/1.002, and selected .998 by a
    # minuscule sync-correlation advantage. Retain that discrepancy.
    unconstrained = find_frames(iq, payload_bytes=word_count * 2,
                                min_correlation=.95)
    expected = expected_frames(experiment)
    rows = []
    for ix, frame in enumerate(fixed["packets"]):
        if ix >= len(expected):
            break
        observed = bytes.fromhex(frame["raw_payload_hex"])
        rows.append({
            "frame_index": ix,
            "payload_start_s": frame["payload_start_s"],
            "public_sync_correlation": frame["sync_correlation"],
            "public_sync_bit_errors": frame["sync_bit_errors"],
            "baud_fixed": frame["baud_estimate"],
            **score_payload(observed, expected[ix]),
        })
    return {
        "experiment": f"FSK-09S-{experiment.upper()}",
        "capture": str(capture),
        "iq_sha256": digest,
        "acquisition": "public 16x55 preamble plus 076b3e36 sync, fixed nominal 4800 baud",
        "posthoc_hypothesis": "unexpected 0x00F8 leading FIFO word and last-word displacement",
        "prospective_status_unchanged": True,
        "fixed_4800_frame_candidates": fixed["frames_found"],
        "unconstrained_frame_candidates": unconstrained["frames_found"],
        "unconstrained_receiver_baud_choices": [
            f["baud_estimate"] for f in unconstrained["packets"]
        ],
        "frames": rows,
        "limitations": [
            "Post-hoc correction cannot be reclassified as an experimental pass.",
            "0x00F8 may reflect FIFO control/reset/SPI/framing, origin unknown.",
            "No evidence for more than 64 words, safe FIFO headroom or gapless streaming.",
            "SDR is air-coupled and not a calibrated spectrum analyzer.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", choices=tuple(FILES) + ("all",), default="all")
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("refusing to overwrite existing evidence")
    names = FILES if args.experiment == "all" else (args.experiment,)
    result = {name: audit(name, args.archive) for name in names}
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    for name, entry in result.items():
        print(name.upper(), "frames", len(entry["frames"]), "unconstrained rates",
              entry["unconstrained_receiver_baud_choices"])
        for f in entry["frames"]:
            print("  strict errors", f["strict_bit_errors"], "posthoc 0x00f8 errors",
                  f["posthoc_leading_0x00f8_bit_errors"], "in-flight recovered",
                  f["posthoc_recovered_appended_words"])


if __name__ == "__main__":
    main()
