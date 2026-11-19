#!/usr/bin/env python3
"""Build a signed-on-install CARD LINK module bundle for the FD6818B Flux host."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from modem import k6mod


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path,
                        default=ROOT / "firmware/k6-aura/out-fd6818b/cardlink-module-bundle")
    parser.add_argument("--slot", type=int, default=8,
                        help="SPI module-library slot (default: 8, outside the current test slots)")
    args = parser.parse_args()
    source = ROOT / "c/k6/mod/cardlink.c"
    code, entry = k6mod.build(str(source))
    name = k6mod.module_name(code)
    if name != "CARD LINK" or k6mod.source_level(str(source)) != 24:
        raise SystemExit("CARD LINK source name/API level did not match the image contract")
    if len(code) + k6mod.HDR + k6mod.TAG > k6mod.LIB_SLOT_BYTES:
        raise SystemExit("CARD LINK does not fit in one SPI library slot")
    args.out.mkdir(parents=True, exist_ok=True)
    filename = "mod-cardlink.bin"
    (args.out / filename).write_bytes(code)
    digest = hashlib.sha256(code).hexdigest()
    manifest = {
        "format": 1,
        "build": "FD6818B CARD LINK (whole PXK6 over acknowledged FSK)",
        "build_id": "0000000000000000",
        "library": [{"slot": args.slot, "kind": "module", "file": filename,
                      "name": name, "level": 24, "entry": entry}],
        "sha256": {filename: digest},
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"slot {args.slot}: {filename}, {len(code)} bytes, entry={entry}, sha256={digest}")


if __name__ == "__main__":
    main()
