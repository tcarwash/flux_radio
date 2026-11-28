"""Offline, fail-closed gate for firmware-owned GP0 error classification."""
from pathlib import Path
import hashlib
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import k6_sotto_gp0_fault_gate as G

REF_LAYOUT={
    'arena_base':'0x20001930','arena_bytes':14976,
    'ram0_used':3768,'ram0_free':72,'stack_reservation':2812}
TRACE_LAYOUT={**REF_LAYOUT,'ram0_used':3792,'ram0_free':48}

def fixture(monkeypatch):
    # Correct-position 24-byte GNU note, with 732 bytes of .data.
    h=G.build_id_note
    note=bytes.fromhex('040000000800000003000000474e5500')
    field=b'KUL2'+b'\0'*128+note+b'A'*8+b'\0'*732
    baseline=field[:148]+b'B'*8+field[156:]
    diagnostic=field[:4]+b'GPF1'+field[4:]
    # Use unique GNU note IDs despite different image contents.
    at=diagnostic.index(note)+16
    diagnostic=diagnostic[:at]+b'C'*8+diagnostic[at+8:]
    assert h(field) and h(baseline) and h(diagnostic)
    module=b'K6NMSOTTO GP0'+b'\x01'*9784
    monkeypatch.setattr(G,'EXPECTED_FIELD_SHA256',hashlib.sha256(field).hexdigest())
    monkeypatch.setattr(G,'EXPECTED_GP0_MODULE_SHA256',hashlib.sha256(module).hexdigest())
    return field,baseline,diagnostic,module

def audit(fixture_data,reference=REF_LAYOUT,baseline=REF_LAYOUT,diagnostic=TRACE_LAYOUT):
    field,rebuilt,trace,module=fixture_data
    return G.audit(field,rebuilt,trace,reference,baseline,diagnostic,module)

def test_good_profile_only_enters_attended_review(monkeypatch):
    f=fixture(monkeypatch)
    r=audit(f)
    assert r['status']=='READY_FOR_ATTENDED_REVIEW'
    assert not r['safe_to_flash_automatically']
    assert r['baseline_code_identical_except_build_id']
    assert r['arena_and_stack_unchanged']
    assert r['diagnostic_ram0_free']==48

def test_reject_baseline_code_difference(monkeypatch):
    f,b,d,m=fixture(monkeypatch)
    b=b[:42]+b'\xff'+b[43:]
    r=audit((f,b,d,m))
    assert r['status']=='STOP_DO_NOT_FLASH'
    assert any('baseline differs' in s for s in r['safety_failures'])

def test_reject_tight_ram0_or_changed_arena_and_stack(monkeypatch):
    f=fixture(monkeypatch)
    for key,value in [('ram0_free',16),('ram0_used',3800),
                      ('arena_base','0x20001950'),
                      ('arena_bytes',14800),('stack_reservation',2788)]:
        r=audit(f,diagnostic={**TRACE_LAYOUT,key:value})
        assert r['status']=='STOP_DO_NOT_FLASH',(key,r)

def test_reject_nontrace_and_probe_and_missing_gnu_id(monkeypatch):
    f,b,d,m=fixture(monkeypatch)
    for trace in (d.replace(b'GPF1',b'\0'*4),
                  d.replace(b'GPF1',b'SPB1'),
                  d[:4]+b'GPF1'+d[4:],
                  d.replace(b'GNU\0',b'\0'*4)):
        r=audit((f,b,trace,m))
        assert r['status']=='STOP_DO_NOT_FLASH'

def test_ref_sha_and_module_hash_are_pinned(monkeypatch):
    f=fixture(monkeypatch)
    monkeypatch.setattr(G,'EXPECTED_FIELD_SHA256','bad')
    assert audit(f)['status']=='STOP_DO_NOT_FLASH'
    monkeypatch.setattr(G,'EXPECTED_FIELD_SHA256',hashlib.sha256(f[0]).hexdigest())
    monkeypatch.setattr(G,'EXPECTED_GP0_MODULE_SHA256','bad')
    assert audit(f)['status']=='STOP_DO_NOT_FLASH'

def test_reject_flash_overflow_and_invalid_layout_type(monkeypatch):
    field,baseline,diagnostic,module=fixture(monkeypatch)
    oversized=diagnostic[:3]+b'\0'*(G.FLASH_LIMIT)+diagnostic[3:]
    assert audit((field,baseline,oversized,module))['status']=='STOP_DO_NOT_FLASH'
    assert audit((field,baseline,diagnostic,module),
                 diagnostic={**TRACE_LAYOUT,'ram0_free':None})['status']=='STOP_DO_NOT_FLASH'

def test_trace_never_reuses_prior_dma_counters_before_capture():
    source=(ROOT/'firmware/k6-aura/sotto-gp0-fault.rs.inc').read_text()
    start=source.index('fn gp0_fault_trace_finish')
    stop=source.index('fn mod_sotto_resource_read_traced',start)
    block=source[start:stop]
    assert 'if GP0_FAULT_TRACE.state&4!=0 {' in block
    assert block.index('if GP0_FAULT_TRACE.state&4!=0 {')<block.index('SOTTO_GP0_ERROR')
