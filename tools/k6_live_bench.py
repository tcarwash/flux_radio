#!/usr/bin/env python3
"""Non-transmitting hardware acceptance test. Never enters the loader or flashes firmware.

By default all requests are read-only, including a negotiated synthetic uplink.
An explicit empty --exercise-slot and --backup are required for module mutations.
On any failure leave the radio alone and inspect the saved report/envelopes.
"""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import time

from k6_install import AUTO_SLOTS, LIB_BASE, InstallError, Serials, backup, k6mod, live_ready, open_port
from k6_live import LiveLink
from k6_port_lock import serial_lock
from k6_user_store_restore import persist

# Thumb: movs r0,#42; bx lr. No calls, no writes, no RF operations.
RETURN_42=b"\x2a\x20\x70\x47"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validate(link, folder, report, *, expected_build=None, key_file=None, take_backup=False, slot=None):
    def step(name, **fields):
        report["steps"].append(dict(name=name, **fields))
        persist(folder/"report.json",json.dumps(report,indent=2).encode())
    def require_idle():
        state=link.status()
        if not state["standby_idle"] or state["transmitting"] or state["sleeping"]:
            raise InstallError("Return radio to awake idle standby; test stopped")
        return state
    before=require_idle()
    features=link.capabilities()["features"]
    info=link.info()
    identity=info["build_id"].hex() if info["build_id"] else None
    step("identity",build_id=identity,features=features,status=before,protocol=info["protocol"])
    if expected_build and identity!=expected_build.lower():
        raise InstallError("Unexpected firmware build ID")
    if not {"bulk_read","fast_upload"}<=set(features):
        raise InstallError("Firmware lacks negotiated transport")
    started=time.monotonic()
    rate=link.negotiate_download()
    data=link.read(0,8192)
    step("download",baud=getattr(link,"_download_rate",rate),negotiated_baud=rate,bytes=len(data),sha256=sha(data),seconds=time.monotonic()-started)
    started=time.monotonic()
    rate=link.negotiate_upload(features)
    step("upload_probe",baud=rate,seconds=time.monotonic()-started)
    cal=link.read(0xf000,4096)
    if link.read(0xf000,4096)!=cal:
        raise InstallError("Calibration read differs")
    step("calibration",sha256=sha(cal))
    if take_backup:
        started=time.monotonic()
        raw=backup(link,folder/"spi-before.bin")
        step("spi_backup",baud=getattr(link,"_download_rate",None),bytes=len(raw),sha256=sha(raw),seconds=time.monotonic()-started)
    if slot is not None:
        if not take_backup or not expected_build:
            raise InstallError("Mutation test requires --backup and --expect-build-id")
        if not {"ram_run","ram_status","run","lib_mutate","module_caps"}<=set(features):
            raise InstallError("Firmware lacks the full module test interface")
        caps,key,limit=live_ready(link,key_file)
        directory=link.library_directory()
        if slot not in AUTO_SLOTS or slot in directory:
            raise InstallError("Choose an empty ordinary module slot")
        serials=Serials()
        serials.advance_after(link.serial_watermark())
        if serials.n+3>0xffffffff:
            raise InstallError("Serial exhausted")
        ram=k6mod.image(RETURN_42,0,serials.next(),key,level=1)
        persist(folder/"ram.bin",ram)
        result=link.run_ram(ram)
        if result["result"]!=42 or link.ram_status(result["job_id"])["result"]!=42:
            raise InstallError("Unexpected temporary module result")
        step("ram_execute",job_id=result["job_id"],result=42)
        install=k6mod.image(RETURN_42,0,serials.next(),key,level=1,lib=slot)
        persist(folder/"install.bin",install)
        step("install_attempt",slot=slot,sha256=sha(install))
        result=link.mutate_library(install)
        step("install_verified",**result)
        result=link.run_module(slot)
        if not result["returned"] or result["result"]!=42:
            raise InstallError("Unexpected installed module result")
        step("installed_execute",slot=slot,result=42)
        delete=k6mod.image(b"K6ERASE",0,serials.next(),key,level=1,lib=slot,remove=True)
        persist(folder/"remove.bin",delete)
        step("remove_attempt",slot=slot,sha256=sha(delete))
        step("remove_verified",**link.mutate_library(delete))
        # Logical allocation of every pre-existing object must be unchanged.
        if link.library_directory()!=directory:
            raise InstallError("Unexpected logical directory change")
        for oldslot,(address,length) in directory.items():
            expected=raw[LIB_BASE+address:LIB_BASE+address+length]
            if link.library_read(oldslot,length)!=expected:
                raise InstallError(f"Existing object {oldslot} changed")
        if link.serial_watermark()!=serials.n:
            raise InstallError("Unexpected replay watermark (possible reboot)")
        step("library_preserved",objects=len(directory),watermark=serials.n)
    after=require_idle()
    if before["vfos"]!=after["vfos"] or before["master"]!=after["master"]:
        raise InstallError("VFO configuration changed")
    if link.read(0xf000,4096)!=cal:
        raise InstallError("Calibration changed")
    step("final",status=after)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",required=True)
    p.add_argument("--output-dir",required=True,type=Path,help="new directory; contains report and optional private SPI backup")
    p.add_argument("--expect-build-id",help="exact build ID (required for exercise)")
    p.add_argument("--key-file")
    p.add_argument("--backup",action="store_true")
    p.add_argument("--exercise-slot",type=int,choices=AUTO_SLOTS,help="explicitly authorize a temporary install/run/remove in an empty slot")
    a=p.parse_args(argv)
    if a.exercise_slot is not None and not (a.backup and a.expect_build_id):
        p.error("--exercise-slot requires --backup and --expect-build-id")
    a.output_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    report=dict(port=a.port,steps=[],ok=False)
    try:
        with serial_lock(a.port),open_port(SimpleNamespace(port=a.port)) as port:
            validate(LiveLink(port),a.output_dir,report,expected_build=a.expect_build_id,
                     key_file=a.key_file,take_backup=a.backup,slot=a.exercise_slot)
        report["ok"]=True
    except (InstallError,OSError,ValueError) as exc:
        report["error"]=str(exc)
        print(f"Stopped: {exc}. No automatic cleanup/retry; inspect report and saved envelopes.")
    finally:
        persist(a.output_dir/"report.json",json.dumps(report,indent=2).encode())
    print(a.output_dir/"report.json")
    return 0 if report["ok"] else 1


if __name__=="__main__":
    raise SystemExit(main())
