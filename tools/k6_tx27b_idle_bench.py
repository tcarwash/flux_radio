#!/usr/bin/env python3
"""One-shot RX-only K6 register baseline, new experiment #40 after failed #20.

Preflight is OFFLINE. Physical --run requires explicit operator approval,
nonblocking shared bench+serial lock, enrolled build, untouched original
modules/VFO, secure temporary slot, and independent postflight. The *module*
never writes RFIC registers or keys TX. No automatic retries or recovery
reflashing. A loss of state certainty is HOLD, never qualified PASS.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import hashlib
import fcntl
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
PORT='/dev/ttyUSB0'
ISSUE=40
TEMP_SLOT=30
EXPECTED_BUILD='5d8e7f39844d42f9'
EXPECTED_PROTOCOL=5
EXPECTED_API=31
EXPECTED_VFOS=((433500000,'FM',True),(144390000,'FM',True))
SOURCE=ROOT/'c/k6/mod/tx27b_reg51_idle_diag.c'
DEFAULT_BENCH=Path.home()/'.cache/flux/bench.lock'
HOLD_SENTINEL=DEFAULT_BENCH.parent/'bench.hold'
EXPECTED_SOURCE_SHA256='faf60e3d0da01324da96330776261f78ba5df96960a5057f79851cca1a07f015'


class Refused(RuntimeError):
    pass


def source_guard(source:Path=SOURCE):
    text=source.read_text()
    for banned in ('->fd_write(', '->tx_on(', '->tx_pa(', '->tx_off(',
                   '->tx_on_quiet(', 'mmio_wr(', 'REG_PA'):
        if banned in text:raise Refused(f'RX-only source contains {banned!r}')
    for present in ('#define K6_MODULE_LEVEL 3','return 27;',
                    'a->fd_read(R_DCSCTRL)','TX27B_DELAY_US 5000u'):
        if present not in text:raise Refused(f'RX source drift: {present!r} missing')
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    if source.resolve()==SOURCE.resolve() and sha!=EXPECTED_SOURCE_SHA256:
        raise Refused('RX-only source SHA256 changed; new independent review is required')
    return sha


def preflight():
    source_hash=source_guard()
    from modem import k6mod
    code,entry=k6mod.build(str(SOURCE))
    if not (0<len(code)<=1600) or not 0<=entry<len(code):
        raise Refused(f'RX module size/entry outside bound: {len(code)} bytes, entry {entry}')
    return dict(source_sha256=source_hash,
                module_code_sha256=hashlib.sha256(code).hexdigest(),
                code_size=len(code),entry=entry,
                physical_rf=False,RFIC_writes=0,transmitter_calls=0,
                intent='RX-only one-shot idle REG51 after #20 errors',issue=ISSUE),code,entry


@contextmanager
def bench_lock(path=DEFAULT_BENCH):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    hold=HOLD_SENTINEL if path==DEFAULT_BENCH else path.parent/(path.name+'.hold')
    if hold.exists():raise Refused(f'bench is on independent-restoration HOLD: {hold}')
    with path.open('a+b') as handle:
        try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:raise Refused('shared Flux bench lock is held') from exc
        try:
            if hold.exists():raise Refused(f'bench is on independent-restoration HOLD: {hold}')
            yield
        finally:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)


def digest(b):return hashlib.sha256(b).hexdigest()


def snapshot(link):
    status=link.status()
    directory=dict(link.library_directory())
    blobs={}
    for slot,(_address,length) in sorted(directory.items()):
        if not 0<length<=16384:raise Refused(f'packed slot {slot} invalid size {length}')
        blobs[str(slot)]={'size':length,'sha256':digest(bytes(link.library_read(slot,length)))}
    return dict(status=status,blobs=blobs)


def assert_idle_stable(before,after,*,require_slot30_empty=True):
    for label,x in (('before',before),('after',after)):
        s=x['status'];v=s.get('vfos')
        if s.get('transmitting') is not False or s.get('standby_idle') is not True:
            raise Refused(f'{label}: radio not standby idle/TX OFF')
        if not isinstance(v,list) or len(v)!=2:
            raise Refused(f'{label}: invalid VFO pair')
        for got,(hz,mode,wide) in zip(v,EXPECTED_VFOS):
            if (got.get('hz'),got.get('mode'),got.get('wide'))!=(hz,mode,wide):
                raise Refused(f'{label}: VFO not in approved 433.500/144.390 FM-wide pair')
        if require_slot30_empty and str(TEMP_SLOT) in x['blobs']:
            raise Refused(f'{label}: temporary slot 30 not empty')
    if before['blobs']!=after['blobs']:
        raise Refused('original packed slots changed after RX-only diagnostic')
    if before['status']['vfos']!=after['status']['vfos']:
        raise Refused('VFO/mode changed')


def verify_report(result):
    if not result.get('returned') or result.get('result')!=27:
        raise Refused(f'RX-only module returned {result.get("result")}; expected 27')
    reports=result.get('reports',[])
    if len(reports)!=4 or not all(str(t).startswith('tx27b ') for t in reports):
        raise Refused(f'incomplete diagnostic reports: {reports!r}')
    import re
    match=re.fullmatch(r'tx27b 51a=([a-f0-9]{4}) 51b=([a-f0-9]{4})',str(reports[0]))
    if not match or match.group(1)!=match.group(2):
        raise Refused('REG51 unstable or invalid report format')
    for pattern,line in ((r'tx27b 08=[a-f0-9]{4} 52=[a-f0-9]{4}',reports[1]),
                         (r'tx27b 70=[a-f0-9]{4} 71=[a-f0-9]{4}',reports[2]),
                         (r'tx27b 30a=[a-f0-9]{4} 30b=[a-f0-9]{4}',reports[3])):
        if not re.fullmatch(pattern,str(line)):
            raise Refused(f'malformed register report: {line!r}')
    r30=re.fullmatch(r'tx27b 30a=([a-f0-9]{4}) 30b=([a-f0-9]{4})',reports[3])
    if not r30 or int(r30.group(1),16)&2 or int(r30.group(2),16)&2:
        raise Refused('RF state register indicates TX during RX diagnostic')
    return dict(reg51=int(match.group(1),16),reg51_hex=match.group(1),
                reg51_nonzero=int(match.group(1),16)!=0,source='physical_module_report_unverified_RF',
                reports=reports)


def execute(prefix:Path,info,code,entry):
    if not prefix.is_absolute() or prefix.exists():
        raise Refused('fresh absolute --prefix must not exist (one-shot)')
    prefix.mkdir(mode=0o700,parents=True)
    result=dict(issue=ISSUE,source=info,radio_port=PORT,
                stage='RX_ONLY',physical_rf=False,
                fresh_one_shot=True,verdict='HOLD')
    (prefix/'manifest.json').write_text(json.dumps(result,indent=2)+'\n')
    def save():
        (prefix/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    committed=False
    install_attempted=False
    link=None
    stack=None
    serials=None
    baseline=None
    cleaned=False
    with bench_lock():
        try:
            from k6_install import open_port,Serials
            from k6_live import LiveLink
            from k6_port_lock import serial_lock
            from modem import k6mod
            from contextlib import ExitStack
            stack=ExitStack()
            stack.enter_context(serial_lock(PORT))
            p=stack.enter_context(open_port(SimpleNamespace(port=PORT)))
            link=LiveLink(p)
            radio=link.info()
            build=radio['build_id'].hex() if radio.get('build_id') else None
            if build!=EXPECTED_BUILD or radio.get('protocol')!=EXPECTED_PROTOCOL:
                raise Refused(f'enrolled build/protocol changed: {build}/{radio.get("protocol")}')
            caps=link.module_capabilities(key=None)
            if caps.get('module_api_max')!=EXPECTED_API or not caps.get('cable_only'):
                raise Refused('API31 cable-only enrollment not verified')
            features=link.capabilities()['features']
            for feat in ('lib_mutate','module_caps','run'):
                if feat not in features:raise Refused(f'required host feature {feat} missing')
            baseline=snapshot(link)
            assert_idle_stable(baseline,baseline)
            result['baseline']=baseline
            serials=Serials()
            serials.advance_after(link.serial_watermark())
            ser=serials.next()
            image=k6mod.image(code,entry,ser,None,level=3,lib=TEMP_SLOT)
            result['install_image']=dict(length=len(image),sha256=digest(image),serial=ser)
            # After beginning a library mutation the result may be lost even
            # if the radio committed the slot. Treat it as *possibly* present
            # until a full snapshot verifies otherwise.
            committed=True
            install_attempted=True
            result['install_attempted']=True
            installed=link.mutate_library(image)
            result['install_result']=installed
            if not installed.get('verified'):
                raise Refused('install response unverified; do not execute')
            response=link.run_module(TEMP_SLOT,timeout=12.,allow_higher_api=True,
                                     on_report=lambda t:result.setdefault('reports',[]).append(t))
            result['module_result']=response
            observed_reports=response.get('reports') or result.get('reports',[])
            result['diagnostic']=verify_report({**response,'reports':observed_reports})
            serials.advance_after(link.serial_watermark())
            erase=k6mod.image(b'K6ERASE',0,serials.next(),None,level=1,lib=TEMP_SLOT,remove=True)
            erased=link.mutate_library(erase)
            result['erase_result']=erased
            if not erased.get('verified'):
                raise Refused('temporary module erase not verified')
            committed=False
            afterward=snapshot(link)
            result['after']=afterward
            assert_idle_stable(baseline,afterward)
            cleaned=True
            result['verdict']='PASS_RX_ONLY'
        except Exception as exc:
            result['error']=f'{type(exc).__name__}: {exc}'
            # A single best-effort cleanup attempt, never a retest/auto-repeat.
            # Do not claim success if an install may have committed invisibly.
            if link is not None and serials is not None and committed:
                try:
                    from modem import k6mod
                    # Read directory before the single cleanup erase. No
                    # blind erase if the uncertain install never committed.
                    directory=dict(link.library_directory())
                    if TEMP_SLOT in directory:
                        serials.advance_after(link.serial_watermark())
                        erase=k6mod.image(b'K6ERASE',0,serials.next(),None,level=1,lib=TEMP_SLOT,remove=True)
                        result['failure_cleanup']=link.mutate_library(erase)
                    else:
                        result['failure_cleanup']={'slot_absent':True}
                    committed=False
                except Exception as cleanup_exc:
                    result['failure_cleanup_error']=str(cleanup_exc)
            if link is not None and baseline is not None:
                try:
                    after=snapshot(link)
                    result['after_failure']=after
                    assert_idle_stable(baseline,after)
                    cleaned=True
                except Exception as cleanup_exc:
                    result['verification_error']=str(cleanup_exc)
            result['verdict']=('BLOCKED_RESTORED' if cleaned else
                               'HOLD_UNVERIFIED' if install_attempted else
                               'BLOCKED_NO_MUTATION')
            if install_attempted and not cleaned:
                # A stopped process cannot keep flock forever: a persistent
                # hold marker prevents later cooperative runs from treating
                # this bench as free. Clear only after independent inspection.
                HOLD_SENTINEL.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                HOLD_SENTINEL.write_text(json.dumps({'issue':ISSUE,
                    'prefix':str(prefix),'reason':result.get('error'),
                    'required':'independent full radio/slot/VFO/TX-off verification'},indent=2)+'\n')
                result['hold_sentinel']=str(HOLD_SENTINEL)
        finally:
            save()
            if stack is not None:stack.close()
        # The bench lock remains held through module cleanup and postflight.
    return 0 if result['verdict']=='PASS_RX_ONLY' else 2


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight-only',action='store_true')
    modes.add_argument('--run',action='store_true')
    parser.add_argument('--prefix',type=Path)
    parser.add_argument('--issue',type=int,required=True)
    parser.add_argument('--operator-approved',action='store_true')
    args=parser.parse_args(argv)
    if args.issue!=ISSUE:
        parser.error('fresh diagnostic requires GitHub issue #40; never reuse #20')
    try:
        info,code,entry=preflight()
        if args.preflight_only:
            print(json.dumps(info,indent=2));return 0
        if not args.operator_approved:
            raise Refused('explicit operator approval for temporary module installation required')
        if args.prefix is None:raise Refused('--prefix fresh absolute output path required')
        return execute(args.prefix,info,code,entry)
    except (OSError,Refused,RuntimeError) as exc:
        print(f'BLOCKED: {exc}',file=sys.stderr)
        return 2

if __name__=='__main__':raise SystemExit(main())
