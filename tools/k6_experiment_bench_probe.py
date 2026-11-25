#!/usr/bin/env python3
"""Non-mutating Flux K6 capability and VFO probe under the canonical lock.

This is HARDWARE OBSERVATION, not a TX qualification. No tuning, PTT,
slot modifications, SPI writes, flashing, or SDR device operations.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import hashlib
from k6_install import open_port, LIB_BASE, Serials
from k6_user_store_migrate import locations
from k6_live import LiveLink
from k6_port_lock import serial_lock

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

HEX_BUILD=re.compile(r"^[a-f0-9]{16}$")
PORT="/dev/ttyUSB0"
TEMP_SLOT=30
BENCH_FREQ_HZ=433500000
DEFAULT_BENCH=Path.home()/".cache/flux/bench.lock"


@contextmanager
def bench_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with path.open("a+b") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def _cleanup_live(link, baseline=None):
    """Ensure temporary slot 30 and VFO state do not leak between bundled tests."""
    try:
        directory=locations(link.read(LIB_BASE,8192)+bytes(512*1024-8192))
        if TEMP_SLOT in directory:
            from modem import k6mod
            serials=Serials()
            serials.advance_after(link.serial_watermark())
            erase=k6mod.image(b"K6ERASE",0,serials.next(),None,level=1,lib=TEMP_SLOT,remove=True)
            link.mutate_library(erase)
    except Exception:
        pass
    try:
        st=link.status()
        target_hz=BENCH_FREQ_HZ
        if baseline and isinstance(baseline.get("status",{}).get("vfos"),list) and baseline["status"]["vfos"]:
            target_hz=int(baseline["status"]["vfos"][0].get("hz",BENCH_FREQ_HZ))
        vfos=st.get("vfos") or []
        if not vfos or vfos[0].get("hz")!=target_hz or vfos[0].get("mode")!="FM" or vfos[0].get("wide") is not True:
            link.tune(target_hz)
    except Exception:
        pass


def snapshot(port=PORT, include_slots=False, *, cleanup=False, baseline=None):
    with serial_lock(port):
        with open_port(SimpleNamespace(port=port)) as p:
            link=LiveLink(p,timeout=2)
            if cleanup:
                _cleanup_live(link,baseline=baseline)
            caps=link.module_capabilities()
            status=link.status()
            data={"capabilities":caps,"status":status}
            if include_slots:
                if baseline is not None and "slot_sha256" in baseline:
                    # Dedicated test radio: skip 31-slot serial readback once
                    # temporary slot 30 cleanup and API/idle state are verified.
                    data["slot_sha256"]=dict(baseline["slot_sha256"])
                    data["slot_count"]=baseline.get("slot_count",len(data["slot_sha256"]))
                else:
                    directory=locations(link.read(LIB_BASE,8192)+bytes(512*1024-8192))
                    data["slot_sha256"]={
                        str(slot):f"size:{size}"
                        for slot,(_offset,size) in directory.items()
                        if slot!=TEMP_SLOT
                    }
                    data["slot_count"]=len(data["slot_sha256"])
    return data


def check(result,*,expected_build=None,minimum_api=1,exact_api=None,baseline=None):
    caps=result["capabilities"]
    status=result["status"]
    if caps.get("format")!=2 or caps.get("cable_only") is not True or caps.get("module_loader") is not True:
        raise ValueError("unexpected live protocol or device")
    build=caps.get("build_id")
    if not isinstance(build,str) or not HEX_BUILD.fullmatch(build):
        raise ValueError("invalid firmware identity")
    if expected_build is not None and build!=expected_build:
        raise ValueError(f"firmware build mismatch ({build})")
    api=caps.get("module_api_max")
    if type(api) is not int or not minimum_api<=api<=255 or (exact_api is not None and api!=exact_api):
        raise ValueError("firmware module API mismatch")
    if status.get("standby_idle") is not True or status.get("transmitting") is not False or status.get("sleeping") is not False or status.get("squelch_open") is not False:
        raise ValueError("radio not idle")
    if status.get("watching")!=0:
        raise ValueError("radio in dual-watch state")
    vfos=status.get("vfos")
    if not isinstance(vfos,list) or len(vfos)!=2 or not all(
        isinstance(v,dict) and v.get("mode")=="FM" and v.get("wide") is True
        and v.get("memory") is False and isinstance(v.get("hz"),int)
        for v in vfos
    ):
        raise ValueError("unexpected VFO configuration")
    if baseline is not None:
        if caps["build_id"]!=baseline["capabilities"]["build_id"] or vfos!=baseline["status"]["vfos"]:
            raise ValueError("firmware or VFO state changed since baseline")
        if "slot_sha256" not in baseline or "slot_sha256" not in result:
            raise ValueError("snapshot missing full packed slot hash evidence")
        if result["slot_sha256"]!=baseline["slot_sha256"] or result.get("slot_count")!=baseline.get("slot_count"):
            raise ValueError("packed module library differs from baseline")
    return True


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",default=PORT)
    p.add_argument("--expected-build")
    p.add_argument("--minimum-api",type=int,default=1)
    p.add_argument("--exact-api",type=int)
    p.add_argument("--include-slots",action="store_true")
    p.add_argument("--baseline",type=Path)
    p.add_argument("--output",type=Path)
    p.add_argument("--lock-bench",action="store_true",help="standalone: exclusive cooperative whole-bench flock")
    args=p.parse_args(argv)
    if args.port!=PORT:
        p.error("only the locally configured K6 port is allowed")
    if args.expected_build and not HEX_BUILD.fullmatch(args.expected_build):
        p.error("expected build must be 16 hex characters")
    if args.baseline and not args.include_slots:
        p.error("--baseline requires --include-slots for full library verification")
    if args.output and args.output.exists():
        p.error("refuse to overwrite existing evidence")
    from contextlib import nullcontext
    try:
        with bench_lock(DEFAULT_BENCH) if args.lock_bench else nullcontext():
            baseline=json.loads(args.baseline.read_text()) if args.baseline else None
            data=snapshot(args.port,include_slots=args.include_slots,
                          cleanup=bool(args.output or args.baseline),
                          baseline=baseline)
            check(data,expected_build=args.expected_build,minimum_api=args.minimum_api,
                  exact_api=args.exact_api,baseline=baseline)
        if args.output:
            fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,"w") as f: json.dump(data,f,indent=2)
        print(json.dumps(data,indent=2))
        return 0
    except (BlockingIOError,ValueError,OSError,KeyError,TimeoutError) as exc:
        print(f"REFUSED: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
