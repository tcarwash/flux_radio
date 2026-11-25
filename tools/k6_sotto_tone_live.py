#!/usr/bin/env python3
"""Opt-in live-serial controller for the generated Sotto 1 kHz tone module.

No hardware IO on import, --help or --build. Both --install and --run are
explicit; --run requires a dummy-load confirmation and a receiving SDR.
This does NOT synthesize PTT, send PCM over UART, retune, or flash firmware.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import subprocess
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import urllib.request
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
if str(ROOT/"tools") not in sys.path:sys.path.insert(0,str(ROOT/"tools"))
from tools.k6_sotto_module import build
from modem import k6mod

LEVEL=29
HZ=433_500_000
VALID_SLOTS=set(range(9))|set(range(16,32))


def code():
    image,entry=build(host_resources=True,tone_tx=True)
    if k6mod.module_name(image)!="SOTTO TONE" or entry!=16:
        raise ValueError("Unexpected generated Sotto tone module")
    return image,entry


def url_fetch(base,path):
    with urllib.request.urlopen(base.rstrip("/")+path,timeout=5) as response:
        return response.read()


def monitor_preflight(base):
    status=json.loads(url_fetch(base,"/api/status?after=0"))
    if status.get("state")!="receiving" or abs(float(status.get("freq",0))-433.5)>0.001:
        raise RuntimeError("Sotto Monitor must be receiving at nominal 433.500 MHz")
    return status


def radio_preflight(link):
    s=link.status()
    if s["transmitting"] or not s["standby_idle"]:
        raise RuntimeError("Radio is not in idle RX; do not start RF test")
    if s["vfos"][s["master"]]["hz"]!=HZ:
        raise RuntimeError("Active VFO must be 433.500 MHz (no auto-retune)")
    caps=link.capabilities()["features"]
    for required in ("coop_cancel","run","module_caps"):
        if required not in caps:
            raise RuntimeError("Firmware lacks required live serial feature "+required)
    return s


@contextmanager
def bench_claim():
    """Reserve the shared K6 bench for a single short, attended test run."""
    cmd=[sys.executable,str(ROOT/"tools/k6_bench.py")]
    owner="sotto-tone-live"
    acquired=subprocess.run(cmd+["claim",owner,"3","1kHz Sotto dummy-load tone"],
                            capture_output=True,text=True,timeout=10)
    if acquired.returncode:
        raise RuntimeError("K6 bench unavailable: "+(acquired.stderr or acquired.stdout))
    try:
        yield
    finally:
        released=subprocess.run(cmd+["release",owner],
                                capture_output=True,text=True,timeout=10)
        if released.returncode:
            print("WARNING: bench release failed: "+(released.stderr or released.stdout),
                  file=sys.stderr)

def live_run(args):
    if not args.dummy_load_confirmed:
        raise ValueError("--dummy-load-confirmed is mandatory for a transmit run")
    if args.slot not in VALID_SLOTS:
        raise ValueError("Invalid packed-library slot")
    before=monitor_preflight(args.monitor_url)
    from tools.k6_live import LiveLink
    from tools.k6_port_lock import serial_lock
    from tools.k6_install import open_port
    out=Path(args.capture_dir)
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    (out/"monitor-before.json").write_text(json.dumps(before,indent=2)+"\n")
    outcome=None
    after_mon=None
    try:
        with bench_claim(),serial_lock(args.port),open_port(SimpleNamespace(port=args.port)) as port:
            link=LiveLink(port)
            radio_preflight(link)
            if link.module_capabilities().get('module_api_max',0)<LEVEL:
                raise RuntimeError('Radio firmware lacks the API29 Sotto FSK callback')
            outcome=link.run_module_cooperative(
                args.slot,timeout=20.0,allow_higher_api=True,
                on_report=lambda line:print(line,file=sys.stderr,flush=True))
            after=link.status()
            if after["transmitting"]:
                raise RuntimeError("Radio still reports transmitting after module return")
    finally:
        # Capture evidence even if radio did not return an ACK. Never retry TX.
        for path,name in (("/api/export.json","diagnostics.json"),
                          ("/api/iq.npz","rejected-iq.npz")):
            try:(out/name).write_bytes(url_fetch(args.monitor_url,path))
            except Exception as exc:(out/(name+".error.txt")).write_text(str(exc)+"\n")
        try:
            after_mon=monitor_preflight(args.monitor_url)
            (out/"monitor-after.json").write_text(json.dumps(after_mon,indent=2)+"\n")
        except Exception as exc:
            (out/"monitor-after-error.txt").write_text(str(exc)+"\n")
        if outcome is not None:
            (out/"radio-result.json").write_text(json.dumps(outcome,indent=2)+"\n")
    summary=dict(outcome=outcome,evidence=str(out),
                 module_frames=(outcome or {}).get('result'),
                 module_returned=(outcome or {}).get('returned'))
    if after_mon is not None:
        for key in ('good','bad'):
            summary['crc_'+('accepted' if key=='good' else 'rejected')+'_delta']=max(
                0,int(after_mon.get(key,0))-int(before.get(key,0)))
    # A syntactically successful module result is NOT proof of RF decode.
    summary['radio_return_only']=True
    (out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return summary


def live_install(args):
    if not args.confirm_install:
        raise ValueError("Explicit --confirm-install required to write the radio library")
    if args.slot not in VALID_SLOTS:
        raise ValueError("Invalid packed-library slot")
    if bool(args.cable_only)==bool(args.key_file):
        raise ValueError("Use exactly one of --cable-only or --key-file")
    from tools.k6_live import LiveLink
    from tools.k6_port_lock import serial_lock
    from tools.k6_install import open_port,Serials
    module,entry=code()
    key=None if args.cable_only else k6mod.read_key(args.key_file)
    if key==k6mod.PLACEHOLDER:raise ValueError("Refusing placeholder signing key")
    with serial_lock(args.port),open_port(SimpleNamespace(port=args.port)) as port:
        link=LiveLink(port)
        s=link.status()
        if s["transmitting"] or not s["standby_idle"]:
            raise RuntimeError("Radio must be idle for library installation")
        caps=link.module_capabilities(key)
        if caps.get('module_api_max',0)<LEVEL:
            raise RuntimeError('Radio firmware lacks API29')
        if caps['cable_only']!=args.cable_only:
            raise RuntimeError('Cable-only/keyed installation mode does not match radio')
        if args.slot in link.library_directory() and not args.replace:
            raise RuntimeError("Slot occupied; choose an empty slot or explicitly --replace")
        serials=Serials()
        serials.advance_after(link.serial_watermark())
        image=k6mod.image(module,entry,serials.next(),key,level=LEVEL,lib=args.slot)
        if args.envelope_out:
            with Path(args.envelope_out).open("xb") as f:f.write(image)
        verified=link.mutate_library(image)
        return dict(result=verified,sha256=hashlib.sha256(image).hexdigest())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="command",required=True)
    build_p=sub.add_parser("build",help="Generate deterministic module locally; no radio IO")
    build_p.add_argument("--out",required=True,type=Path)
    install=sub.add_parser("install",help="Explicitly install a precompiled module with live serial")
    install.add_argument("--port",required=True)
    install.add_argument("--slot",required=True,type=int)
    auth=install.add_mutually_exclusive_group(required=True)
    auth.add_argument("--cable-only",action="store_true")
    auth.add_argument("--key-file")
    install.add_argument("--confirm-install",action="store_true")
    install.add_argument("--replace",action="store_true",help="allow replacing an occupied slot")
    install.add_argument("--envelope-out")
    run=sub.add_parser("run",help="Transmit one bounded tone session and preserve SDR evidence")
    run.add_argument("--port",required=True)
    run.add_argument("--slot",required=True,type=int)
    run.add_argument("--dummy-load-confirmed",action="store_true")
    run.add_argument("--monitor-url",default="http://127.0.0.1:8024")
    run.add_argument("--capture-dir",required=True,type=Path)
    a=p.parse_args()
    if a.command=="build":
        module,entry=code()
        a.out.mkdir(parents=True,exist_ok=False)
        (a.out/"module.bin").write_bytes(module)
        manifest=dict(name="SOTTO TONE",level=LEVEL,module_bytes=len(module),
                      entry=entry,sha256=hashlib.sha256(module).hexdigest(),
                      input_audio="8 kHz / 1 kHz sine / 320 ms / PCM16",
                      tx_policy="inert on install; explicitly armed module RUN at 433.500 MHz")
        (a.out/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
        print(json.dumps(manifest,indent=2))
    elif a.command=="install":
        print(json.dumps(live_install(a),indent=2))
    else:
        print(json.dumps(live_run(a),indent=2))


if __name__=="__main__":main()
