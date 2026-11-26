"""K6 Sotto SPI gate: 8-byte linker-note tolerance, fail closed otherwise."""
import importlib.util
from pathlib import Path

def load_gate():
    p = Path(__file__).resolve().parents[1] / 'tools/k6_sotto_spi_profile_gate.py'
    spec = importlib.util.spec_from_file_location('k6_sotto_spi_profile_gate', p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

M = load_gate()
L = {'arena_base':'0x20001930', 'arena_bytes':14976,
     'ram0_used':3768, 'ram0_free':72, 'stack_reservation':2812}


def reference(monkeypatch):
    image = (b'KUL2' + b'\0' * 70 + M.GNU_FAST_NOTE_PREFIX +
             bytes.fromhex('b58ea09cddd1bcc4') + b'\0' * M.FIELD_DATA_BYTES)
    monkeypatch.setattr(M, 'EXPECTED_FIELD_SHA256', M.digest(image))
    assert M.build_id_note(image)[1].hex() == 'b58ea09cddd1bcc4'
    return image


def with_id(image: bytes, value: bytes) -> bytes:
    position, _ = M.build_id_note(image)
    assert len(value) == 8
    return image[:position] + value + image[position+8:]


def test_matching_candidate_gate_requires_additional_review(monkeypatch):
    field = reference(monkeypatch)
    candidate = field[:4] + b'SPB1' + field[4:]
    r = M.audit(field, field, candidate, L, L, L)
    assert r['status'] == 'READY_FOR_ATTENDED_REVIEW'
    assert not r['safe_to_flash_automatically']
    assert not r['safety_failures']
    assert r['baseline_matches_reference']
    assert r['baseline_code_equivalent_except_build_id']
    assert r['candidate_flash_free'] == M.FLASH_LIMIT - len(candidate)


def test_only_gnu_build_id_difference_accepted(monkeypatch):
    field = reference(monkeypatch)
    rebuilt = with_id(field, bytes.fromhex('4ccd2df6da433470'))
    candidate = rebuilt[:4] + b'SPB1' + rebuilt[4:]
    r = M.audit(field, rebuilt, candidate, L, L, L)
    assert r['status'] == 'READY_FOR_ATTENDED_REVIEW'
    assert r['baseline_code_equivalent_except_build_id']
    assert r['baseline_build_id_only_difference']
    assert not r['baseline_matches_reference']
    assert r['candidate_changes_build_id']
    assert r['dependent_executable_spi_pages_need_review']


def test_additional_executable_byte_difference_is_rejected(monkeypatch):
    field = reference(monkeypatch)
    rebuilt = bytearray(with_id(field, b'\x01'*8))
    rebuilt[50] ^= 0x40  # outside the precisely located note's last eight bytes
    baseline = bytes(rebuilt)
    candidate = baseline[:4] + b'SPB1' + baseline[4:]
    r = M.audit(field, baseline, candidate, L, L, L)
    assert r['status'] == 'STOP_DO_NOT_FLASH'
    assert not r['baseline_code_equivalent_except_build_id']
    assert any('outside' in s for s in r['safety_failures'])


def test_wrong_note_size_or_location_rejected(monkeypatch):
    field = reference(monkeypatch)
    corrupt = field.replace(M.GNU_FAST_NOTE_PREFIX, b'\xff'*16)
    r = M.audit(field, corrupt, field[:4]+b'SPB1'+field[4:], L, L, L)
    assert r['status'] == 'STOP_DO_NOT_FLASH'
    assert any('build ID note' in s for s in r['safety_failures'])
    duplicate = field[:4]+M.GNU_FAST_NOTE_PREFIX+field[4:]
    assert M.build_id_note(duplicate) is None


def test_baseline_size_mismatch_rejected_despite_matching_ram(monkeypatch):
    field = reference(monkeypatch)
    different = field + b'\0'
    candidate = field[:4]+b'SPB1'+field[4:]
    r = M.audit(field, different, candidate, L, L, L)
    assert r['status'] == 'STOP_DO_NOT_FLASH'
    assert not r['baseline_matches_reference']
    assert any('outside' in s for s in r['safety_failures'])


def test_unsafe_rambudget_rejects(monkeypatch):
    field = reference(monkeypatch)
    diff = dict(L, ram0_free=16)
    candidate = field[:4]+b'SPB1'+field[4:]
    r = M.audit(field, field, candidate, L, L, diff)
    assert r['status'] == 'STOP_DO_NOT_FLASH'
    assert any('ram0_free' in s for s in r['safety_failures'])


def test_no_probe_or_corrupted_library_rejects(monkeypatch):
    field = reference(monkeypatch)
    for candidate in (field, b'SPB1'+b'\0'*800,
                      field[:4]+b'SPB1SPB1'+field[4:]):
        r = M.audit(field, field, candidate, L, L, L)
        assert r['status'] == 'STOP_DO_NOT_FLASH'


def test_oversize_rejects(monkeypatch):
    field = reference(monkeypatch)
    # Still a correctly placed linker note, with a full size .data suffix.
    core = b'KUL2SPB1' + b'\0'*(M.FLASH_LIMIT-8-M.FIELD_DATA_BYTES)
    candidate = core+M.GNU_FAST_NOTE_PREFIX+b'\x99'*8+b'\0'*M.FIELD_DATA_BYTES
    r = M.audit(field, field, candidate, L, L, L)
    assert r['status'] == 'STOP_DO_NOT_FLASH'
    assert any('flash size' in s for s in r['safety_failures'])


def test_field_sha_pin_is_enforced(monkeypatch):
    field = reference(monkeypatch)
    monkeypatch.setattr(M,'EXPECTED_FIELD_SHA256','deadbeef')
    r = M.audit(field, field, field[:4]+b'SPB1'+field[4:], L, L, L)
    assert r['status']=='STOP_DO_NOT_FLASH'
