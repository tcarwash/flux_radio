"""Milestone 2: optional Sotto voice decoder pack (no radio writes, no flashing).

Covers the Sotto/Base split end to end at the host level: real pack resolution
(sotto is buildable, not planned), K6_SOTTO_SERVICE gating, the capability-bit
contract shared by the firmware patch and the host parser, and proof that the
sotto build links the genuine fixed-point decoder rather than the no-voice stub.
The linked-ELF symbol and size assertions themselves run in the external
acceptance script against real firmware builds.
SPDX-License-Identifier: MIT"""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from k6_feature_packs import resolve  # noqa: E402
from k6_pack_policy import POLICY, gate_environment, validate  # noqa: E402
import k6_install as ki  # noqa: E402

SPEC = json.loads((ROOT / 'config/k6-feature-packs.json').read_text())


def test_sotto_resolves_and_gates_service():
    policy = json.loads(POLICY.read_text())
    validate(SPEC, policy)
    plan = resolve(SPEC, ['sotto'])
    assert plan['packs'] == ['base', 'sotto']
    assert 'sotto-decoder' in plan['capabilities']
    assert plan['modules'] == []
    assert gate_environment(plan, policy)['K6_SOTTO_SERVICE'] == '1'
    assert gate_environment(resolve(SPEC, ['base']), policy)['K6_SOTTO_SERVICE'] == '0'


@pytest.mark.parametrize('sotto,ota', [(True, False), (False, False), (True, True), (False, True)])
def test_sotto_capability_bit_round_trip(sotto, ota):
    mask = 1 | (8 if ota else 16) | (64 if sotto else 0)
    record = b'K6CP' + bytes([2, mask, 23, 0]) + bytes(range(1, 9))
    caps = ki.Cable.parse_capabilities(record)
    assert caps['sotto'] is sotto
    assert caps['cable_only'] is (not ota)
    assert caps['module_api_max'] == 23
    from k6_pack_install import compatibility_report
    lock = {'capabilities': ['module-loader', 'sotto-decoder'], 'modules': []}
    assert compatibility_report(lock, caps)['capabilities_match'] is sotto
    assert compatibility_report(lock, {**caps, 'sotto': False})['capabilities_match'] is False


def test_sotto_bit_matches_generated_build_mask():
    text = (ROOT / 'firmware/k6-aura/apply-capability-query.py').read_text()
    assert '(64 if sotto == "1" else 0)' in text
    assert 'K6_SOTTO_SERVICE' in text


def test_real_decoder_selected_only_for_sotto_build():
    build = (ROOT / 'firmware/k6-aura/build-fd6818b.sh').read_text()
    assert 'K6_SOTTO_SERVICE' in build and '--cfg k6_no_sotto' in build
    assert 'K6_VOICE="$K6_VOICE"' in build
    # The genuine fixed-point decoder defines the decoder entry plus fx_ routines;
    # the no-voice stand-in defines the entry only, so the link check tells them apart.
    dec = (ROOT / 'sotto/c/fixed/sotto_fx_dec.c').read_text()
    assert 'sotto_dec_create' in dec and 'fx_oa_block' in dec
    stub = (ROOT / 'c/k6/sp_novoice.c').read_text()
    assert 'sotto_dec_create' in stub and 'fx_oa_block' not in stub and 'fx_cs512' not in stub
    patch = (ROOT / 'firmware/k6-aura/apply-sotto-service.py').read_text()
    assert 'not(k6_no_sotto)' in patch and 'poll_library_queue' in patch
