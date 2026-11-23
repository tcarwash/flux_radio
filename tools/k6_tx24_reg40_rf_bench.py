#!/usr/bin/env python3
"""TX-24: ONE hash-pinned REG40 deviation scan on an exclusively leased K6.

Never run while another owner has the bench, even if this CLI could
acquire the serial OS lock. Requires a separately acknowledged exclusive
queue lease AND a confirmed conducted dummy-load fixture. No auto retry.
No native-FSK FIFO writes, calibration flash or MCU firmware updates.

Exact current expected operator VFO configuration: A watched 440.850 MHz,
B 144.390 MHz, both wide FM; refuse instead of overwriting changed state.
Signed original slot30, VFOs and unchanged original code independently
verified after the single bounded module transaction.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from modem import k6mod
from k6_live import LiveLink,open_port
from k6_port_lock import serial_lock
from k6_install import LIB_BASE
from k6_user_store_migrate import locations
from k6_fsk_rf_record import RtlCapture,summarize_raw_iq,save_capture
from k6_tx15t_rf_bench import fingerprint

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"c/k6/mod/tx24_reg40_tone_armed.c"
PROBE=ROOT/"c/k6/mod/tx24_reg40_tone_probe.c"
SOURCE_SHA="de786e08e3bc8d6c1c57a2f53be7d55c77deb1f7ca3babbe79e9c103bb40360e"
PROBE_SHA="be3496e0d2dbbe63cc86fabe34e8275d1eff667d652262972305f7181c16d9d4"
CODE_SHA="e792a8bd77d2f055a16f9e1da025a4f7a4555b6dbb9d3417817612e5e21024f0"
SOURCE_BYTES=808
EXPECTED_BUILD="95d6b260d4122287"
LAB_HZ=433500000
VFO_A=440850000
VFO_B=144390000
SLOT=30
LEASE_MS=1800
EXPECTED_RESULT=13
REFERENCE_SLOT=Path(
    "/home/tyler/flux-bench/tx-injection-20261122/"
    "fsklab-pdm300-slot30-envelope.k6m")
REFERENCE_CODE_SHA="6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522"
CAPTURE_CENTER_HZ=433750000
CAPTURE_SAMPLE_RATE=1024000
SDR_GAIN_TENTH_DB=0
ARCHIVE_BASE=Path("/home/tyler/flux-bench/tx-injection-20261122/tx24-reg40")

def baseline_vfos(state):
    """Require intact two-VFO operator configuration, not just A frequency."""
    vfos=state.get("vfos",[])
    return bool(state.get("watching")==0 and len(vfos)==2 and
        [x.get("hz") for x in vfos]==[VFO_A,VFO_B] and
        all(x.get("mode")=="FM" and x.get("wide") is True and
            x.get("memory") is False for x in vfos) and
        not state.get("transmitting") and not state.get("sleeping") and
        state.get("standby_idle"))

def frozen_build():
    """Offline, immutable source/compiled-body check; no hardware access."""
    for path,sha in ((SOURCE,SOURCE_SHA),(PROBE,PROBE_SHA)):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=sha:
            raise RuntimeError(f"TX24 source hash changed: {path}; refuse")
    source=SOURCE.read_text()
    for macro in (
        "#define K6_MODULE_LEVEL 3",
        "#define TX24_BENCH_ENABLE 1",
        "#define TX24_RF_ENABLE 1",
        "#define TX24_FIXTURE_ACK 1",
        "#define TX24_WATCHDOG_ACK 1",
        "#define TX24_FREQ_HZ 433500000u",
        '#include "tx24_reg40_tone_probe.c"',
    ):
        if macro not in source.splitlines():
            raise RuntimeError("missing exact armed guard: "+macro)
    code,entry=k6mod.build(str(SOURCE))
    if (entry!=0 or len(code)!=SOURCE_BYTES or
        k6mod.source_level(str(SOURCE))!=3 or
        hashlib.sha256(code).hexdigest()!=CODE_SHA):
        raise RuntimeError("TX24 compiler output differs from frozen test")
    return code,entry

def output_paths(prefix):
    return {
        "raw":prefix.with_suffix(".npy"),
        "receipt":prefix.with_suffix(".json"),
        "before":prefix.with_name(prefix.name+"-slot30-before.k6m"),
    }

def run_transaction(port_path,prefix,*,dummy_load_confirmed=False,
                    operator_lease_confirmed=False):
    if port_path!="/dev/ttyUSB0":
        raise ValueError("TX24 is qualified only on canonical Flux ttyUSB0")
    if not dummy_load_confirmed or not operator_lease_confirmed:
        raise ValueError("physical RF requires independently ACKed hardware lease AND dummy load")
    paths=output_paths(prefix)
    if any(p.exists() for p in paths.values()):
        raise FileExistsError("TX24 no overwrites: IQ, receipt or slot backup already exists")
    code,entry=frozen_build()
    prefix.parent.mkdir(parents=True,exist_ok=True)
    report=dict(study="TX24 B-chip REG40 FM-deviation low-byte single guarded RF",
                queue_issue="https://github.com/tcarwash/flux_radio/issues/17",
                operator_lease_confirmed=True,dummy_load_confirmed=True,
                module_source_sha256=SOURCE_SHA,
                probe_source_sha256=PROBE_SHA,
                module_code_sha256=CODE_SHA,
                firmware_build_expected=EXPECTED_BUILD,
                module_slot=SLOT,target_hz=LAB_HZ,
                expected_module_result=EXPECTED_RESULT,
                module_tx_watchdog_ms=LEASE_MS,
                max_transmissions=1,tx_module_requests=0,
                no_retry=True,
                provisional_rf_qualification=False,
                original_module_restored=False,
                original_vfos_restored=False,
                sdr_observation_uncalibrated=True,
                register_deviation_not_rf_power=True)
    raw=None
    error=None
    # CANONICAL PROCESS LOCK spans every radio, SDR, SPI and restoration action.
    # Queue lease is an additional explicit safety requirement.
    with serial_lock(port_path),open_port(SimpleNamespace(port=port_path)) as port:
        link=LiveLink(port)
        caps=link.module_capabilities()
        initial=link.status()
        report["firmware_capabilities"]=caps
        report["initial_radio_status"]=initial
        if (caps["build_id"]!=EXPECTED_BUILD or
            not caps["cable_only"] or not caps["fsk_rx"] or
            caps["module_api_max"]<3 or not baseline_vfos(initial)):
            raise RuntimeError("unknown operator VFOs, firmware, or active TX: refuse")
        directory=locations(link.read(LIB_BASE,8192)+bytes(512*1024-8192))
        _,length=directory[SLOT]
        original=link.library_read(SLOT,length)
        metadata=fingerprint(original)
        trusted=REFERENCE_SLOT.read_bytes()
        trusted_meta=fingerprint(trusted)
        report["original_slot_fingerprint"]=metadata
        if (metadata["code_sha256"]!=REFERENCE_CODE_SHA or
            trusted_meta["code_sha256"]!=REFERENCE_CODE_SHA or
            not metadata["correct_crc"] or not trusted_meta["correct_crc"] or
            metadata["size"]!=trusted_meta["size"] or
            metadata["entry"]!=trusted_meta["entry"] or
            metadata["level"]!=trusted_meta["level"] or
            metadata["level"]!=3 or
            original[16:-8]!=trusted[16:-8] or
            metadata["serial"]<trusted_meta["serial"]):
            raise RuntimeError("slot 30 does not match the known signed original")
        watermark=max(link.serial_watermark(),metadata["serial"])
        if watermark>=0xfffffffd:
            raise RuntimeError("signed-module serial range nearly exhausted")
        replacement=k6mod.image(code,entry,watermark+1,None,level=3,lib=SLOT)
        rollback=k6mod.image(original[16:-8],metadata["entry"],
                            watermark+2,None,level=metadata["level"],lib=SLOT)
        if not (fingerprint(replacement)["correct_crc"] and
                fingerprint(rollback)["correct_crc"]):
            raise RuntimeError("new or rollback image has bad authenticated tag")
        report["new_module_serial"]=watermark+1
        report["restore_module_serial"]=watermark+2
        with paths["before"].open("xb") as f:
            f.write(original)
            f.flush()
            os.fsync(f.fileno())
        print("TX24 signed slot30 snapshot saved and verified",flush=True)
        try:
            verification=link.mutate_library(replacement)
            report["temporary_module_verification"]=verification
            if not verification.get("verified"):
                raise RuntimeError("temporary module installation not verified")
            tuned=link.tune(LAB_HZ)
            report["tuned_radio_status"]=tuned
            if (tuned["transmitting"] or tuned.get("watching")!=0 or
                tuned["vfos"][0]["hz"]!=LAB_HZ or
                tuned["vfos"][1]["hz"]!=VFO_B or
                tuned["rssi_dbm"]>-85):
                raise RuntimeError("radio tuning or channel occupancy invalid")
            with RtlCapture(center_hz=CAPTURE_CENTER_HZ,fs=CAPTURE_SAMPLE_RATE,
                            gain_tenth_db=SDR_GAIN_TENTH_DB) as sdr:
                quiet=summarize_raw_iq(sdr.read(.45))
                report["sdr_preflight"]=quiet
                if quiet["potential_occupied_or_overloaded"]:
                    raise RuntimeError("SDR gain overloaded or channel occupied")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    capture=pool.submit(sdr.read,5.0)
                    time.sleep(.65)
                    report["tx_module_requests"]=1
                    try:
                        result=link.run_module(SLOT,timeout=12.0,
                            allow_higher_api=True,
                            on_report=lambda s:print("TX24 RF:",s,flush=True))
                        report["module_completion"]=result
                    except Exception as exc:
                        report["module_error"]=type(exc).__name__+": "+str(exc)
                    raw=capture.result(timeout=20)
        except Exception as exc:
            error=type(exc).__name__+": "+str(exc)
            report["operation_error"]=error
        finally:
            # If TX remains keyed, do NOT mutate flash until lease expires.
            try:
                state=link.status()
                report["status_before_restore"]=state
                if state["transmitting"]:
                    raise RuntimeError("TX not dekeyed: STOP, do not touch library")
                link.__dict__.pop("_library_directory",None)
                live_directory=link.library_directory()
                extent=live_directory.get(SLOT)
                if extent is None:
                    raise RuntimeError("slot30 no longer exists, no blind overwrite")
                current=link.library_read(SLOT,extent[1])
                if current[16:-8]==original[16:-8]:
                    report["original_module_restored"]=True
                elif current[16:-8]==code:
                    verified=link.mutate_library(rollback)
                    report["restoration_verification"]=verified
                    if not verified.get("verified"):
                        raise RuntimeError("rollback library write not verified")
                    now=link.library_read(SLOT,len(rollback))
                    if now[16:-8]!=original[16:-8] or not fingerprint(now)["correct_crc"]:
                        raise RuntimeError("rollback code or authenticated tag mismatch")
                    report["original_module_restored"]=True
                else:
                    raise RuntimeError("slot30 modified by unknown owner; no overwrite")
                restored=link.tune(VFO_A)
                report["final_radio_status"]=restored
                report["original_vfos_restored"]=baseline_vfos(restored)
            except Exception as exc:
                report["restoration_error"]=type(exc).__name__+": "+str(exc)
    report["transmission_confirmed_by_firmware"]=bool(
        report.get("module_completion",{}).get("returned") and
        report.get("module_completion",{}).get("result")==EXPECTED_RESULT)
    if raw is not None:
        stats=summarize_raw_iq(raw)
        stats["hardware_lab_transaction"]=report
        save_capture(raw,prefix,summary=stats)
    else:
        with paths["receipt"].open("x") as f:
            json.dump(report,f,indent=2)
            f.write("\n")
    if not report["original_module_restored"] or not report["original_vfos_restored"]:
        raise RuntimeError("CRITICAL incomplete restoration; inspect original and receipt")
    if error:
        raise RuntimeError(error)
    if not report["transmission_confirmed_by_firmware"]:
        raise RuntimeError("no 13-slot firmware confirmation, no retry")
    return report

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",default="/dev/ttyUSB0")
    p.add_argument("--prefix",type=Path,required=True)
    p.add_argument("--dummy-load-confirmed",action="store_true")
    p.add_argument("--operator-lease-confirmed",action="store_true")
    args=p.parse_args(argv)
    report=run_transaction(args.port,args.prefix,
        dummy_load_confirmed=args.dummy_load_confirmed,
        operator_lease_confirmed=args.operator_lease_confirmed)
    print("TX24 completed",report["transmission_confirmed_by_firmware"],
          "signed slot/VFO restored",
          report["original_module_restored"],report["original_vfos_restored"])

if __name__=="__main__":
    main()
