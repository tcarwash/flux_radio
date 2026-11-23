#!/usr/bin/env python3
"""FSK-12: RF qualify packets made from a captured physical K6 microphone.

A frozen four-packet experimental transmitter using the existing *bounded*
native-FSK RF bench transaction, retaining the prior SDR and restoration
harness. Microphone bytes and generated C source remain PRIVATE outside Git.
Preparations/verification are entirely offline. Radio use requires explicit
command and shared bench lease; the queue never launches it automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from modem import k6mod, pager
from k6_fsk_boundary_bench import run_transaction
from k6_fsk_iq_edges import read_iq
from k6_fsk_sotto_sync_rx import find_frames

ROOT = Path(__file__).resolve().parents[1]
SOURCE_TEMPLATE = ROOT / "c/k6/mod/fsk_sotto_l8_burst_armed.c"
PACKET_SHA = "d81956bf1ae57747eaf8353edd2d10453b530408a61e781600ee8c7dc05d8bd6"
PACKET_COUNT = 4
PACKET_BYTES = 64
FSK_BAUD = 4800
SOTTO_MSG = 93
MAX_CODE_BYTES = 4096

# Filled after offline private-source preparation, committed BEFORE any RF.
FROZEN_SOURCE_SHA = "b1cf48083d990a853054a4ec36797d5c5ffbc53e0a7c9b690193d4d66131dd56"
FROZEN_CODE_SHA = "46a7c785297f91428b09496bf251ca96fe744cf6794464e0ca23503020ffd77a"
FROZEN_CODE_BYTES = 1196


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def packets_from_file(path: Path) -> list[bytes]:
    b = path.read_bytes()
    if sha_bytes(b) != PACKET_SHA or len(b) != PACKET_COUNT * PACKET_BYTES:
        raise ValueError("Private mic packet file differs from offline FSK-12 fixture")
    p = pager.profile("L", "1200", depth=1, parity=False, short_last=True)
    packets = [b[i:i+PACKET_BYTES] for i in range(0, len(b), PACKET_BYTES)]
    for i, pkt in enumerate(packets):
        q = p.unpack(pkt)
        if q is None or q["msg_id"] != SOTTO_MSG or q["seq"] != i or q["parity"] is not None:
            raise ValueError(f"Packet {i}: CRC/header/sequence mismatch")
    assm = pager.Assembler(p)
    for pkt in packets:
        assm.push(pkt)
    voice, good = assm.frames(SOTTO_MSG)
    if len(good) != 26 or not good.all() or len(voice) != 26 * 6:
        raise ValueError("Real microphone compressed voice reassembly mismatch")
    return packets


def render_source(packets: list[bytes]) -> str:
    if len(packets) != PACKET_COUNT:
        raise ValueError("Must render exactly four packets")
    src = SOURCE_TEMPLATE.read_text()
    if "#define FSK_NUM_FRAMES 8u" not in src:
        raise ValueError("Unknown original transmitter source")
    src = src.replace("#define FSK_NUM_FRAMES 8u", "#define FSK_NUM_FRAMES 4u")
    src = src.replace('#include "../k6_mod.h"', '#include "k6_mod.h"')
    src = src.replace('#include "fsk_lab_vector.h"\n', "")
    src = src.replace("FSK-10B prospective eight-packet Sotto 1200 blind RF burst",
                      "FSK-12 prospective private K6-microphone-derived Sotto L64 RF burst")
    src = src.replace("FSK-10B", "FSK-12")
    src = src.replace("fsk10B:", "fsk12:")
    words = []
    for packet in packets:
        w = [int.from_bytes(packet[i:i+2], "little") for i in range(0, 64, 2)]
        words.append("    {\n" + "".join("        "+", ".join(f"0x{x:04x}u" for x in w[i:i+8])+",\n"
                                           for i in range(0, 32, 8))+"    },")
    start = src.index("static const uint16_t sotto_packet_words[8][32] = {")
    stop = src.index("static uint16_t word_for(", start)
    src = (src[:start] + "static const uint16_t sotto_packet_words[4][32] = {\n"
           + "\n".join(words) + "\n};\n" + src[stop:])
    if not all(x in src for x in (
        "#define FSK_BOUNDARY_ENABLE 1",
        "#define FSK_BOUNDARY_FIXTURE_ACK 1",
        "#define FSK_BOUNDARY_WATCHDOG_ACK 1",
        "#define FSK_WORDS 32u",
        "#define FSK_NUM_FRAMES 4u",
        "#define FSK_MAX_TX_MS 1900u",
        "W(R_LENGTH,0x3f00u);",
    )):
        raise ValueError("Generated source lacks a bounded TX invariant")
    if "wait_until(" in src or "fd_read(R_FIFO)" in src:
        raise ValueError("This finite test may not depend on FIFO refill")
    return src


def private_source_path(directory: Path) -> Path:
    return directory / "fsk12_k6mic_private_armed.c"


def prepare(packet_file: Path, directory: Path) -> dict:
    if directory.exists():
        raise FileExistsError("Refuse to overwrite private prep directory")
    packets = packets_from_file(packet_file)
    source = render_source(packets)
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    path = private_source_path(directory)
    path.write_text(source)
    path.chmod(0o600)
    code, entry = k6mod.build(str(path))
    if not (0 < len(code) < MAX_CODE_BYTES) or entry != 0 or k6mod.source_level(str(path)) != 3:
        raise ValueError("Generated radio module incompatible with K6 API3")
    attestation = {
        "experiment": "FSK-12 mic-originated Sotto native-FSK four finite frames",
        "private_packet_file": str(packet_file),
        "private_source_file": str(path),
        "packet_file_sha256": PACKET_SHA,
        "packet_count": PACKET_COUNT,
        "packet_sha256": [sha_bytes(p) for p in packets],
        "packet_message_id": SOTTO_MSG,
        "payload_baud": FSK_BAUD,
        "source_sha256": sha_bytes(source.encode()),
        "compiled_module_sha256": sha_bytes(code),
        "compiled_module_bytes": len(code),
        "compiled_entry": entry,
        "rf_test_executed": False,
        "source_bytes_not_committed": True,
    }
    report = directory / "fsk12_private_preflight.json"
    report.write_text(json.dumps(attestation, indent=2)+"\n")
    report.chmod(0o600)
    return attestation


def frozen_preflight(packet_file: Path, directory: Path) -> dict:
    packets = packets_from_file(packet_file)
    path = private_source_path(directory)
    rendered = render_source(packets)
    actual = path.read_bytes()
    if actual != rendered.encode():
        raise ValueError("Frozen private source was modified")
    code, entry = k6mod.build(str(path))
    source_hash = sha_bytes(actual)
    code_hash = sha_bytes(code)
    m = json.loads((directory / "fsk12_private_preflight.json").read_text())
    if (m["source_sha256"] != source_hash or
            m["compiled_module_sha256"] != code_hash or
            m["compiled_module_bytes"] != len(code) or
            m["packet_file_sha256"] != PACKET_SHA or
            m["packet_count"] != 4 or entry != 0):
        raise ValueError("Private prospective manifest/build mismatch")
    if not FROZEN_SOURCE_SHA or not FROZEN_CODE_SHA or not FROZEN_CODE_BYTES:
        raise RuntimeError("RF path NOT frozen yet: pin hashes in committed script")
    if (source_hash != FROZEN_SOURCE_SHA or code_hash != FROZEN_CODE_SHA
            or len(code) != FROZEN_CODE_BYTES):
        raise RuntimeError("Source/compiled module differs from precommitted RF fixture")
    return m


def analyze(iq_file: Path, out: Path, packet_file: Path) -> dict:
    expected = packets_from_file(packet_file)
    if out.exists():
        raise FileExistsError("Analysis output exists; no overwrite")
    with iq_file.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    iq = read_iq(iq_file, duration=2., offset=280500.)
    # Receiver is entirely independent of expected microphone payload.
    received = find_frames(iq, rate_factors=(1.,), min_correlation=.95)
    decoded = [bytes.fromhex(x["raw_payload_hex"]) for x in received["packets"]]
    profile = pager.profile("L","1200",depth=1,parity=False,short_last=True)
    valid = [profile.unpack(x) for x in decoded]
    strict = (len(decoded) == PACKET_COUNT
              and all(v is not None for v in valid)
              and all(v["msg_id"] == SOTTO_MSG and v["seq"] == i for i,v in enumerate(valid))
              and decoded == expected)
    scores = []
    for i,b in enumerate(decoded):
        exp = expected[i] if i < len(expected) else None
        scores.append({
            "frame_index": i,
            "payload_start_s": received["packets"][i]["payload_start_s"],
            "sync_correlation": received["packets"][i]["sync_correlation"],
            "crc_valid": valid[i] is not None,
            "msg_id": valid[i]["msg_id"] if valid[i] else None,
            "sequence": valid[i]["seq"] if valid[i] else None,
            "expected_posthoc_bit_errors": (
                sum((a^c).bit_count() for a,c in zip(b,exp)) if exp else None),
        })
    report = {
        "result": "MICROPHONE_SOTTO_RF_STRICT_PASS" if strict else "FAIL_OR_INCONCLUSIVE",
        "receiver_uses_only_public_preamble_sync": True,
        "sync_frame_definition": "16x0x55 plus 076b3e36",
        "receiver_rate_hz_frozen": FSK_BAUD,
        "expected_packets": PACKET_COUNT,
        "found_packets": len(decoded),
        "crc_valid": sum(x is not None for x in valid),
        "correct_packets_exact": sum(b == e for b,e in zip(decoded,expected)),
        "iq_sha256": digest,
        "private_mic_packet_sha256": PACKET_SHA,
        "frames": scores,
        "limitations": [
            "Still a finite packet replay of previously captured audio, not live mic TX.",
            "Radio decoder and speaker not involved; this is host SDR receive.",
            "No FIFO refill, continuous air-bitstream or on-radio encoder proven.",
        ],
    }
    out.write_text(json.dumps(report,indent=2)+"\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action",required=True)
    p=actions.add_parser("prepare",help="No hardware: generate a private pinned source")
    p.add_argument("--packets",required=True,type=Path)
    p.add_argument("--private-dir",required=True,type=Path)
    p=actions.add_parser("preflight",help="No hardware: verify frozen private fixture")
    p.add_argument("--packets",required=True,type=Path)
    p.add_argument("--private-dir",required=True,type=Path)
    p=actions.add_parser("tx",help="Single guarded conducted RF transaction, no retries")
    p.add_argument("--packets",required=True,type=Path)
    p.add_argument("--private-dir",required=True,type=Path)
    p.add_argument("--prefix",required=True,type=Path)
    p.add_argument("--port",default="/dev/ttyUSB0")
    p.add_argument("--dummy-load-confirmed",action="store_true")
    p.add_argument("--operator-lease-confirmed",action="store_true")
    p=actions.add_parser("analyze",help="Offline independent SDR decoder")
    p.add_argument("--packets",required=True,type=Path)
    p.add_argument("--iq",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    a=parser.parse_args()
    if a.action=="prepare":
        result=prepare(a.packets,a.private_dir)
        print(json.dumps({k:v for k,v in result.items() if k not in
              ("private_packet_file","private_source_file")},indent=2))
    elif a.action=="preflight":
        result=frozen_preflight(a.packets,a.private_dir)
        print("Frozen FSK-12 private source+module verified",result["compiled_module_sha256"])
    elif a.action=="analyze":
        print(json.dumps(analyze(a.iq,a.out,a.packets),indent=2))
    elif a.action=="tx":
        if not (a.dummy_load_confirmed and a.operator_lease_confirmed):
            parser.error("Requires explicit operator lease + confirmed dummy load")
        result=frozen_preflight(a.packets,a.private_dir)
        from pprint import pprint
        pprint({k:result[k] for k in ("source_sha256","compiled_module_sha256")})
        rf=run_transaction(a.port,a.prefix,private_source_path(a.private_dir),
                 a.prefix.with_name(a.prefix.name+"-slot30-before.k6m"),
                 armed=True,expected_result=4*32,
                 experiment="FSK-12 previously captured K6 microphone Sotto packet RF test",
                 watchdog_ms=1900)
        if not rf["transmission_confirmed_by_firmware"]:
            raise RuntimeError("Module return incomplete; do not retry")
        print("RF transaction completed with guarded restoration; decode offline to decide RF PASS")


if __name__ == "__main__":
    main()
