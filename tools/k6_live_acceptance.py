#!/usr/bin/env python3
"""Non-transmitting, non-flashing physical acceptance probes for Flux K6.

observe: read live status during a supervised operator-controlled bench test.
abandon-stream: abandon a read-only downlink, then verify return to 115200.
Never keys PTT, switches power, executes modules or invokes the legacy loader.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import time
from types import SimpleNamespace

from k6_install import CHIP_BYTES, InstallError, open_port
from k6_live import BURST_DATA, BURST_LONG, LiveLink
from k6_port_lock import serial_lock


def checked_rx(link, *, expected_id=None, previous=None):
    state = link.status()
    if (not state["standby_idle"] or state["transmitting"] or
            state["sleeping"]):
        raise InstallError("Radio must be in awake RX standby; no test started.")
    if previous and (state["vfos"] != previous["vfos"] or
                     state["master"] != previous["master"]):
        raise InstallError("VFO settings changed during acceptance.")
    build_id = link.info()["build_id"]
    if build_id is None:
        raise InstallError("Radio has no inspectable firmware build ID.")
    build = build_id.hex()
    if expected_id is not None and build != expected_id:
        raise InstallError(f"Build mismatch: expected {expected_id}, radio has {build}.")
    return state, build


def observe(link, *, duration, interval, output, clock=time.monotonic, sleep=time.sleep):
    """Read only. The operator must monitor RF and perform any local actions."""
    if not 1 <= duration <= 300 or not .1 <= interval <= 5:
        raise ValueError("Observe needs 1–300 seconds and interval 0.1–5 seconds.")
    initial, build = checked_rx(link)
    start = clock()
    samples = 0
    failures = 0
    transitions = 0
    last = None
    with Path(output).expanduser().open("x", encoding="utf-8") as report:
        while clock() - start < duration:
            at = round(clock() - start, 3)
            try:
                value = link.status()
                # Keep every observation, not just changes: an absent response
                # must never be interpreted as RX or proof of power-off.
                snapshot = (value["transmitting"], value["standby_idle"],
                            value["sleeping"], value["master"])
                if last is not None and snapshot != last:
                    transitions += 1
                    print(f"{at:.3f}s transition TX={snapshot[0]} idle={snapshot[1]} "
                          f"sleep={snapshot[2]} master={snapshot[3]}", flush=True)
                last = snapshot
                record = {"seconds": at, "type": "status", "value": value}
                samples += 1
            except (InstallError, OSError, ValueError) as exc:
                failures += 1
                record = {"seconds": at, "type": "no-valid-status",
                          "error": str(exc)}
                print(f"{at:.3f}s no valid live status: {exc}", flush=True)
            report.write(json.dumps(record, separators=(",", ":")) + "\n")
            report.flush()
            sleep(interval)
    return {"build_id": build, "samples": samples, "invalid_samples": failures,
            "state_transitions": transitions, "duration_s": duration,
            "output": str(output), "no_automatic_ptt_or_power": True}


def abandon_stream(link, port, snapshot, *, expected_id, count=3,
                   clock=time.monotonic, sleep=time.sleep):
    """Exercise silent host loss during a bounded read-only 32 KiB download."""
    if not 1 <= count <= 8:
        raise ValueError("Count must be 1–8.")
    if len(snapshot) != CHIP_BYTES:
        raise ValueError("A complete 2 MiB baseline SPI backup is required.")
    initial, build = checked_rx(link, expected_id=expected_id)
    if "bulk_stream" not in link.capabilities()["features"]:
        raise InstallError("Firmware does not support long read-only streams.")
    results = []
    for i in range(count):
        address = 0x1a0000 + i * 0x8000
        packed = address // 64 | (128 << 15) | (2 << 23)
        ack = link.request(BURST_LONG, packed)
        if ack != struct.pack("<I", 500000):
            raise InstallError("Unexpected high-rate downlink acknowledgment.")
        started = clock()
        try:
            port.baudrate = 500000
            frame = link._frame(clock() + 2)
            if not frame or (frame[0], frame[1]) != (BURST_DATA, link.seq):
                raise InstallError("First stream frame missing or mismatched.")
            if (len(frame[2]) != 260 or
                    int.from_bytes(frame[2][:4], "little") != address or
                    frame[2][4:] != snapshot[address:address + 256]):
                raise InstallError("First stream frame failed independent verification.")
            # Do not send anything while the radio finishes without a reader.
            # The device restores its own UART baud rate; this is not a reset.
        finally:
            # Even if the first frame was missing/malformed, stay completely
            # silent until the radio's bounded stream has had time to end.
            # Never issue a recovery command at the wrong UART speed.
            remaining = 1.55 - (clock() - started)
            if remaining > 0:
                sleep(remaining)
            port.baudrate = 115200
            sleep(.06)
            port.reset_input_buffer()
            link.buf = link.text = b""
        checked_rx(link, expected_id=expected_id, previous=initial)
        # Original 4 KiB framed mode verifies recovery without enabling a
        # new long stream or making a flash mutation.
        old_read = link._burst(address, 16, 500000)
        if old_read != snapshot[address:address + 4096]:
            raise InstallError("Recovered old burst does not match backup.")
        results.append({"attempt": i + 1, "start": f"0x{address:06x}",
                        "readback_4k_verified": True, "rx_verified": True})
        print(f"PASS {i + 1}/{count}: 115200 restored, RX and 4KiB readback verified",
              flush=True)
    checked_rx(link, expected_id=expected_id, previous=initial)
    return {"ok": True, "build_id": build, "attempts": results,
            "physical_usb_disconnected": False, "tx_requested": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--expect-build", help="Reject a different 8-byte firmware build ID (hex)")
    sub = parser.add_subparsers(dest="action", required=True)
    obs = sub.add_parser("observe", help="Log status while a local operator controls PTT/power")
    obs.add_argument("--duration", type=float, default=45)
    obs.add_argument("--interval", type=float, default=.25)
    obs.add_argument("--output", type=Path, required=True,
                     help="New JSONL event file; never overwritten")
    burst = sub.add_parser("abandon-stream", help="Read-only host-reader interruption")
    burst.add_argument("--backup", type=Path, required=True,
                       help="Previously verified 2 MiB SPI backup")
    burst.add_argument("--count", type=int, default=3)
    burst.add_argument("--output", type=Path, help="New JSON result file")
    args = parser.parse_args(argv)
    if args.expect_build is not None:
        if len(args.expect_build) != 16 or any(c not in "0123456789abcdef"
                                                 for c in args.expect_build.lower()):
            parser.error("--expect-build must be 16 hex digits")
        args.expect_build = args.expect_build.lower()
    if args.action == "abandon-stream" and args.expect_build is None:
        parser.error("abandon-stream requires --expect-build")
    # Never send TX or bootloader commands, even in exception cleanup.
    try:
        with serial_lock(args.port), open_port(SimpleNamespace(port=args.port)) as port:
            link = LiveLink(port, timeout=2)
            if args.action == "observe":
                if args.expect_build:
                    checked_rx(link, expected_id=args.expect_build)
                print("Observation only: manually operate the hardware ONLY with an "
                      "independent RF monitor and rated dummy load.", flush=True)
                result = observe(link, duration=args.duration,
                                 interval=args.interval, output=args.output)
            else:
                snapshot = args.backup.expanduser().read_bytes()
                result = abandon_stream(link, port, snapshot,
                                        expected_id=args.expect_build,
                                        count=args.count)
                if args.output is not None:
                    with args.output.expanduser().open("x", encoding="utf-8") as out:
                        out.write(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2), flush=True)
    except (InstallError, OSError, ValueError) as exc:
        parser.exit(1, f"Acceptance aborted: {exc}\n")


if __name__ == "__main__":
    main()
