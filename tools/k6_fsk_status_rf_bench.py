#!/usr/bin/env python3
"""Hash-pinned one-shot RF capture for the read-only FSK status probe."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
sys.path.insert(0,str(ROOT))
from k6_fsk_boundary_bench import run_transaction as bounded_transaction
from modem import k6mod

SOURCE=ROOT/"c/k6/mod/fsk_status_probe_rf.c"
EXPECTED_CODE_SHA256="2186deaa27b689c81651ac8bfb914fb6e647a83d12e7d62d05044a13a4991823"

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",default="/dev/ttyUSB0")
    p.add_argument("--prefix",required=True,type=Path)
    p.add_argument("--slot-backup",required=True,type=Path)
    p.add_argument("--dummy-load-confirmed",action="store_true")
    a=p.parse_args()
    code,entry=k6mod.build(str(SOURCE))
    if entry!=0 or hashlib.sha256(code).hexdigest()!=EXPECTED_CODE_SHA256:
        raise SystemExit("FSK status probe code differs from the frozen RF image")
    r=bounded_transaction(
        a.port,a.prefix,SOURCE,a.slot_backup,armed=a.dummy_load_confirmed,
        expected_result=96,
        experiment="FSK-09S status-only 16/32/48-word B-chip observation",
        watchdog_ms=1800)
    print("Restored:",r["original_module_restored"],
          r["original_radio_frequency_restored"],
          "TX confirmed:",r["transmission_confirmed_by_firmware"])

if __name__=="__main__":
    main()
