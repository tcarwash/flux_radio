#!/usr/bin/env python3
"""FSK-09S-G: one fully preloaded 60-word control frame (prospective RF).

Inert source is default. Armed source and code hashes are frozen and a
hardware transaction requires BOTH explicit operator approval and dummy
load confirmation. Never run from the research queue automatically.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from modem import k6mod
from k6_fsk_boundary_bench import run_transaction

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "c/k6/mod/fsk_preload60_control_armed.c"
SOURCE_SHA = "c3982e2a553949472d6aef6d84d7c415dbfec3820ffc6004b6b002237ad0e27e"
CODE_SHA = "566d23eb2211bad873e671302f3d06770a454acdca0ce40a5c66b216d2b2c217"


def frozen_build():
    s = SOURCE.read_text()
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    if digest != SOURCE_SHA:
        raise RuntimeError("Frozen source mismatch: refuse TX")
    markers = (
        "#define FSK_BOUNDARY_ENABLE 1",
        "#define FSK_BOUNDARY_FIXTURE_ACK 1",
        "#define FSK_BOUNDARY_WATCHDOG_ACK 1",
        "#define FSK_WORDS 60u",
        "#define FSK_NUM_FRAMES 1u",
        "#define FSK_MAX_TX_MS 1900u",
        "W(R_LENGTH,0x7700u);",
        "for(i=0;i<FSK_WORDS;i++)",
        "W(R_FIFO,word_for(frame,i));",
    )
    for marker in markers:
        if marker not in s:
            raise RuntimeError("Missing bounded RF invariant: " + marker)
    if "for(batch=" in s or "wait_until(" in s or "fd_read(R_FIFO)" in s:
        raise RuntimeError("Control source unexpectedly contains live FIFO operations")
    code, entry = k6mod.build(str(SOURCE))
    if (len(code) != 1056 or entry != 0
            or k6mod.source_level(str(SOURCE)) != 3
            or hashlib.sha256(code).hexdigest() != CODE_SHA):
        raise RuntimeError("Compiled armed module mismatch; refuse TX")
    return {
        "source_sha256": SOURCE_SHA,
        "code_sha256": CODE_SHA,
        "code_bytes": len(code),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--prefix", required=True, type=Path)
    p.add_argument("--port", default="/dev/ttyUSB0")
    p.add_argument("--dummy-load-confirmed", action="store_true")
    p.add_argument("--operator-lease-confirmed", action="store_true")
    a = p.parse_args()
    if not a.dummy_load_confirmed or not a.operator_lease_confirmed:
        p.error("Requires explicit operator lease AND physically verified dummy load")
    print("Frozen FSK-09S-G control", frozen_build(), flush=True)
    report = run_transaction(
        a.port, a.prefix, SOURCE,
        a.prefix.with_name(a.prefix.name + "-slot30-before.k6m"),
        armed=True, expected_result=60,
        experiment="FSK-09S-G: one 60-word fully preloaded native-FSK control",
        watchdog_ms=1900,
    )
    if not report["transmission_confirmed_by_firmware"]:
        raise RuntimeError("Incomplete frame; preserve SDR and do not retry")
    print("Control TX complete, original slot and VFOs restored", flush=True)


if __name__ == "__main__":
    main()
