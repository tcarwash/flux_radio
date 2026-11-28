#!/usr/bin/env python3
"""Raw RTL-SDR capture with the original FSK13 RF-qualified settings.

Only the exact hash-pinned, authorized API31 Sotto tone module is permitted
to transmit (slot27, 433.500 MHz, dummy load). Separate SDR monitor
must be stopped. No firmware flashing; captures archived, no overwrite.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(HERE/'tools'),'/storage5/dev/flux-live-dv-fifo-rootcause/tools']
from k6_live import LiveLink
from k6_install import open_port
from k6_port_lock import serial_lock
from k6_fsk_rf_record import RtlCapture,save_capture,summarize_raw_iq
from modem import k6mod

CODE_SHA='f332fea1fbcd9e81c7ffb76b25dd8bbe86a6bc017ea786909f8f0c8fde74e9cc'
BASE=Path('/storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125')
PREFIX=BASE/'api31-tone-first-burst-raw01'
PORT='/dev/ttyUSB0'

def run():
    o=subprocess.check_output([sys.executable,str(HERE/'tools/k6_bench.py'),'owner'],text=True).strip()
    if not o.startswith('flux-sotto-rf '):raise RuntimeError('No exclusive bench claim')
    if any(PREFIX.with_suffix(s).exists() for s in ('.npy','.json')):
        raise FileExistsError('Capture exists; no overwrite')
    with serial_lock(PORT),open_port(SimpleNamespace(port=PORT)) as port:
        link=LiveLink(port)
        s=link.status()
        if s['transmitting'] or not s['standby_idle'] or s['vfos'][s['master']]['hz']!=433500000:
            raise RuntimeError('K6 must be idle at 433.500 MHz')
        head=link.library_read(27,16)
        n=int.from_bytes(head[4:6],'little')+24
        image=link.library_read(27,n)
        if (image[12]!=31 or hashlib.sha256(image[16:-8]).hexdigest()!=CODE_SHA
                or k6mod.image_tag(None,image[:-8])!=image[-8:]):
            raise RuntimeError('FSK13 code hash, API or signature mismatch')
        with RtlCapture(center_hz=433750000,fs=1024000,gain_tenth_db=0) as sdr:
            # Allocate and settle RTL before TX; do not assume a quiet channel.
            pre=sdr.read(.3)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(sdr.read,5.0)
                time.sleep(.6)
                t0=time.monotonic()
                outcome=link.run_module_cooperative(27,timeout=15,allow_higher_api=True)
                elapsed=time.monotonic()-t0
                raw=future.result(timeout=20)
        final=link.status()
        if final['transmitting'] or not final['standby_idle']:
            raise RuntimeError('Radio not safely idle after RF: do not repeat')
        meta=summarize_raw_iq(raw)
        meta['hardware_lab_transaction']=dict(
            test='API31 1kHz tone/BEGIN capture, 100ms TX settle, raw 433.750/0dB SDR capture',
            dummy_load_operator_confirmed=True,
            exclusive_bench_owner='flux-sotto-rf',
            expected_hash=CODE_SHA, module_result=outcome,elapsed_s=elapsed,
            firmware_build_id=link.module_capabilities()['build_id'],
            radio_idle_after=not final['transmitting'] and final['standby_idle'])
        files=save_capture(raw,PREFIX,summary=meta)
        print('TONE_MODULE',json.dumps(outcome),flush=True)
        print('CAPTURED',files,'TX_RUN_SEC',round(elapsed,3),flush=True)
        return files

if __name__=='__main__':run()
