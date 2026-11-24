#!/usr/bin/env python3
"""Run an actual Cortex-M0 polling module against archived/synthetic IQ, offline."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from functools import lru_cache
import struct

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/"tests")]
import numpy as np
from k6_live_emu import Emu
from modem import k6mod, pager
from sim.k6emu.rf.backend import create_backend


@lru_cache(maxsize=2)
def fixture(name):
    if name not in ("rx", "tx"):
        raise ValueError("unknown fixture")
    return k6mod.build(str(ROOT/f"tests/data/k6/fsk_{name}_emu.c"))


def receive(b, *, tune, duration, frame_bytes=64, threshold=4, poll_us=1000):
    if not math.isfinite(duration) or not 0 < duration <= 120:
        raise ValueError("duration must be 0–120 seconds")
    if not 2 <= frame_bytes <= 256 or frame_bytes%2 or not 1<=threshold<=7 or not 1<=poll_us<=100000:
        raise ValueError("invalid RX parameters")
    code, entry = fixture("rx")
    emu = Emu(code, entry, np.zeros(1, dtype=np.uint8), 0, 1,
              max_s=duration+.5, rf_backend=b, rf_hz=tune, level=2)
    emu.mu.mem_write(emu.scr, struct.pack("<5I", round(duration*1000), 0, frame_bytes, threshold, poll_us))
    result = emu.run()
    if result is None:
        raise RuntimeError("module did not return")
    count, stack = result
    if not 0 <= count <= (emu.scratch-20)//frame_bytes:
        raise RuntimeError("invalid module frame count")
    raw = bytes(emu.mu.mem_read(emu.scr+4, 4))
    if int.from_bytes(raw,"little") != count:
        raise RuntimeError("module result count mismatch")
    data = bytes(emu.mu.mem_read(emu.scr+20, frame_bytes*count)) if count else b""
    frames = [data[i:i+frame_bytes] for i in range(0,len(data),frame_bytes)]
    return frames, dict(module_return=count, stack_bytes=stack,
                        virtual_ms=round(emu.cyc/96000,3), fsk=b.chip.fsk.status())


def replay(source, *, rate, center, tune, duration, frame_bytes=64, threshold=4, poll_us=1000):
    if not source.startswith(("npy:", "npy-u8:")):
        raise ValueError("offline NPY replay only")
    b = create_backend(source, rate=rate, center_hz=center)
    try:
        return receive(b,tune=tune,duration=duration,frame_bytes=frame_bytes,threshold=threshold,poll_us=poll_us)
    finally:
        b.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="npy:PATH or npy-u8:PATH")
    p.add_argument("--rate", required=True, type=float)
    p.add_argument("--center", required=True, type=float, help="effective IQ center, including known receiver offset")
    p.add_argument("--tune", required=True, type=int)
    p.add_argument("--duration", type=float, default=2.5)
    p.add_argument("--frame-bytes", type=int, default=64)
    p.add_argument("--threshold", type=int, default=4)
    p.add_argument("--poll-us", type=int, default=1000)
    args = p.parse_args()
    if not args.source.startswith(("npy:", "npy-u8:")):
        p.error("only NPY recordings supported")
    frames, stats = replay(args.source, rate=args.rate, center=args.center, tune=args.tune, duration=args.duration,
                           frame_bytes=args.frame_bytes,threshold=args.threshold,poll_us=args.poll_us)
    profile = pager.profile("L","1200",depth=1,parity=False,short_last=True)
    report = dict(**stats, crc_valid=sum(profile.unpack(x) is not None for x in frames),
                  guarded_crc_valid=(sum(sum(profile.unpack(x[o:o+64]) is not None for o in (0,2))==1 for x in frames)
                                     if args.frame_bytes==66 else None),
                  first_words=[f"{int.from_bytes(x[:2],'little'):04x}" for x in frames],
                  payload_sha256=[hashlib.sha256(x).hexdigest() for x in frames],
                  rate=args.rate, center=args.center, tune=args.tune, duration=args.duration,
                           frame_bytes=args.frame_bytes,threshold=args.threshold,poll_us=args.poll_us)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
