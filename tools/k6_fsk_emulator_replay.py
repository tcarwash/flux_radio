#!/usr/bin/env python3
"""Run an actual Cortex-M0 polling module against archived/synthetic IQ, offline."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/"tests")]
import numpy as np
from k6_live_emu import Emu
from modem import k6mod, pager
from sim.k6emu.rf.backend import create_backend


def replay(source, *, rate, center, tune, duration):
    if not source.startswith(("npy:", "npy-u8:")) or not math.isfinite(duration) or not 0 < duration <= 120:
        raise ValueError("offline NPY replay requires a duration of 0–120 seconds")
    code, entry = k6mod.build(str(ROOT/"tests/data/k6/fsk_rx_emu.c"))
    b = create_backend(source, rate=rate, center_hz=center)
    try:
        emu = Emu(code, entry, np.zeros(1, dtype=np.uint8), 0, 1,
                  max_s=duration+.5, rf_backend=b, rf_hz=tune, level=2)
        emu.mu.mem_write(emu.scr, round(duration*1000).to_bytes(4,"little"))
        result = emu.run()
        if result is None:
            raise RuntimeError("module did not return")
        count, stack = result
        if not 0 <= count <= (emu.scratch-8)//64:
            raise RuntimeError("invalid module frame count")
        raw = bytes(emu.mu.mem_read(emu.scr+4, 4+64*count))
        if int.from_bytes(raw[:4],"little") != count:
            raise RuntimeError("module result count mismatch")
        frames = [raw[4+i*64:4+(i+1)*64] for i in range(count)]
        return frames, dict(module_return=count, stack_bytes=stack,
                            virtual_ms=round(emu.cyc/96000,3), fsk=b.chip.fsk.status())
    finally:
        b.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", required=True, help="npy:PATH or npy-u8:PATH")
    p.add_argument("--rate", required=True, type=float)
    p.add_argument("--center", required=True, type=float, help="effective IQ center, including known receiver offset")
    p.add_argument("--tune", required=True, type=int)
    p.add_argument("--duration", type=float, default=2.5)
    args = p.parse_args()
    if not args.source.startswith(("npy:", "npy-u8:")):
        p.error("only NPY recordings supported")
    frames, stats = replay(args.source, rate=args.rate, center=args.center, tune=args.tune, duration=args.duration)
    profile = pager.profile("L","1200",depth=1,parity=False,short_last=True)
    report = dict(**stats, crc_valid=sum(profile.unpack(x) is not None for x in frames),
                  first_words=[f"{int.from_bytes(x[:2],'little'):04x}" for x in frames],
                  payload_sha256=[hashlib.sha256(x).hexdigest() for x in frames],
                  rate=args.rate, center=args.center, tune=args.tune, duration=args.duration)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
