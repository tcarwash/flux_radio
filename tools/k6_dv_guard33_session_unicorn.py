#!/usr/bin/env python3
"""Execute compiled Thumb/Cortex-M0 FDV1 control builder/parser in Unicorn.

Uses the pinned offline Cortex-M0 emulator environment from prior Flux work.
No K6 radio, no hardware SPI, no physical SDR. ELF is NOT FLASHABLE.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CM0 = Path("/storage5/dev/flux-mic-digital-voice/sotto/c/fixed/cm0/run.py")
LINK = CM0.parent.parent / "cm4/link.ld"
sys.path.insert(0, str(ROOT))
from tools.k6_dv_guard33_session import Session, BEGIN, END, encode_control, decode_control


def build(out: Path) -> tuple[Path, int]:
    out.mkdir(parents=True, exist_ok=True)
    obj = out/"session-probe.o"
    elf = out/"NOT-FLASHABLE-guard-session-armv6m.elf"
    cc = ["clang", "--target=thumbv6m-none-eabi", "-mcpu=cortex-m0",
          "-mthumb", "-mfloat-abi=soft", "-Oz", "-ffreestanding",
          "-fno-builtin", "-fno-stack-protector", "-std=c11",
          "-Wall", "-Wextra", "-Werror", "-ffunction-sections",
          "-fdata-sections", "-c", str(ROOT/"c/k6/mod/dv_guard33_session_m0_probe.c"),
          "-o", str(obj)]
    subprocess.run(cc, check=True, capture_output=True, timeout=30)
    subprocess.run(["ld.lld", "-T", str(LINK), "--gc-sections",
                    "-e", "dv_arm_session_build",
                    "-u", "dv_arm_session_decode",
                    "-u", "dv_arm_session_select66",
                    "-u", "dv_arm_session_state_bytes", str(obj), "-o", str(elf)],
                   check=True, capture_output=True, timeout=30)
    result = subprocess.run(["llvm-size", str(elf)], check=True,
                            capture_output=True, text=True).stdout.splitlines()[1].split()
    textbytes, data, bss = map(int, result[:3])
    if data or bss or not 100 <= textbytes <= 4096:
        raise RuntimeError(f"Unsafe linked footprint: {textbytes}, {data}, {bss}")
    return elf, textbytes


def run(elf: Path, size: int) -> dict:
    spec = importlib.util.spec_from_file_location("flux_dv_cm0", CM0)
    lib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    lib.WS = 0
    emulator = lib.M0(str(elf))
    IO = lib.cm4.IO
    in_descriptor = IO
    out_words = IO + 0x100
    rx_physical = IO + 0x200
    decoded = IO + 0x300
    offsetaddr = IO + 0x400
    word_count,_ = emulator.call("dv_arm_session_state_bytes")
    if word_count != 16:
        raise RuntimeError(f"Unexpected ARM packed descriptor size {word_count}")
    emulator.paint_stack()

    cases = [
        Session(BEGIN, 0x123456789ABCDEF0, 112),
        Session(END,   0x123456789ABCDEF0, 112,115,2,4),
        Session(BEGIN, 0xFEEDFACE1234ABCD, 255),
        Session(END,   0xFEEDFACE1234ABCD, 255,255,8,1),
        Session(BEGIN, 1, 0),
        Session(END, 1, 0, 143, 1, 144),
    ]
    cycles = []
    allframes = []
    for control in cases:
        raw_desc = struct.pack("<II5B3x", control.tag>>32,
                               control.tag&0xffffffff, control.kind,
                               control.first_id, control.last_id,
                               control.last_frames, control.segments)
        emulator.mu.mem_write(in_descriptor, raw_desc)
        n,c = emulator.call("dv_arm_session_build", in_descriptor, out_words)
        cycles.append(c)
        if n != 33:
            raise RuntimeError("Compiled Thumb builder refused a valid session")
        actual = bytes(emulator.mu.mem_read(out_words, 66))
        expected = encode_control(control)
        if actual != expected:
            raise RuntimeError("Cortex-M0 physical guard control disagrees with Python CRC/PN9")
        allframes.append(actual)
        emulator.mu.mem_write(rx_physical, actual[:64])
        result,c = emulator.call("dv_arm_session_decode", rx_physical, decoded)
        cycles.append(c)
        if result != 1 or bytes(emulator.mu.mem_read(decoded,13)) != raw_desc[:13]:
            raise RuntimeError("ARM control parser not byte exact to original descriptor")
        for index, physical in enumerate((actual, (bytes([0xF8,0]) + actual)[:66])):
            emulator.mu.mem_write(rx_physical, physical)
            n,c = emulator.call("dv_arm_session_select66",
                                rx_physical, decoded, offsetaddr)
            cycles.append(c)
            off = int.from_bytes(emulator.mu.mem_read(offsetaddr,4),"little")
            if n != 1 or off != (2 if index else 0) or bytes(emulator.mu.mem_read(decoded,13)) != raw_desc[:13]:
                raise RuntimeError(f"ARM guard select mismatch case={control} idx={index} n={n} off={off} got={bytes(emulator.mu.mem_read(decoded,16)).hex()} want={raw_desc.hex()}")
            from tools.k6_dv_guard33_session import select_guarded
            oracle = select_guarded(physical)
            if oracle["packet_type"] != "control" or oracle["offset_bytes"] != off:
                raise RuntimeError("Python independent control receiver disagrees")
    # Fail closed on CRC-flipped and invalid frame.
    bad = bytearray(allframes[0])
    bad[22] ^= 0x20
    emulator.mu.mem_write(rx_physical, bytes(bad))
    n,c = emulator.call("dv_arm_session_select66", rx_physical, decoded, offsetaddr)
    cycles.append(c)
    if n != 0:
        raise RuntimeError("ARM CRC corruption incorrectly accepted")

    return {
        "qualifier": "ACTUAL Thumb/Cortex-M0 Unicorn 2.1.4 emulator, not radio hardware",
        "tested_valid_session_records": len(cases),
        "tested_guarded_normal_and_shifted": len(cases)*2,
        "rejected_corrupt_control": True,
        "all_arm_control_payloads_match_python": True,
        "arm_control_text_rodata_bytes": size,
        "control_abi_bytes": word_count,
        "max_emulator_cycles_per_call": max(cycles),
        "emulator_stack_highwater_bytes": emulator.stack_used(),
        "control_wire_sha256": hashlib.sha256(b"".join(allframes)).hexdigest(),
        "no_hardware_access": True,
        "not_flashable": True,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--build-dir", required=True, type=Path)
    args = p.parse_args()
    elf,size = build(args.build_dir)
    report = run(elf,size)
    print(json.dumps(report,indent=2))


if __name__ == "__main__":
    main()
