#!/usr/bin/env python3
"""Prospective SINGLE short FD6818B FIFO-AE IRQ telemetry hardware transaction.

NEVER invoke without explicit exclusive queue ACK, confirmed dummy load, and
operator approval. The underlying harness grabs the canonical NONBLOCKING
serial flock, verifies chip/build, signed original slot30, exact two VFOs,
channel idle and SDR, enforces 1900ms TX lease, restores authentic slot30 and
original operator VFOs and records IQ / report. No FIFO refills or flash.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from modem import k6mod
from k6_fsk_boundary_bench import run_transaction

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'c/k6/mod/fsk_bchip_ae_qual_armed.c'
SOURCE_SHA256='7ebd2a4ac62314abee6781c8ca194a39607c667d1b1e0582a60fc6e5a65812cf'
CODE_SHA256='a14bf5a7538c6510de54b85a0cc69580ae86fa06453e53efd7ca802c9f046879'


def preflight():
    src=SOURCE.read_bytes()
    source_sha=hashlib.sha256(src).hexdigest()
    code,entry=k6mod.build(str(SOURCE))
    code_sha=hashlib.sha256(code).hexdigest()
    if source_sha!=SOURCE_SHA256 or code_sha!=CODE_SHA256:
        raise ValueError('Unfrozen / altered FSK-09S-H module. NO radio access permitted')
    if not 0<len(code)<4096 or entry!=0 or k6mod.source_level(str(SOURCE))!=3:
        raise ValueError('Unexpected Cortex module code/ABI')
    return dict(source_sha256=source_sha,code_sha256=code_sha,code_bytes=len(code))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prefix',required=True,type=Path)
    p.add_argument('--port',default='/dev/ttyUSB0')
    p.add_argument('--dummy-load-confirmed',action='store_true')
    p.add_argument('--operator-lease-confirmed',action='store_true')
    p.add_argument('--queue-lease-acknowledged',action='store_true')
    a=p.parse_args()
    if not all([a.dummy_load_confirmed,a.operator_lease_confirmed,a.queue_lease_acknowledged]):
        p.error('Refusing hardware operation without dummy load, operator approval and separate queue ACK')
    print('Frozen preflight:',preflight(),flush=True)
    res=run_transaction(a.port,a.prefix,SOURCE,
       a.prefix.with_name(a.prefix.name+'-slot30-original.k6m'),
       armed=True,expected_result=98,
       experiment='FSK-09S-H B-chip AE IRQ telemetry: thresholds 24 vs 8, two 49-word frames, no refills',
       watchdog_ms=1900)
    print('Restored',res['original_module_restored'],res['original_radio_frequency_restored'],
          'Completed',res['transmission_confirmed_by_firmware'],flush=True)

if __name__=='__main__':main()
