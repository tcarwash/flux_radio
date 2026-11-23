#!/usr/bin/env python3
"""One-shot TX-15W REG70 gain ladder deviation RF trial with verified restore.

Only run on operator-confirmed dummy load / SDR receiver with live radio RX,
expected firmware and known module bytes. Back up slot 30 before mutation,
key one 1800-ms-leased RF module, then always restore slot and VFO.
No MCU firmware flash, calibration edits, retries or unguarded RF module.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
from types import SimpleNamespace

from k6_live import LiveLink,open_port
from k6_port_lock import serial_lock
from k6_install import LIB_BASE
from k6_user_store_migrate import locations
from k6_fsk_rf_record import RtlCapture,summarize_raw_iq,save_capture
from modem import k6mod

EXPECTED_BUILD="95d6b260d4122287"
LAB_HZ=433_500_000
INITIAL_HZ=144_390_000
SLOT=30
EXPECTED_CODE_SHA256={0:"2b49c6e8d6f05013892d1e6c48c28b7a83790a88d57ea68d33ba00f69b2d2422",1:"1c8e29ae886a1abfefa3800bffa7f5b1d549448ef50ab1799cb42dbc02effe83"}
# A previously saved, trusted slot-30 envelope is retained in the bench
# archive. Replay protection changes serial/MAC on every restoration, so
# compare its signed executable bytes to the current live slot exactly.
TX_TIMEOUT_MS=1800


def fingerprint(img):
    if len(img)<25 or img[:4]!=b"K6M\x01":
        raise ValueError("invalid ordinary module envelope")
    size,entry,serial,level,flags,tlm,slot=struct.unpack_from("<HHIBBBB",img,4)
    if size+24!=len(img) or not serial or flags!=1 or slot!=SLOT or tlm or entry>=size:
        raise ValueError("unexpected module header")
    body=img[:-8]
    return dict(size=size,entry=entry,serial=serial,level=level,
                slot=slot,flags=flags,
                code_sha256=hashlib.sha256(img[16:-8]).hexdigest(),
                envelope_sha256=hashlib.sha256(img).hexdigest(),
                correct_crc=k6mod.image_tag(None,body)==img[-8:])


def run_transaction(port_path: str, prefix: Path, source: Path,
                    original_envelope: Path, *, variant:int, armed=False,
                    expected_result: int=13,
                    experiment: str="Flux TX-15W B-chip REG70 gain-deviation and tone phase",
                    watchdog_ms: int=TX_TIMEOUT_MS) -> dict:
    if variant not in EXPECTED_CODE_SHA256:
        raise ValueError("only TX-15W PA-off (0) / RF-on (1) are allowed")
    if not armed:
        raise ValueError("TX and SPI mutation require --dummy-load-confirmed")
    if any(prefix.with_suffix(x).exists() for x in (".npy",".json")):
        raise FileExistsError("output IQ or metadata exists; no overwrite")
    if original_envelope.exists():
        raise FileExistsError("original slot backup already exists; no overwrite")
    prefix.parent.mkdir(parents=True,exist_ok=True)
    source_text=source.read_text()
    required=("#define TX15W_BENCH_ENABLE 1",
              f"#define TX15W_RF_ENABLE {variant}",
              "#define TX15W_FIXTURE_ACK 1",
              "#define TX15W_WATCHDOG_ACK 1",
              "#define TX15W_HZ 433500000u")
    if not all(item in source_text.splitlines() for item in required):
        raise ValueError("RF wrapper missing exact safety acknowledgments")
    test_code,entry=k6mod.build(str(source))
    if (not 0<len(test_code)<4096 or entry!=0 or
        k6mod.source_level(str(source)) != 3 or
        hashlib.sha256(test_code).hexdigest() != EXPECTED_CODE_SHA256[variant]):
        # Do not run any other native module as part of this RF bench.
        raise ValueError("RF source differs from exact emulator-tested TX-15W code")
    report={
        "experiment":experiment,
        "predeclared_variant":variant,
        "reg70_gain_ladder_at_constant_reg71_tone":True,
        "dummy_load_operator_confirmed":True,
        "rf_air_coupled_uncalibrated":True,
        "target_hz":LAB_HZ,"module_slot":SLOT,
        "tx_module_requests":0,"original_module_restored":False,
        "original_radio_frequency_restored":False,
        "expected_firmware_build_id":EXPECTED_BUILD,
        "new_test_code_sha256":hashlib.sha256(test_code).hexdigest(),
        "active_tx_watchdog_timeout_ms":watchdog_ms,
        "expected_module_result":expected_result,
        "no_automatic_retry":True,
        "audio_tone_phase_not_arbitrary_iq":True,
    }
    raw=None
    originally_written=False
    primary_error=None
    with serial_lock(port_path),open_port(SimpleNamespace(port=port_path)) as port:
        link=LiveLink(port)
        caps=link.module_capabilities()
        initial=link.status()
        report["original_radio_status"]=initial
        report["firmware_capabilities"]=caps
        if (caps["build_id"]!=EXPECTED_BUILD or
            not caps["cable_only"] or not caps["fsk_rx"] or
            caps["module_api_max"]<3 or
            initial["transmitting"] or initial["sleeping"] or
            not initial["standby_idle"] or
            any(v["hz"]!=INITIAL_HZ for v in initial["vfos"]) or
            not any(v["hz"]==INITIAL_HZ for v in initial["vfos"])):
            raise RuntimeError("unsafe or unexpected initial radio state/firmware")
        directory=locations(link.read(LIB_BASE,8192)+bytes(512*1024-8192))
        offset,length=directory[SLOT]
        old_image=link.library_read(SLOT,length)
        old_meta=fingerprint(old_image)
        report["original_slot_fingerprint"]=old_meta
        known_file=Path("/home/tyler/flux-bench/tx-injection-20261122/tone-oscillator/slot30-before-tx8.k6m")
        reference=known_file.read_bytes()
        ref_meta=fingerprint(reference)
        # A successful previous restoration must use a NEW serial and
        # therefore produce a new envelope MAC. Verify the exact trusted
        # executable + ABI/entry/flags and both envelope checksums, not
        # full-envelope byte equality across replay serial changes.
        if (not old_meta["correct_crc"] or not ref_meta["correct_crc"] or
            old_image[16:-8]!=reference[16:-8] or
            old_meta["level"]!=3 or old_meta["level"]!=ref_meta["level"] or
            old_meta["entry"]!=ref_meta["entry"] or
            old_meta["size"]!=ref_meta["size"] or
            old_meta["serial"]<ref_meta["serial"]):
            raise RuntimeError("slot 30 no longer matches trusted prior module code/header")
        old_code=old_image[16:-8]
        # Preserve monotonic replay serials across MCU firmware flashes.
        # The boot-local verifier watermark may reset to zero, but the
        # installed slot-30 envelope has its own much newer serial.
        watermark=max(link.serial_watermark(),old_meta["serial"])
        if watermark>=0xfffffffd:
            raise RuntimeError("module serial watermark near exhaustion")
        new_img=k6mod.image(test_code,entry,watermark+1,None,level=3,lib=SLOT)
        restored_img=k6mod.image(old_code,old_meta["entry"],watermark+2,
                                 None,level=old_meta["level"],lib=SLOT)
        if not fingerprint(new_img)["correct_crc"] or not fingerprint(restored_img)["correct_crc"]:
            raise AssertionError("incorrect module envelopes")
        report["new_module_serial"]=watermark+1
        report["restored_module_serial"]=watermark+2
        with original_envelope.open("xb") as out:
            out.write(old_image)
            out.flush()
            os.fsync(out.fileno())
        print("Original module backed up, verified",old_meta["code_sha256"],flush=True)
        try:
            ver=link.mutate_library(new_img)
            originally_written=True
            report["temporary_module_verification"]=ver
            if not ver.get("verified"):
                raise RuntimeError("temporary module failed installed verification")
            result_freq=link.tune(LAB_HZ)
            report["radio_tuned_status"]=result_freq
            watching=result_freq["watching"]
            if (result_freq["transmitting"] or
                result_freq["vfos"][watching]["hz"]!=LAB_HZ or
                result_freq["rssi_dbm"]>-85):
                raise RuntimeError("lab channel not idle or VFO refused tuning")
            with RtlCapture(center_hz=433_750_000,fs=1_024_000,gain_tenth_db=0) as sdr:
                pre=summarize_raw_iq(sdr.read(.45))
                report["sdr_preflight"]=pre
                if pre["potential_occupied_or_overloaded"]:
                    raise RuntimeError("SDR baseline occupied/overloaded")
                with ThreadPoolExecutor(max_workers=1) as pool:
                    capture=pool.submit(sdr.read,5.0)
                    time.sleep(.65)
                    report["tx_module_requests"]=1
                    try:
                        outcome=link.run_module(SLOT,timeout=12.,
                                                allow_higher_api=True,
                            on_report=lambda s:print("RF MODULE:",s,flush=True))
                        report["module_completion"]=outcome
                        print("TX-15W RF result",outcome,flush=True)
                    except Exception as e:
                        report["module_error"]=type(e).__name__+": "+str(e)
                        print("TX module failed (no retry):",e,flush=True)
                    raw=capture.result(timeout=20)
        except Exception as exc:
            primary_error=type(exc).__name__+": "+str(exc)
            report["operation_error"]=primary_error
            print("TX-15W transaction issue:",primary_error,flush=True)
        finally:
            # The module and its leased TX watchdog should be finished by now.
            # Always inspect TX state before any SPI restoration.
            try:
                state=link.status()
                report["status_before_restore"]=state
                if state["transmitting"]:
                    raise RuntimeError("TX still active: wait for independent lease to expire")
                # A lost live-library COMMIT acknowledgment can occur *after*
                # flash was changed. Inspect current exact code rather than
                # assuming a failed mutation left the original slot untouched.
                link.__dict__.pop("_library_directory",None)
                now_directory=link.library_directory()
                now_extent=now_directory.get(SLOT)
                if now_extent is None:
                    raise RuntimeError("original slot missing after library transaction")
                now_image=link.library_read(SLOT,now_extent[1])
                now_code=now_image[16:-8]
                if now_code==old_code:
                    report["original_module_restored"]=True
                    print("Original slot 30 already matches backup",flush=True)
                elif now_code==test_code:
                    # Only replace a positively identified temporary image.
                    # A different third-party write would be left untouched.
                    restored=link.mutate_library(restored_img)
                    report["restoration_verification"]=restored
                    if not restored.get("verified"):
                        raise RuntimeError("restored module readback not verified")
                    written=link.library_read(SLOT,len(restored_img))
                    if written[16:-8]!=old_code:
                        raise RuntimeError("restored module code differs from backup")
                    report["original_module_restored"]=True
                    print("Original slot 30 code restored and readback verified",flush=True)
                else:
                    raise RuntimeError("slot 30 changed to unknown image; refusing blind overwrite")
                final=link.tune(INITIAL_HZ)
                report["final_radio_status"]=final
                report["original_radio_frequency_restored"]=not final["transmitting"] and all(
                    v["hz"]==INITIAL_HZ for v in final["vfos"])
                print("Radio restored:",report["original_radio_frequency_restored"],flush=True)
            except Exception as recovery_exc:
                report["restoration_error"]=type(recovery_exc).__name__+": "+str(recovery_exc)
                print("CRITICAL restoration issue:",report["restoration_error"],flush=True)

    report["transmission_confirmed_by_firmware"]=bool(
        report.get("module_completion",{}).get("returned") and
        report.get("module_completion",{}).get("result")==expected_result)
    if raw is not None:
        rf=summarize_raw_iq(raw)
        rf["hardware_lab_transaction"]=report
        save_capture(raw,prefix,summary=rf)
    else:
        report_path=prefix.with_suffix(".json")
        report_path.write_text(json.dumps(report,indent=2)+"\n")
    if not report["original_module_restored"] or not report["original_radio_frequency_restored"]:
        raise RuntimeError("hardware restoration incomplete; inspect saved report before new TX")
    if primary_error is not None:
        raise RuntimeError(primary_error)
    if not report["transmission_confirmed_by_firmware"]:
        raise RuntimeError("firmware did not confirm all thirteen REG70 gain plateaus")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",default="/dev/ttyUSB0")
    p.add_argument("--prefix",required=True,type=Path)
    p.add_argument("--enabled-source",required=True,type=Path)
    p.add_argument("--slot-backup",required=True,type=Path)
    p.add_argument("--dummy-load-confirmed",action="store_true")
    p.add_argument("--variant",type=int,choices=(0,1),required=True)
    p.add_argument("--expected-result",type=int,choices=(13,),default=13)
    p.add_argument("--experiment",default="Flux TX-15W B-chip REG70 gain-deviation and tone phase")
    p.add_argument("--watchdog-ms",type=int,choices=(1800,),default=1800)
    args=p.parse_args()
    r=run_transaction(args.port,args.prefix,args.enabled_source,
                      args.slot_backup,variant=args.variant,armed=args.dummy_load_confirmed,
                      expected_result=args.expected_result,
                      experiment=args.experiment,watchdog_ms=args.watchdog_ms)
    print("Restored:",r["original_module_restored"],r["original_radio_frequency_restored"],
          "TX confirmed:",r["transmission_confirmed_by_firmware"])


if __name__=="__main__":
    main()
