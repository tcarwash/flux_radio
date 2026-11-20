"""Catalog status and install gates are independent of physical radio access."""
import hashlib
import json
from pathlib import Path
import pytest
from test_k6_store_defaults import k6_store as store
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import k6_install as ki


def bundle(tmp_path, code=b'abc', level=21):
    (tmp_path / 'module.bin').write_bytes(code)
    (tmp_path / 'manifest.json').write_text(json.dumps({'library': [{'kind': 'module', 'file': 'module.bin', 'level': level}], 'sha256': {'module.bin': hashlib.sha256(code).hexdigest()}}))
    return {'path': tmp_path}


def test_stale_api_and_arena_are_blocked(tmp_path):
    info = {'status': 'targeted', 'source_module': 'explore_scan', 'expected_api': 21}
    assert store.local_compatibility(info, bundle(tmp_path, level=6))['status'] == 'blocked'
    assert store.local_compatibility(info, bundle(tmp_path, code=b'x'*10689))['status'] == 'blocked'
    assert store.local_compatibility(info, bundle(tmp_path))['status'] == 'targeted'
    (tmp_path/'module.bin').write_bytes(b'wrong')
    assert store.local_compatibility(info, {'path': tmp_path})['install_blocked']


def test_experimental_requires_ack_and_pack_flags():
    info = store.local_compatibility({'status':'experimental', 'required_packs':['data']}, None)
    with pytest.raises(ValueError, match='acknowledge'):
        store.catalog_install_flags(info)
    assert store.catalog_install_flags(info, True) == ['--require-capability','module_loader','--require-capability','fsk_rx']
    with pytest.raises(ValueError, match='WIP'):
        store.catalog_install_flags(store.local_compatibility({'status':'wip'}, None), True)


def test_capability_check_fails_before_writes():
    class Cable:
        def capabilities(self):
            return {'module_loader':True, 'scanner':False, 'module_api_max':20}
    with pytest.raises(ki.InstallError, match='scanner'):
        ki.check_required_capabilities(Cable(), ['scanner'])
    with pytest.raises(ki.InstallError, match='API level 21'):
        ki.check_required_capabilities(Cable(), ['module_loader'], [{'kind':'module','level':21}])
    ki.check_required_capabilities(Cable(), ['module_loader'], [{'kind':'module','level':6}])


def test_policy_covers_legacy_tx_and_current_compact_game():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root/'config/flux-catalog.json').read_text())['local_metadata']
    for slug in ('cwkey','ft8tx','wsprbcn','wsprtx','rangetx','morse'):
        assert policy[slug]['status'] == 'wip'
    assert policy['gw']['source_module'] == 'gw_flux'
    assert policy['explore_scan']['expected_api'] == 21


def test_confirmed_modules_keep_preflight_without_experimental_ack():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root/'config/flux-catalog.json').read_text())['local_metadata']
    for slug in ('aprs', 'aprs-gps-sync', 'midi', 'aprs-beacon', 'gw'):
        info = store.local_compatibility(policy[slug], None)
        assert info['status'] == 'tested'
        assert info['status_label'] == 'Hardware tested'
        assert not info['install_blocked']
        assert '--require-capability' in store.catalog_install_flags(info)
    assert 'fsk_rx' in store.catalog_install_flags(store.local_compatibility(policy['cardlink'], None), True)
    assert policy['aprs-gps-sync']['expected_api'] == 23


def test_native_flux_origin_is_independent_of_hardware_status():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root/'config/flux-catalog.json').read_text())['local_metadata']
    assert {slug for slug, meta in policy.items() if meta.get('native_flux')} == {'aprs','aprs-gps-sync','cardlink','aprs-beacon'}
    for slug in ('aprs','aprs-gps-sync','aprs-beacon'):
        info = store.local_compatibility(policy[slug], None)
        assert info['native_flux'] and info['status'] == 'tested'
    for slug in ('midi','gw'):
        info = store.local_compatibility(policy[slug], None)
        assert not info['native_flux'] and info['status'] == 'tested'
    info = store.local_compatibility({'native_flux': True, 'status': 'wip'}, None)
    assert info['native_flux'] and info['install_blocked']
