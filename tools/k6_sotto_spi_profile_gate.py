#!/usr/bin/env python3
"""Offline-only, fail-closed K6 Sotto SPI diagnostic compatibility gate.

Compare the uninstrumented firmware against the exact known-good physical
reference. The GNU --build-id=fast note contains an eight-byte linker hash
that changes between clean source-worktree paths, even with bit-identical
flash executable code. Permit ONLY that precisely located 8-byte difference,
never a code/rodata/metadata-layout difference.

A successful audit is for ATTENDED REVIEW, not permission to flash or TX.
No radio, network, cable, flash, or RF hardware operations occur here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

FLASH_LIMIT = 122880
FIELD_DATA_BYTES = 732
GNU_FAST_NOTE_PREFIX = bytes.fromhex('040000000800000003000000474e5500')
EXPECTED_FIELD_SHA256 = '8d38e8973e89d32099791c34a5c9d7c68520869564bffc004c08d4695fa4827d'
MEMORY_KEYS = ('arena_base','arena_bytes','ram0_used','ram0_free','stack_reservation')


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_id_note(image: bytes) -> tuple[int, bytes] | None:
    """Return ID position/value from exactly one correctly placed .k6_id note.

    A 24-byte ELF GNU note sits after rodata, before the fielded firmware's
    exactly 732-byte .data load image. Reject occurrences in other content.
    This is intentionally specific to the verified API30 binary profile.
    """
    if image.count(GNU_FAST_NOTE_PREFIX) != 1:
        return None
    pos = image.find(GNU_FAST_NOTE_PREFIX)
    if pos + 24 != len(image) - FIELD_DATA_BYTES:
        return None
    return pos + len(GNU_FAST_NOTE_PREFIX), image[pos + 16:pos + 24]


def identical_except_build_id(reference: bytes, rebuilt: bytes) -> bool:
    """Exactly one 8-byte GNU fast build ID may differ; nothing else."""
    a, b = build_id_note(reference), build_id_note(rebuilt)
    if a is None or b is None or len(reference) != len(rebuilt):
        return False
    if a[0] != b[0]:
        return False
    at = a[0]
    return (reference[:at] == rebuilt[:at]
            and reference[at + 8:] == rebuilt[at + 8:])


def audit(field: bytes, baseline: bytes, candidate: bytes,
          field_layout: dict, baseline_layout: dict,
          candidate_layout: dict) -> dict:
    failures = []
    if digest(field) != EXPECTED_FIELD_SHA256:
        failures.append('reference firmware is not the pinned known-good KUL2 image')

    ids = {name: build_id_note(image) for name, image in
           [('reference', field), ('baseline', baseline), ('candidate', candidate)]}
    for name, note in ids.items():
        if note is None:
            failures.append(f'{name} lacks correctly located unique GNU 8-byte build ID note')

    code_match = identical_except_build_id(field, baseline)
    if not code_match:
        failures.append('uninstrumented clean rebuild differs outside its 8-byte GNU build ID')

    for name, data in [('reference', field), ('baseline', baseline), ('candidate', candidate)]:
        if b'KUL2' not in data:
            failures.append(f'{name} lacks packed KUL2 module-library support')
        if not data or len(data) > FLASH_LIMIT:
            failures.append(f'{name} exceeds flash size or is empty')

    if baseline.count(b'SPB1') or candidate.count(b'SPB1') != 1:
        failures.append('SPI timing probe signature not isolated to instrumented image')

    for key in MEMORY_KEYS:
        vals = [field_layout.get(key), baseline_layout.get(key), candidate_layout.get(key)]
        if any(value is None for value in vals) or vals[0] != vals[1] or vals[0] != vals[2]:
            failures.append(f'RAM/stack memory contract mismatch: {key} = {vals}')

    ref_id = ids['reference'][1] if ids['reference'] else b''
    base_id = ids['baseline'][1] if ids['baseline'] else b''
    candidate_id = ids['candidate'][1] if ids['candidate'] else b''

    return {
        'status': 'READY_FOR_ATTENDED_REVIEW' if not failures else 'STOP_DO_NOT_FLASH',
        'safe_to_flash_automatically': False,
        'reference_sha256': digest(field),
        'baseline_sha256': digest(baseline),
        'candidate_sha256': digest(candidate),
        'reference_size': len(field),
        'baseline_size': len(baseline),
        'candidate_size': len(candidate),
        'candidate_flash_free': FLASH_LIMIT - len(candidate),
        'baseline_matches_reference': baseline == field,
        'baseline_code_equivalent_except_build_id': code_match,
        'baseline_build_id_only_difference': code_match and baseline != field,
        'reference_build_id': ref_id.hex() if ref_id else None,
        'baseline_build_id': base_id.hex() if base_id else None,
        'candidate_build_id': candidate_id.hex() if candidate_id else None,
        'candidate_changes_build_id': bool(ref_id and candidate_id and candidate_id != ref_id),
        'dependent_executable_spi_pages_need_review': bool(
            ref_id and candidate_id and ref_id != candidate_id),
        'ram0_contract_matches': all(
            field_layout.get(k) == baseline_layout.get(k) == candidate_layout.get(k)
            for k in MEMORY_KEYS),
        'safety_failures': failures,
        'note': ('Build-only audit. GNU fast-build-ID differences are not executable code changes, '
                 'but ID-dependent SPI pages may require regeneration; READY does not authorize '
                 'flash, reboot, module migration or PTT.'),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', required=True, type=Path)
    p.add_argument('--baseline', required=True, type=Path)
    p.add_argument('--candidate', required=True, type=Path)
    p.add_argument('--reference-layout', required=True, type=Path)
    p.add_argument('--baseline-layout', required=True, type=Path)
    p.add_argument('--candidate-layout', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path, help='New JSON manifest; cannot overwrite')
    args = p.parse_args()
    report = audit(args.reference.read_bytes(), args.baseline.read_bytes(),
                   args.candidate.read_bytes(), json.loads(args.reference_layout.read_text()),
                   json.loads(args.baseline_layout.read_text()),
                   json.loads(args.candidate_layout.read_text()))
    report['paths'] = {
        'reference': str(args.reference.resolve()),
        'baseline': str(args.baseline.resolve()),
        'candidate': str(args.candidate.resolve())}
    with args.output.open('x') as file:
        file.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps(report, indent=2, sort_keys=True))
    if report['status'] != 'READY_FOR_ATTENDED_REVIEW':
        p.exit(2, 'STOP: preflash qualification rejected the candidate\n')


if __name__ == '__main__':
    main()
