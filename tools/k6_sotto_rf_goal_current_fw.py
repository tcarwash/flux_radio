#!/usr/bin/env python3
"""Single-owner API31 Sotto RF bench experiments, with reversible slot-27 swaps.

Uses existing Flux cable/serial APIs. Explicit install and run subcommands;
no radio, SDR, or bench I/O on import/--help. No firmware flash.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parents[1]
if str(HERE/'tools') not in sys.path:sys.path.insert(0,str(HERE/'tools'))
from k6_install import Cable,CABLE_HANDSHAKE,open_port
from k6_port_lock import serial_lock
from k6_live import LiveLink
from modem import k6mod

BASE=Path('/storage5/dev/sotto-monitor/evidence/bench-ptt-sdr-size-20261125')
PREVIOUS=Path('/storage5/dev/sotto-monitor/evidence/bench-api31-20261125/slot27-pre.k6m')
OWNER='sotto-ptt-sdr-20261125'
PORT='/dev/ttyUSB0'
HZ=433500000
SLOT=27
URL='http://127.0.0.1:8024'
TUNE_HZ=-30500
CAPTURE_CENTER_MHZ=433.75
BUILD='b6a3df0d360d3110'
FSK13_CODE_SHA='cd4dd68674658cdf5a9b44cc40af32f2679d9f1c09580017beebd4def1a86295'
FIXTURES=Path('/storage5/dev/sotto-monitor/evidence/bench-rf-goal-20261125')
TONE=Path('/storage5/dev/flux-sotto-tone-api31-build-20261125/sotto-1k-fixture-module.bin')
TONE_SETTLE100=FIXTURES/'sotto-tone-settle100.bin'
TONE_ONRADIO_PCM=FIXTURES/'sotto-onradio-pcm-direct.bin'
TONE_ONRADIO_PCM20=FIXTURES/'sotto-pcm-rf-settle20-gap0/module.bin'

def has_bench():
    s=subprocess.check_output([sys.executable,str(HERE/'tools/k6_bench.py'),'owner'],text=True).strip()
    if not s.startswith(OWNER+' '):
        raise RuntimeError('Refusing hardware work without exclusive '+OWNER+' bench ownership: '+s)

def slot_bytes(c):
    h=c.library_read(SLOT,16)
    if len(h)!=16 or h[:4]!=b'K6M\x01':raise RuntimeError('No typed module envelope in slot27')
    n=int.from_bytes(h[4:6],'little')+24
    if not 24<n<20000:raise RuntimeError('Unexpected slot length')
    raw=c.library_read(SLOT,n)
    if len(raw)!=n or k6mod.image_tag(None,raw[:-8])!=raw[-8:]:
        raise RuntimeError('Existing radio module envelope corrupt')
    return raw

def sha(raw):return hashlib.sha256(raw).hexdigest()
def name(raw):
    result=k6mod.module_name(raw[16:-8])
    if result:return result
    if sha(raw[16:-8])==FSK13_CODE_SHA and raw[12]==3:return 'FSK13'
    return None
def save(path,data):
    if path.exists():raise FileExistsError(path)
    path.write_bytes(data)
    path.chmod(0o600)

def loader(op,new_module=None,expect=None):
    has_bench()
    BASE.mkdir(parents=True,exist_ok=True)
    with serial_lock(PORT),open_port(SimpleNamespace(port=PORT)) as p:
        c=Cable(p,lambda *args,**kw:None)
        if not c.handshake(CABLE_HANDSHAKE,wait=6):
            raise RuntimeError('No cable handshake; do not assume a slot changed')
        try:
            caps=c.capabilities()
            if caps.get('build_id')!=BUILD or not caps.get('cable_only') or caps.get('module_api_max',0)<31:
                raise RuntimeError('Unexpected radio build/cable/API31')
            old=slot_bytes(c)
            old_name=name(old)
            if old_name!=expect:
                raise RuntimeError(f'Slot27 is {old_name!r}, expected {expect!r}; refusing overwrite')
            if old_name=='FSK DIAG' and old[16:-8]!=PREVIOUS.read_bytes()[16:-8]:
                raise RuntimeError('FSK DIAG differs from exact known recovery envelope; refusing overwrite')
            if op=='restore':
                ref=PREVIOUS.read_bytes()
                if name(ref)!='FSK DIAG':raise RuntimeError('Backup is not FSK DIAG')
                code=ref[16:-8];entry=k6mod.bin_entry(code)
                level=ref[12]
            else:
                if old_name=='FSK DIAG':
                    orig=BASE/'original-signed-slot27.k6m'
                    if not orig.exists():save(orig,old)
                    elif orig.read_bytes()[16:-8]!=old[16:-8]:
                        raise RuntimeError('Original FSK DIAG changed')
                code=Path(new_module).read_bytes()
                if not 0<len(code)<12000:
                    raise RuntimeError('Invalid module size')
                if k6mod.module_name(code)=='SOTTO DRAIN':
                    level=31
                elif k6mod.module_name(code)=='SOTTO 1K FX' and code in (TONE.read_bytes(),TONE_SETTLE100.read_bytes()):
                    level=31
                elif k6mod.module_name(code)=='SOTTO PCM RF' and code in (TONE_ONRADIO_PCM.read_bytes(),TONE_ONRADIO_PCM20.read_bytes()):
                    level=31
                elif sha(code)==FSK13_CODE_SHA and k6mod.bin_entry(code)==0:
                    level=3  # Exact 8/8 RF-proven control from prior physical test
                else:
                    raise RuntimeError('Only known SOTTO DRAIN, frozen API31 1k tone, or frozen FSK13 control allowed')
                entry=k6mod.bin_entry(code)
            serial=max(int(time.time()),int.from_bytes(old[8:12],'little')+1)
            result=k6mod.image(code,entry,serial,None,level=level,lib=SLOT)
            if result[12]!=level or result[15]!=SLOT:raise RuntimeError('Envelope mismatch')
            label=('restore-' if op=='restore' else 'install-')+str(serial)
            path=BASE/(label+'.k6m')
            save(path,result)
            r,have,total,written=c.send_image(result,serial&255)
            if r!=3 or written!=len(result):
                raise RuntimeError(f'Uncertain module write status {r}/{written}; inspect before retry')
            if slot_bytes(c)!=result:raise RuntimeError('Radio readback mismatch')
            out=dict(operation=op,previous=old_name,installed=name(result),level=level,slot=SLOT,
                     serial=serial,sha256=sha(result),bytes=len(result),timestamp=time.time())
            (BASE/(label+'.json')).write_text(json.dumps(out,indent=2)+'\n')
            return out
        finally:
            c.end() # Exit cable mode and restart normal application

def radio_preflight():
    has_bench()
    with serial_lock(PORT),open_port(SimpleNamespace(port=PORT)) as p:
        link=LiveLink(p)
        s=link.status()
        raw=slot_bytes(link)
        if not s['standby_idle'] or s['transmitting'] or s['vfos'][s['master']]['hz']!=HZ:
            raise RuntimeError('Radio must be idle at 433.500 MHz')
        if not ((name(raw) in ('SOTTO DRAIN','SOTTO 1K FX','SOTTO PCM RF') and raw[12]==31)
                or (name(raw)=='FSK13' and raw[12]==3)):
            raise RuntimeError('Wrong module in slot27')
        return s

def monitor_get(path):
    with urllib.request.urlopen(URL+path,timeout=5) as f:return f.read()

def monitor_start():
    q=dict(freq=433.5,device=0,gain=0,offset_hz=TUNE_HZ,demo=False)
    request=urllib.request.Request(URL+'/api/start',data=json.dumps(q).encode(),
                                   headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=8) as f:
        if not json.load(f).get('ok'):raise RuntimeError('SDR monitor start refused')

def capture_run(label):
    has_bench()
    radio_preflight()
    out=BASE/label
    if out.exists():raise FileExistsError(out)
    out.mkdir(parents=True)
    before=json.loads(monitor_get('/api/status?after=0'))
    if (before.get('state')!='receiving' or before.get('freq')!=433.5 or
        before.get('offset_hz')!=TUNE_HZ or before.get('capture_center_mhz')!=CAPTURE_CENTER_MHZ):
        raise RuntimeError('SDR monitor not ready at reference center and tuned offset')
    (out/'before.json').write_text(json.dumps(before,indent=2)+'\n')
    samples=[]
    stop=threading.Event()
    def watch():
        while not stop.is_set():
            try:
                d=json.loads(monitor_get('/api/status?after=0'))
                spec=d.get('spectrum') or []
                samples.append(dict(t=time.monotonic(),level=d.get('level'),
                                    good=d.get('good'),bad=d.get('bad'),
                                    peak=max(spec) if spec else None,
                                    peakbin=spec.index(max(spec)) if spec else None))
            except Exception as exc:
                samples.append(dict(t=time.monotonic(),error=str(exc)))
            time.sleep(.04)
    th=threading.Thread(target=watch,daemon=True);th.start()
    time.sleep(.5)
    radio=None
    try:
        cmd=[sys.executable,str(HERE/'tools/k6_live.py'),'--port',PORT,
             'run-coop',str(SLOT),'--allow-higher-api','--timeout','15']
        t0=time.monotonic()
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=22)
        radio=dict(rc=p.returncode,stdout=p.stdout,stderr=p.stderr,started=t0,
                   elapsed_s=round(time.monotonic()-t0,3))
    finally:
        time.sleep(1)
        stop.set()
        th.join(timeout=5)
        (out/'spectral-timeline.json').write_text(json.dumps(dict(radio=radio,samples=samples),indent=2)+'\n')
        for ep,file in (('/api/export.json','diagnostics.json'),('/api/iq.npz','rejected-iq.npz'),
                        ('/api/status?after=0','after.json')):
            try:(out/file).write_bytes(monitor_get(ep))
            except Exception as exc:(out/(file+'.error')).write_text(str(exc)+'\n')
    after=json.loads((out/'after.json').read_text())
    if not radio:raise RuntimeError('No radio run result')
    result=dict(radio=radio,monitor=dict(
        good_before=before.get('good'),good_after=after.get('good'),
        bad_before=before.get('bad'),bad_after=after.get('bad'),
        frames_after=after.get('frames'),state=after.get('state')),
        events=[dict(id=e.get('id'),kind=e.get('kind'),sync_bit_errors=e.get('sync_bit_errors'))
                for e in after.get('events',[])],
        evidence=str(out))
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    inst=sub.add_parser('install')
    inst.add_argument('--module',required=True,type=Path)
    inst.add_argument('--expect',choices=['FSK DIAG','SOTTO DRAIN','FSK13','SOTTO 1K FX','SOTTO PCM RF'],required=True)
    restore=sub.add_parser('restore')
    restore.add_argument('--expect',choices=['SOTTO DRAIN','FSK13','SOTTO 1K FX','SOTTO PCM RF'],default='SOTTO DRAIN')
    run=sub.add_parser('run')
    run.add_argument('--label',required=True)
    sub.add_parser('start-sdr')
    a=p.parse_args()
    if a.command=='install':print(json.dumps(loader('install',a.module,a.expect),indent=2))
    elif a.command=='restore':print(json.dumps(loader('restore',expect=a.expect),indent=2))
    elif a.command=='run':print(json.dumps(capture_run(a.label),indent=2))
    else:
        has_bench();monitor_start();print('SDR started')
if __name__=='__main__':main()
