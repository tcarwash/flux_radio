#!/usr/bin/env python3
"""One reversible Flux K6 two-frame FSK boundary RF lab transaction.

Explicitly armed, one-shot conducted/dummy-load use only.
- Attests firmware and original lab slot bytes before replacement.
- Backs up exact installed module envelope before any SPI mutation.
- Temporarily installs the guarded, compiled two-frame test into slot 30.
- Captures air-coupled SDR IQ during ONE bounded transmitter module run.
- Restores the exact original code with fresh replay serial and verifies it.
- Restores original VFO, writes JSON evidence and IQ without overwrites.
No MCU firmware flashing, calibration writes, or automatic TX retry.
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
EXPECT_CODE_SHA256="223d6976090e4d29af70b9ae2c9a76a60dd57aa37720f4fe1950b44061c90a29"
# The *saved envelope* hash includes the serial and MAC. The known 300-baud
# PDM signed source envelope is held locally for an additional before/after
# source-artifact match; here we gate on its full envelope SHA-256.
TX_TIMEOUT_MS=1900


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
                    original_envelope: Path, *, armed=False) -> dict:
    if not armed:
        raise ValueError("TX and SPI mutation require --dummy-load-confirmed")
    if any(prefix.with_suffix(x).exists() for x in (".npy",".json")):
        raise FileExistsError("output IQ or metadata exists; no overwrite")
    if original_envelope.exists():
        raise FileExistsError("original slot backup already exists; no overwrite")
    prefix.parent.mkdir(parents=True,exist_ok=True)
    test_code,entry=k6mod.build(str(source))
    if not 0<len(test_code)<4096 or entry!=0:
        raise ValueError("invalid compiled boundary module")
    report={
        "experiment":"Flux two-frame native FSK boundary rearm",
        "dummy_load_operator_confirmed":True,
        "rf_air_coupled_uncalibrated":True,
        "target_hz":LAB_HZ,"module_slot":SLOT,
        "tx_module_requests":0,"original_module_restored":False,
        "original_radio_frequency_restored":False,
        "expected_firmware_build_id":EXPECTED_BUILD,
        "new_test_code_sha256":hashlib.sha256(test_code).hexdigest(),
        "active_tx_watchdog_timeout_ms":TX_TIMEOUT_MS,
        "no_automatic_retry":True,
        "not_true_fifo_streaming":True,
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
        known_file=Path("/home/tyler/flux-bench/tx-injection-20261122/fsklab-pdm300-slot30-envelope.k6m")
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
            raise RuntimeError("slot 30 no longer matches trusted PDM test code/header")
        old_code=old_image[16:-8]
        watermark=link.serial_watermark()
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
                        print("RF boundary result",outcome,flush=True)
                    except Exception as e:
                        report["module_error"]=type(e).__name__+": "+str(e)
                        print("TX module failed (no retry):",e,flush=True)
                    raw=capture.result(timeout=20)
        except Exception as exc:
            primary_error=type(exc).__name__+": "+str(exc)
            report["operation_error"]=primary_error
            print("Boundary transaction issue:",primary_error,flush=True)
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
        report.get("module_completion",{}).get("result")==128)
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
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port",default="/dev/ttyUSB0")
    p.add_argument("--prefix",required=True,type=Path)
    p.add_argument("--enabled-source",required=True,type=Path)
    p.add_argument("--slot-backup",required=True,type=Path)
    p.add_argument("--dummy-load-confirmed",action="store_true")
    args=p.parse_args()
    r=run_transaction(args.port,args.prefix,args.enabled_source,
                      args.slot_backup,armed=args.dummy_load_confirmed)
    print("Restored:",r["original_module_restored"],r["original_radio_frequency_restored"],
          "TX confirmed:",r["transmission_confirmed_by_firmware"])


if __name__=="__main__":
    main()
