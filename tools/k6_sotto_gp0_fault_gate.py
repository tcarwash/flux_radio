#!/usr/bin/env python3
"""Offline-only, fail-closed gate for the field-profile K6 GP0 fault trace.

No serial, RF, flash, module installation or background work. A passing
report means READY_FOR_ATTENDED_REVIEW, never automatic flash permission.

The uninstrumented rebuild must be identical to the pinned fielded firmware
except its unique linker-generated GNU eight-byte build ID.
The diagnostic may consume exactly 24 additional protected RAM0 bytes, but
must preserve the arena base, arena size and main-stack reservation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from k6_sotto_spi_profile_gate import (
    EXPECTED_FIELD_SHA256, FLASH_LIMIT, MEMORY_KEYS,
    digest, identical_except_build_id, build_id_note
)

EXPECTED_GP0_MODULE_SHA256='551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e'
RAM0_TRACE_BYTES=24
MIN_TRACE_RAM0_FREE=48

def audit(field:bytes, baseline:bytes, diagnostic:bytes,
          field_layout:dict, baseline_layout:dict, diagnostic_layout:dict,
          module:bytes) -> dict:
    failures=[]
    if digest(field)!=EXPECTED_FIELD_SHA256:
        failures.append('known-good reference firmware SHA256 pin mismatch')
    if not identical_except_build_id(field,baseline):
        failures.append('clean baseline differs outside its unique eight-byte GNU linker ID')
    if hashlib.sha256(module).hexdigest()!=EXPECTED_GP0_MODULE_SHA256:
        failures.append('original field-safe GP0 module code hash changed')
    if not module.startswith(b'K6NMSOTTO GP0'):
        failures.append('GP0 module name is missing')
    for name,data in (('reference',field),('baseline',baseline),('diagnostic',diagnostic)):
        if not data or len(data)>FLASH_LIMIT:
            failures.append(f'{name} is empty or exceeds MCU flash capacity')
        if b'KUL2' not in data:
            failures.append(f'{name} lacks packed KUL2 support')
        if build_id_note(data) is None:
            failures.append(f'{name} lacks a unique expected-position GNU fast build ID')
    if field.count(b'GPF1') or baseline.count(b'GPF1') or diagnostic.count(b'GPF1')!=1:
        failures.append('GPF1 trace must appear exactly once in diagnostic, absent from baseline')
    if any(data.count(b'SPB1') for data in (field,baseline,diagnostic)):
        failures.append('legacy SPI measurement must not be enabled')
    for key in MEMORY_KEYS:
        if field_layout.get(key)!=baseline_layout.get(key) or field_layout.get(key) is None:
            failures.append(f'uninstrumented RAM/stack ABI mismatch: {key}')
    for key in ('arena_base','arena_bytes','stack_reservation'):
        if field_layout.get(key)!=diagnostic_layout.get(key):
            failures.append(f'diagnostic changed GP0 ABI/layout contract: {key}')
    for key in ('ram0_used','ram0_free'):
        if type(field_layout.get(key)) is not int or type(diagnostic_layout.get(key)) is not int:
            failures.append(f'missing numeric RAM0 measurement: {key}')
    if all(type(field_layout.get(k)) is int and type(diagnostic_layout.get(k)) is int
           for k in ('ram0_used','ram0_free')):
        if diagnostic_layout['ram0_used']!=field_layout['ram0_used']+RAM0_TRACE_BYTES:
            failures.append('diagnostic RAM0 size changed by an unexpected amount')
        if diagnostic_layout['ram0_free']!=field_layout['ram0_free']-RAM0_TRACE_BYTES:
            failures.append('RAM0 free-space accounting mismatch')
        if diagnostic_layout['ram0_free']<MIN_TRACE_RAM0_FREE:
            failures.append('diagnostic leaves less than 48 bytes RAM0 free')
    if diagnostic==field or diagnostic==baseline:
        failures.append('diagnostic trace unexpectedly identical to baseline firmware')
    return {
        'status': 'READY_FOR_ATTENDED_REVIEW' if not failures else 'STOP_DO_NOT_FLASH',
        'safe_to_flash_automatically': False,
        'safety_failures':failures,
        'field_firmware_sha256':digest(field),
        'baseline_sha256':digest(baseline),
        'diagnostic_sha256':digest(diagnostic),
        'field_gp0_module_sha256':hashlib.sha256(module).hexdigest(),
        'field_firmware_size':len(field),
        'diagnostic_size':len(diagnostic),
        'diagnostic_flash_free':FLASH_LIMIT-len(diagnostic),
        'baseline_code_identical_except_build_id':identical_except_build_id(field,baseline),
        'arena_and_stack_unchanged':all(field_layout.get(k)==diagnostic_layout.get(k)
                                        for k in ('arena_base','arena_bytes','stack_reservation')),
        'ram0_trace_bytes':RAM0_TRACE_BYTES,
        'diagnostic_ram0_free':diagnostic_layout.get('ram0_free'),
        'note':('Offline structural gate only. Before live tests verify current SPI backup, '
                'signed GP0 module slot, recovery firmware and unattended TX policies; '
                'the gate does not authorize a radio flash or PTT.'),
    }


def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('reference','baseline','diagnostic','module','reference-layout',
                 'baseline-layout','diagnostic-layout','output'):
        p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    report=audit(
        a.reference.read_bytes(),a.baseline.read_bytes(),
        a.diagnostic.read_bytes(),
        json.loads(a.reference_layout.read_text()),
        json.loads(a.baseline_layout.read_text()),
        json.loads(a.diagnostic_layout.read_text()),
        a.module.read_bytes())
    report['paths']={name:str(getattr(a,name.replace('-','_')).resolve())
                     for name in ('reference','baseline','diagnostic','module')}
    with a.output.open('x') as f:
        f.write(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2,sort_keys=True))
    if report['status']!='READY_FOR_ATTENDED_REVIEW':
        p.exit(2,'STOP: fault-trace firmware is not qualified for hardware review\n')

if __name__=='__main__':
    main()
