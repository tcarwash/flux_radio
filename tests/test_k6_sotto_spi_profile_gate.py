"""K6 SPI diagnostic build gate is byte-exact and fails closed."""
import importlib.util
from pathlib import Path

def load_gate():
    p=Path(__file__).resolve().parents[1]/'tools/k6_sotto_spi_profile_gate.py'
    spec=importlib.util.spec_from_file_location('k6_sotto_spi_profile_gate',p)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

M=load_gate()
L={'arena_base':'0x20001930','arena_bytes':14976,
   'ram0_used':3768,'ram0_free':72,'stack_reservation':2812}

def good_data(monkeypatch):
    field=b'KUL2' + b'\0'*300
    monkeypatch.setattr(M,'EXPECTED_FIELD_SHA256',M.digest(field))
    return field

def test_matching_candidate_gate_requires_additional_review(monkeypatch):
    field=good_data(monkeypatch)
    r=M.audit(field,field,field+b'SPB1',L,L,L)
    assert r['status']=='READY_FOR_ATTENDED_REVIEW'
    assert not r['safety_failures']
    assert r['baseline_matches_reference']
    assert r['candidate_flash_free']==M.FLASH_LIMIT-len(field)-4

def test_baseline_mismatch_rejects_despite_matching_ram(monkeypatch):
    field=good_data(monkeypatch)
    different=field+b'\0'
    r=M.audit(field,different,different+b'SPB1',L,L,L)
    assert r['status']=='STOP_DO_NOT_FLASH'
    assert not r['baseline_matches_reference']
    assert any('differs' in p for p in r['safety_failures'])

def test_unsafe_rambudget_rejects(monkeypatch):
    field=good_data(monkeypatch)
    diff=dict(L,ram0_free=16)
    r=M.audit(field,field,field+b'SPB1',L,L,diff)
    assert r['status']=='STOP_DO_NOT_FLASH'
    assert any('ram0_free' in p for p in r['safety_failures'])

def test_no_probe_or_corrupted_library_rejects(monkeypatch):
    field=good_data(monkeypatch)
    for image in (field, b'SPB1' + b'\0'*300, field+b'SPB1SPB1'):
        r=M.audit(field,field,image,L,L,L)
        assert r['status']=='STOP_DO_NOT_FLASH'

def test_oversize_rejects(monkeypatch):
    field=good_data(monkeypatch)
    candidate=b'KUL2SPB1' + b'\0'*M.FLASH_LIMIT
    r=M.audit(field,field,candidate,L,L,L)
    assert r['status']=='STOP_DO_NOT_FLASH'
    assert any('flash size' in p for p in r['safety_failures'])
