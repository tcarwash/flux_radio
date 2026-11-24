#!/usr/bin/env python3
"""Linked ARM Sotto API28 callbacks, real CRC cable module and modeled NOR.

This uses the real Rust K6 firmware and compiled C module, not a stubbed
resource service. Does NOT test production keyed module authorization,
physical SPI silicon, live TX or an installed on-radio voice decoder.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from modem import k6mod
from tools.k6_user_store_migrate import compact,BASE,END
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher,linked_symbols
from tools.k6_sotto_rx4800_firmware_gate import matching_image
from tools.k6_sotto_opt_bench import mixed_pcm

SRC=ROOT/"tests/data/k6/sotto_resources31_call_emu.c"
BASE=0x110000
SLOT=14
SLOT_BYTES=0x4000

def run(elf:Path,image:Path,tree:Path):
    with tempfile.TemporaryDirectory(prefix="flux-sotto-resource31-") as t:
        tmp=Path(t)
        sha,firmware_bytes=matching_image(elf,image,tmp)
        art=prepare(tree,tmp,pcm=mixed_pcm())
        code,entry=k6mod.build(str(SRC))
        if len(code)+24>10240:
            raise RuntimeError("Resource API verifier exceeds ARM module code cap")
        fw=Dispatcher(elf,image,art["names"])
        try:
            b=fw.board
            cb=art["cb"]
            deck=art["deck"]
            if len(cb)!=30752 or len(deck)!=4182:
                raise RuntimeError("Incorrect K6CB / K6SC reference resources")
            deck_image=k6mod.image(deck,0,serial=77,key=None,level=31,
                                   lib=SLOT,cards=True)
            b.flash_data[0x100000:0x100000+len(cb)]=cb
            # The Base+Data pack enables K6_USER_STORE in its build config;
            # kl_locate resolves the packed KUL2 directory rather than fixed
            # offsets. Generate both CRC-protected directory copies.
            packed,manifest=compact({SLOT:deck_image})
            assert len(packed)==END-BASE and manifest[0][0]==SLOT
            b.flash_data[BASE:END]=packed
            before_cb=hashlib.sha256(b.flash_data[0x100000:0x100000+len(cb)]).hexdigest()
            before_deck=hashlib.sha256(b.flash_data[BASE:END]).hexdigest()
            chunks,envelope=fw.verified_cable_stage(code,entry)
            fw.execute(expected_return=7,require_rf=False)
            if hashlib.sha256(b.flash_data[0x100000:0x100000+len(cb)]).hexdigest()!=before_cb:
                raise RuntimeError("Resource API mutated codebook SPI flash")
            if hashlib.sha256(b.flash_data[BASE:END]).hexdigest()!=before_deck:
                raise RuntimeError("Resource API mutated library SPI flash")
            return dict(
                result="PASS",firmware_sha256=sha,
                firmware_bytes=firmware_bytes,
                actual_ARM_resource_callbacks=True,
                real_ARM_module_run=True,
                module_return=fw.module_ret[0],
                module_bytes=len(code),module_envelope_bytes=envelope,
                cable_crc_chunks=chunks,
                codebook_bytes=len(cb),deck_bytes=len(deck),
                codebook_sha256=before_cb,deck_envelope_sha256=before_deck,
                API28_offsets_preserved=True,ABI_level=31,
                bounds_check_exercised=True,deck_handle_reset_exercised=True,
                resource_flash_read_only=True,
                packed_KUL2_tested=True,
                virtual_SPI_only=True,physical_hardware_used=False,
            )
        finally:
            fw.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--elf",type=Path,required=True)
    p.add_argument("--image",type=Path,required=True)
    p.add_argument("--sotto-tree",type=Path,required=True)
    p.add_argument("--json",type=Path)
    a=p.parse_args()
    out=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve())
    if a.json:a.json.write_text(json.dumps(out,indent=2)+"\n")
    print(json.dumps(out,indent=2))

if __name__=="__main__":main()
