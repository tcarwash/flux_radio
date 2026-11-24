#!/usr/bin/env python3
"""One shot temporary fixture on packed API25 without live library mutation."""
import argparse, json, os, time, hashlib
from pathlib import Path
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from k6_sotto_rf_bench import freeze,score,sha
from k6_fsk_boundary_bench import fingerprint
from k6_install import Cable,open_port,CABLE_HANDSHAKE,LIB_BASE,Serials
from k6_live import LiveLink
from k6_port_lock import serial_lock
from k6_user_store_migrate import locations
from k6_fsk_rf_record import RtlCapture,summarize_raw_iq,save_capture
from modem import k6mod

PORT="/dev/ttyUSB0"
BUILD="aba032cfc1d2b5e1"
SLOT=30
def op():return open_port(SimpleNamespace(port=PORT))
def directory(link):
    return locations(link.read(LIB_BASE,8192)+bytes(512*1024-8192))
def preflight(l):
    c=l.module_capabilities()
    s=l.status()
    assert c["build_id"]==BUILD and c["module_api_max"]>=5 and c["cable_only"] and c["fsk_rx"]
    assert s["standby_idle"] and not s["transmitting"] and not s["sleeping"] and not s["squelch_open"]
    assert s["watching"]==0 and [v["hz"] for v in s["vfos"]]==[433500000,144390000]
    assert all(v["mode"]=="FM" and v["wide"] and not v["memory"] for v in s["vfos"])
    return s
def snapshot(l):
    d=directory(l)
    # Compare the full contents of every allocated slot, independent of
    # physical extent placement. No existing slot is edited by this script.
    return {str(i):sha(l.read(LIB_BASE+a,n)) for i,(a,n) in d.items()}
def wait_live():
    # Legacy loader END resets the MCU; wait for normal VFO without sending
    # any state-changing requests. No retry of the RF command.
    time.sleep(3)
    with op() as p:return preflight(LiveLink(p,timeout=3))
def main():
    p=argparse.ArgumentParser()
    p.add_argument("--prefix",type=Path,required=True)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--dummy-load-confirmed",action="store_true")
    p.add_argument("--lease-confirmed",action="store_true")
    a=p.parse_args()
    if not (a.dummy_load_confirmed and a.lease_confirmed):p.error("explicit approvals required")
    frozen, (code,entry)=freeze()
    if frozen!=json.loads(a.manifest.read_text()):p.error("original frozen fixture/scorer mismatch")
    os.umask(0o077)
    if a.prefix.parent.stat().st_mode&0o077:p.error("private output dir required")
    for suffix in (".json",".npy","-rx.json","-score.json","-pre.json"):
        if a.prefix.with_name(a.prefix.name+suffix).exists():p.error("refuse overwrite")
    report=dict(build_id=BUILD,tx_module_requests=0,original_module_restored=False,
                original_radio_frequency_restored=False,transmission_confirmed_by_firmware=False,
                code_sha256=sha(code),fixture_manifest_sha256=sha(a.manifest.read_bytes()),
                script_sha256=sha(Path(__file__).read_bytes()))
    serials=Serials()
    raw=None
    attempted=False
    error=None
    with serial_lock(PORT):
        with op() as port:
            l=LiveLink(port,timeout=3)
            initial=preflight(l)
            before=directory(l)
            if SLOT in before:raise RuntimeError("temporary slot not empty")
            hashes=snapshot(l)
        with a.prefix.with_name(a.prefix.name+"-pre.json").open("x") as f:
            json.dump(dict(status=initial,slot_hashes=hashes,build=BUILD),f,indent=2)
        print("All",len(hashes),"existing slots hashed; slot 30 empty",flush=True)
        # SDR preflight before installation or TX.
        with RtlCapture(center_hz=433750000,fs=1024000,gain_tenth_db=0) as sdr:
            baseline=summarize_raw_iq(sdr.read(.45))
            report["sdr_preflight"]=baseline
            if baseline["potential_occupied_or_overloaded"]:raise RuntimeError("SDR baseline occupied/overloaded")
            try:
                with op() as port:
                    c=Cable(port,timeout=3)
                    if not c.handshake(CABLE_HANDSHAKE,wait=4):raise RuntimeError("legacy handshake failed")
                    try:
                        caps=c.capabilities()
                        assert caps["build_id"]==BUILD and caps["cable_only"] and caps["module_api_max"]>=5
                        assert SLOT not in c.library_directory()
                        try:serials.advance_after(c.serial_watermark())
                        except Exception:pass # reboot-local legacy replay floor
                        n=serials.next()
                        img=k6mod.image(code,entry,n,None,level=5,lib=SLOT)
                        attempted=True
                        reply=c.send_image(img,n&255)
                        if reply[0]!=3 or reply[3]!=len(img):raise RuntimeError(f"install reply {reply}")
                        if c.library_read(SLOT,len(img))!=img:raise RuntimeError("readback mismatch")
                        print("Temporary slot installed and verified",flush=True)
                    finally:c.end()
                wait_live()
                with op() as port:
                    l=LiveLink(port,timeout=3)
                    preflight(l)
                    d=directory(l)
                    off,n=d[SLOT]
                    assert l.read(LIB_BASE+off,n)==img
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        capture=pool.submit(sdr.read,5.)
                        time.sleep(.65)
                        report["tx_module_requests"]=1
                        try:
                            result=l.run_module(SLOT,timeout=12,allow_higher_api=True,on_report=lambda s:print("RF MODULE:",s,flush=True))
                            report["module_completion"]=result
                            report["transmission_confirmed_by_firmware"]=bool(result.get("returned") and result.get("result")==6)
                            print("Result",result,flush=True)
                        finally:raw=capture.result(timeout=20)
                    assert not l.status()["transmitting"]
            except Exception as exc:
                error=type(exc).__name__+": "+str(exc)
                report["operation_error"]=error
                print("Operation stopped:",error,flush=True)
            finally:
                if attempted:
                    try:
                        with op() as port:
                            l=LiveLink(port,timeout=3)
                            preflight(l)
                            d=directory(l)
                            if SLOT in d:
                                off,n=d[SLOT]
                                if l.read(LIB_BASE+off,n)!=img:raise RuntimeError("slot changed; refuse remove")
                        if SLOT in d:
                            with op() as port:
                                c=Cable(port,timeout=3)
                                if not c.handshake(CABLE_HANDSHAKE,wait=4):raise RuntimeError("legacy cleanup handshake failed")
                                try:
                                    assert c.capabilities()["build_id"]==BUILD
                                    assert c.library_read(SLOT,len(img))==img
                                    c.remove_slot(SLOT,None,serials.next())
                                finally:c.end()
                        wait_live()
                        with op() as port:
                            l=LiveLink(port,timeout=3)
                            final=preflight(l)
                            report["original_module_restored"]=snapshot(l)==hashes
                            report["original_radio_frequency_restored"]=final["vfos"]==initial["vfos"]
                        print("All original slot hashes unchanged:",report["original_module_restored"],flush=True)
                    except Exception as exc:
                        report["restoration_error"]=str(exc)
                        print("CLEANUP FAILED:",exc,flush=True)
    if raw is not None:
        save_capture(raw,a.prefix,summary={**summarize_raw_iq(raw),"hardware_lab_transaction":report})
        verdict,rx=score(a.prefix.with_suffix(".npy"),report,frozen)
        for name,data in (("-rx.json",rx),("-score.json",verdict)):
            with a.prefix.with_name(a.prefix.name+name).open("x") as f:json.dump(data,f,indent=2)
        print(json.dumps(verdict,indent=2))
        if not verdict["passed"]:raise SystemExit(1)
    else:
        with a.prefix.with_suffix(".json").open("x") as f:json.dump(report,f,indent=2)
        raise SystemExit("No RF capture; no retry")
if __name__=="__main__":main()
