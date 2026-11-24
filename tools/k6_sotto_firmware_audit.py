#!/usr/bin/env python3
"""Fail-closed offline release-preflight for the Flux Sotto-dev build artifact.

The image is NOT approved for flashing by this audit. It only proves
a deterministic linked ELF/binary within the KD32F328 *application* budget,
and checks that the real Sotto decoder plus guarded C wire ABI were built.

No serial/radio/SDR/flash access. Does not assert real-time B-chip RX/TX.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from k6_flash_policy import flash_bytes, TOTAL

ROOT=Path(__file__).resolve().parents[1]
OUTPUT=ROOT/"firmware/k6-aura/out-fd6818b-sotto"
IMAGE="flux-fd6818b-sotto.bin"
REQUIRED_LINKED=("sp_init", "sp_packet", "sp_play_start", "sp_play_pwm",
                 "fx_oa_block", "sotto_dec_create")
REQUIRED_ARCHIVE=("sp_cb_setup", "flux_dv_wire_rx66", "flux_dv_wire_session_tx",
                  "flux_dv_wire_session_rx66", "flux_dv_wire_push",
                  "flux_dv_wire_finish", "flux_dv_wire_ack")


def symbols(path: Path) -> set[str]:
    r=subprocess.run(["llvm-nm",str(path)],check=True,capture_output=True,
                     text=True,timeout=20)
    return {line.rsplit(" ",1)[-1].strip() for line in r.stdout.splitlines()
            if line.strip() and len(line.split())>=2}


def inspect(out: Path=OUTPUT, *, image_name: str=IMAGE,
            elf: Path|None=None) -> dict:
    out=Path(out)
    image=out/image_name
    size=out/"size.txt"
    archive=out/"c-build/libsotto_k6.a"
    if elf is None:
        elf=out/"flux-fd6818b-sotto.elf"
    checks=(image,size,archive,Path(elf),image.with_suffix(image.suffix+".sha256"))
    missing=[str(x) for x in checks if not x.is_file()]
    if missing:
        raise ValueError("Missing compiled Sotto artifacts: "+", ".join(missing))
    budget=flash_bytes(size.read_text())
    actual=image.stat().st_size
    if not 0<budget<=TOTAL or actual!=budget:
        raise ValueError(f"Unsafe Sotto image size {actual}; linked budget {budget}; limit {TOTAL}")
    digest=hashlib.sha256(image.read_bytes()).hexdigest()
    sha_file=image.with_suffix(image.suffix+".sha256").read_text().split()
    if not sha_file or sha_file[0]!=digest:
        raise ValueError("Firmware SHA file does not match linked output")
    live=symbols(elf)
    required=set(REQUIRED_LINKED)
    if not required<=live:
        raise ValueError("Sotto ELF missing required decoder ABI: "+", ".join(sorted(required-live)))
    static=symbols(archive)
    wire=set(REQUIRED_ARCHIVE)
    if not wire<=static:
        raise ValueError("Sotto archive missing guarded wire ABI: "+", ".join(sorted(wire-static)))
    return {
        "target":"Flux B-chip Sotto-1200 development firmware",
        "image_path":str(image),
        "image_sha256":digest,
        "image_bytes":actual,
        "max_application_bytes":TOTAL,
        "flash_headroom_bytes":TOTAL-actual,
        "linked_sotto_decoder_symbols":sorted(required),
        "guarded_wire_archive_symbols":sorted(wire),
        "build_only":True,
        "hardware_flash_approval":False,
        "native_bchip_fsk_rx_proven":False,
        "on_radio_sotto_dv_proven":False,
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out",type=Path,default=OUTPUT)
    ap.add_argument("--image",default=IMAGE)
    ap.add_argument("--elf",type=Path)
    args=ap.parse_args()
    print(json.dumps(inspect(args.out,image_name=args.image,elf=args.elf),indent=2))


if __name__=="__main__":
    main()
