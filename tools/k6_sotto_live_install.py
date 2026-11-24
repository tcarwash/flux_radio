#!/usr/bin/env python3
"""Plan or explicitly provision Sotto API27/29 modules and their K6SC resource.

Codebook installation is intentionally separate: only an exact pinned K6CB
is accepted, and this tool cannot write the codebook, flash firmware, or TX.
The default is a read-only plan. --install commits two verified live
transactions; an interrupted transaction is never blindly retried.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import zlib

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from k6_install import InstallError,open_port
from k6_live import LiveLink
from k6_port_lock import serial_lock

DECK_SLOTS=(14,15,13,*range(16,32),*range(9))
MODULE_SLOTS=(*range(16,32),*range(9))
CB_ADDR=0x100000
CB_BYTES=30752

def load_bundle(path):
    manifest=json.loads((path/"manifest.json").read_text())
    code=(path/"module.bin").read_bytes()
    deck=(path/"sotto-ivf.k6sc").read_bytes()
    for filename,blob in (("module.bin",code),("sotto-ivf.k6sc",deck)):
        if hashlib.sha256(blob).hexdigest()!=manifest["sha256"][filename]:
            raise InstallError(f"{filename}: SHA256 mismatch")
    if (manifest["library"][0]["level"],k6mod.module_name(code)) not in ((27,"SOTTO TEST"),(27,"SOTTO LIVE"),(29,"SOTTO TX")):
        raise InstallError("Expected Sotto qualification or API29 bench TX bundle")
    if len(deck)!=4182 or deck[:5]!=b"K6SC\x01" or zlib.crc32(deck)!=0x0de43427:
        raise InstallError("Unexpected search deck")
    return code,deck

def check_codebook(blob):
    return (len(blob)==CB_BYTES and blob[:4]==b"K6CB"
        and struct.unpack_from("<HHI",blob,4)==(1,20,30720)
        and blob[12:17]==bytes([4,9,9,8,8])
        and struct.unpack_from("<II",blob,24)==(0xd5856ecd,zlib.crc32(blob[:28]))
        and zlib.crc32(blob[32:])==0xd5856ecd)

def provision(link,code,deck,*,install=False,module_slot=None,deck_slot=None,accept_experimental_tx=False):
    level=29 if k6mod.module_name(code)=="SOTTO TX" else 27
    # Never permit RF-capable bench provisioning through the ordinary
    # non-radiating module installation path by accident.
    if install and level==29 and not accept_experimental_tx:
        raise InstallError("Experimental TX module requires --accept-experimental-tx")
    caps=link.module_capabilities()
    features=link.capabilities()["features"]
    if (caps["module_api_max"]<level or not caps["cable_only"]
        or "resource_deck" not in features or "lib_10k" not in features
        or link.info()["protocol"]!=5):
        raise InstallError("Requires compatible cable-only balanced packed firmware")
    if not link.status()["standby_idle"]:
        raise InstallError("Return radio to idle standby")
    if len(code)+24>link.library_limit(features):
        raise InstallError("Module exceeds live staging capacity")
    if not check_codebook(link.read(CB_ADDR,CB_BYTES)):
        raise InstallError("Missing or incompatible K6CB: provision the pinned codebook separately")
    directory=link.library_directory()
    def choose(payload,slots,requested,flags,level,excluded=()):
        if requested is not None and (requested not in slots or requested in excluded):
            raise InstallError("Requested slot is not eligible")
        candidates=(requested,) if requested is not None else slots
        # Reuse only an exact matching typed payload. Never replace an unrelated slot.
        for slot in candidates:
            if slot in excluded or slot not in directory or directory[slot][1]!=len(payload)+24:
                continue
            raw=link.library_read(slot,len(payload)+24)
            serial=int.from_bytes(raw[8:12],"little")
            if serial and raw==k6mod.image(payload,k6mod.bin_entry(payload) if flags==1 else 0,
                    serial,None,level=level,lib=slot,cards=flags==5):
                return slot,True
        for slot in candidates:
            if slot not in directory and slot not in excluded:
                return slot,False
        raise InstallError("No compatible free slot; no existing object will be overwritten")
    ds,dr=choose(deck,DECK_SLOTS,deck_slot,5,5)
    ms,mr=choose(code,MODULE_SLOTS,module_slot,1,level,(ds,))
    plan={"module_slot":ms,"deck_slot":ds,"module_bytes":len(code)+24,
          "deck_bytes":len(deck)+24,"module_reused":mr,"deck_reused":dr,
          "codebook_verified":True,"installed":False}
    if not install:return plan
    for slot,payload,level,flags,reused in ((ds,deck,5,5,dr),(ms,code,level,1,mr)):
        if reused:continue
        serial=link.serial_watermark()+1
        if not 0<serial<=0xffffffff:raise InstallError("Serial watermark exhausted")
        env=k6mod.image(payload,k6mod.bin_entry(payload) if flags==1 else 0,
                        serial,None,level=level,lib=slot,cards=flags==5)
        if not link.mutate_library(env)["verified"]:
            raise InstallError("Readback failed; inspect directory before retry")
    plan["installed"]=True
    return plan

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle",type=Path)
    p.add_argument("--port",required=True)
    p.add_argument("--install",action="store_true")
    p.add_argument("--accept-experimental-tx",action="store_true",
                   help="explicitly authorize installing TX-capable bench module (no RF by installer)")
    p.add_argument("--module-slot",type=int)
    p.add_argument("--deck-slot",type=int)
    a=p.parse_args()
    code,deck=load_bundle(a.bundle)
    with serial_lock(a.port),open_port(SimpleNamespace(port=a.port)) as port:
        link=LiveLink(port)
        print(json.dumps(provision(link,code,deck,install=a.install,
                   module_slot=a.module_slot,deck_slot=a.deck_slot,
                   accept_experimental_tx=a.accept_experimental_tx),indent=2))
    print("Installation only; no RF or firmware flash. SOTTO TX is bench-only, requires dummy load and physical PTT.")

if __name__=="__main__":main()
