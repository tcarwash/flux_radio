#!/usr/bin/env python3
"""One-shot Sotto FDV1 fixture qualification; existing firmware, no MCU flash.

Freeze actual compiled code and packet expectations before bench access.
The independent public-sync receiver never sees expected packet bytes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod,pager
from k6_fsk_boundary_bench import run_transaction,EXPECTED_BUILD
from tools.k6_dv_guard33_session import receive_iq,packet_in_observation,encode_control,Session
from k6_fsk_iq_edges import read_iq

SRC=ROOT/"c/k6/mod/sotto_tx_fixture.c"
FLAGS=["-DSOTTO_RF_BENCH=1","-DSOTTO_RF_FIXTURE_ACK=1","-DSOTTO_RF_WATCHDOG_ACK=1"]
def sha(b):return hashlib.sha256(b).hexdigest()
def fixture():
    bits=bytes(int(x,16) for x in re.findall(r"0x([0-9a-f]{2})",(SRC.parent/"sotto_tx_fixture.inc").read_text()))
    assert len(bits)==156
    tag=0x534f54544f544553
    expected=[encode_control(Session(1,tag,112))[:64]]
    for i in range(4):
        part=bits[48*i:48*(i+1)]
        plain=bytearray(64)
        plain[:6]=bytes([0xa9,112+i,0xc0,0,0,len(part)//6])
        plain[6:6+len(part)]=part
        plain[62:]=pager.crc16_ccitt(plain[:62]).to_bytes(2,"big")
        expected.append(bytes(x^y for x,y in zip(plain,pager.pn9(64))))
    expected.append(encode_control(Session(2,tag,112,115,2,4))[:64])
    return bits,expected

def freeze():
    code,entry=k6mod.build(str(SRC),cflags=[*k6mod.CFLAGS,*FLAGS])
    assert len(code)==2344 and entry==16
    bits,expected=fixture()
    files=[SRC,SRC.parent/"sotto_tx_fixture.inc",SRC.parent/"sotto_guard_tx.h",
           SRC.parent/"sotto_tx_stage.h",SRC.parent/"dv_guard33_session.h",
           SRC.parent/"dv_guard33_packet.h",SRC.parent/"dv_guard33_stream.h",
           SRC.parent.parent/"k6_mod.h",ROOT/"modem/k6mod.py",
           ROOT/"tools/k6_fsk_boundary_bench.py",ROOT/"tools/k6_fsk_rf_record.py",
           ROOT/"tools/k6_dv_guard33_session.py",ROOT/"tools/k6_fsk_sotto_sync_rx.py",
           ROOT/"tools/k6_fsk_iq_edges.py",ROOT/"tools/k6_rf_matrix_decode.py",
           ROOT/"modem/pager.py",Path(__file__)]
    return dict(build_id=EXPECTED_BUILD,code_sha256=sha(code),code_bytes=len(code),
                entry=entry,api_level=5,tx_timeout_ms=3900,frame_timeout_ms=300,
                iq_duration_s=5.0,iq_shift_hz=280500.,frames=6,
                expected_packet_hashes=[sha(p) for p in expected],codec_sha256=sha(bits),
                source_hashes={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files}), (code,entry)

def score(raw,report,frozen):
    rx=receive_iq(read_iq(raw,duration=frozen["iq_duration_s"],offset=frozen["iq_shift_hz"]))
    fail=[]
    rows=rx["frames"]
    if rx["frames_detected"]!=6 or len(rows)!=6:fail.append("wrong frame count")
    parsed=[]
    for i,r in enumerate(rows):
        try:info,data=packet_in_observation(r)
        except (ValueError,KeyError):fail.append(f"frame {i} unqualified");continue
        parsed.append(info)
        if i>=6 or sha(data)!=frozen["expected_packet_hashes"][i]:
            fail.append(f"frame {i} payload mismatch")
    if not report["transmission_confirmed_by_firmware"] or report["tx_module_requests"]!=1:
        fail.append("module completion not qualified")
    if not report["original_module_restored"] or not report["original_radio_frequency_restored"]:
        fail.append("restoration incomplete")
    result=dict(passed=not fail,failures=fail,frames_detected=rx["frames_detected"],
                crc_valid=len(parsed),packet_types=[r["packet_type"] for r in parsed],
                offsets=[r.get("offset_bytes") for r in rows],sync_errors=[r.get("sync_errors") for r in rows],
                iq_sha256=sha(raw.read_bytes()))
    return result,rx

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freeze",type=Path)
    p.add_argument("--manifest",type=Path)
    p.add_argument("--prefix",type=Path)
    p.add_argument("--operator-lease-confirmed",action="store_true")
    p.add_argument("--dummy-load-confirmed",action="store_true")
    args=p.parse_args()
    frozen,compiled=freeze()
    if args.freeze:
        with args.freeze.open("x") as f:json.dump(frozen,f,indent=2)
        print(json.dumps(frozen,indent=2));return
    if not(args.manifest and args.prefix and args.operator_lease_confirmed and args.dummy_load_confirmed):
        p.error("frozen manifest, prefix and both explicit confirmations required")
    if json.loads(args.manifest.read_text())!=frozen:p.error("manifest mismatch; no TX")
    os.umask(0o077)
    args.prefix.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if args.prefix.parent.stat().st_mode&0o077:p.error("private output directory required")
    report=run_transaction("/dev/ttyUSB0",args.prefix,SRC,
        args.prefix.with_name(args.prefix.name+"-slot30-before.k6m"),armed=True,
        expected_result=6,experiment="Sotto FDV1 six-frame fixture",watchdog_ms=3900,
        compiled=compiled,api_level=5)
    if not args.prefix.with_suffix(".npy").exists():
        raise RuntimeError("No RF capture; do not retry")
    verdict,rx=score(args.prefix.with_suffix(".npy"),report,frozen)
    for name,obj in (("-rx.json",rx),("-score.json",verdict)):
        with args.prefix.with_name(args.prefix.name+name).open("x") as f:json.dump(obj,f,indent=2)
    print(json.dumps(verdict,indent=2))
    if not verdict["passed"]:sys.exit(1)
if __name__=="__main__":main()
