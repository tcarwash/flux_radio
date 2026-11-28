import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from k6_feature_packs import resolve
from k6_pack_build import assemble
from k6_pack_install import PackError, verify_pack

SPEC = json.loads((Path(__file__).resolve().parents[1] / 'config/k6-feature-packs.json').read_text())


def pack(tmp_path):
    image = tmp_path / 'fw.bin'
    image.write_bytes(b'firmware fixture')
    def build(name, source, folder):
        (folder / 'module.bin').write_bytes(name.encode())
        (folder / 'manifest.json').write_text(json.dumps({'library': [
            {'id': f'org.blowfang.k6.{name}', 'level': 16, 'file': 'module.bin'}]}))
    root = tmp_path / 'pack'
    assemble(resolve(SPEC, ['scan', 'data']), root, image, module_builder=build)
    return root


def test_unattested_firmware_refused(tmp_path):
    root = pack(tmp_path)
    with pytest.raises(PackError, match='not implemented'):
        verify_pack(root, SPEC)
    assert {m['name'] for m in verify_pack(root, SPEC, require_attested=False)['modules']} == {'surf', 'explore_scan', 'cardlink', 'fluxtnc'}
    lock = json.loads((root / 'pack.lock.json').read_text())
    lock['firmware']['capabilities_verified'] = True
    (root / 'pack.lock.json').write_text(json.dumps(lock))
    with pytest.raises(PackError, match='not implemented'):
        verify_pack(root, SPEC)


def test_tampered_module_rejected(tmp_path):
    root = pack(tmp_path)
    (root / 'modules/surf/module.bin').write_bytes(b'evil')
    with pytest.raises(PackError, match='hash mismatch'):
        verify_pack(root, SPEC, require_attested=False)


def test_tampered_capabilities_rejected(tmp_path):
    root = pack(tmp_path)
    lock = json.loads((root / 'pack.lock.json').read_text())
    lock['capabilities'].remove('explore-scanner-api')
    (root / 'pack.lock.json').write_text(json.dumps(lock))
    with pytest.raises(PackError, match='capabilities'):
        verify_pack(root, SPEC, require_attested=False)


def test_manifest_hash_conflict_rejected(tmp_path):
    root = pack(tmp_path)
    manifest_path = root / 'modules/surf/manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['sha256'] = {'module.bin': '0' * 64}
    manifest_path.write_text(json.dumps(manifest))
    lock_path = root / 'pack.lock.json'
    lock = json.loads(lock_path.read_text())
    import hashlib
    next(m for m in lock['modules'] if m['name'] == 'surf')['manifest_sha256'] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(PackError, match='manifest hash differs'):
        verify_pack(root, SPEC, require_attested=False)


def test_compatibility_report_fails_closed_even_if_capabilities_match(tmp_path):
    from k6_pack_install import compatibility_report
    root = pack(tmp_path)
    lock = verify_pack(root, SPEC, require_attested=False)
    reported = {'module_loader': True, 'scanner': True, 'fsk_rx': True, 'module_api_max': 22,
                'authenticated': True, 'build_identity_verified': True}
    report = compatibility_report(lock, reported)
    assert report['capabilities_match']
    assert not report['install_ready'] and not report['authenticated']
    report = compatibility_report(lock, {**reported, 'scanner': False, 'module_api_max': 15})
    assert not report['capabilities_match']
    assert 'candidate-ranking' in report['missing_capabilities']
    assert set(report['unsupported_modules']) == {'surf', 'explore_scan', 'cardlink', 'fluxtnc'}


def test_radio_preflight_checks_artifacts_before_opening_port(tmp_path, monkeypatch):
    import k6_install
    from k6_pack_install import radio_preflight
    root = pack(tmp_path)
    (root / 'firmware.bin').write_bytes(b'bad')
    monkeypatch.setattr(k6_install, 'open_port', lambda *_: pytest.fail('opened port before verification'))
    with pytest.raises(PackError, match='hash mismatch'):
        radio_preflight(root, SPEC, '/dev/fake')


def test_radio_preflight_closes_session_on_error(tmp_path, monkeypatch):
    import k6_install
    from k6_pack_install import radio_preflight
    from types import SimpleNamespace
    root = pack(tmp_path)
    events = []
    connection = SimpleNamespace(close=lambda: events.append('close'))
    def capabilities():
        raise k6_install.InstallError('legacy firmware refuses command')
    cable = SimpleNamespace(capabilities=capabilities, end=lambda: events.append('end'))
    monkeypatch.setattr(k6_install, 'open_port', lambda args: connection)
    monkeypatch.setattr(k6_install, 'open_cable', lambda *args, **kwargs: cable)
    with pytest.raises(PackError, match='legacy firmware'):
        radio_preflight(root, SPEC, '/dev/fake')
    assert events == ['end', 'close']
