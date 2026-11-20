import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import k6
from k6_feature_packs import resolve
from k6_pack_policy import validate, gate_environment, POLICY

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / 'config/k6-feature-packs.json').read_text())
POL = json.loads(POLICY.read_text())


def test_policy_declares_every_capability():
    assert validate(SPEC, POL)
    env = gate_environment(resolve(SPEC, ['scan', 'data']), POL)
    assert env['K6_SCAN_SERVICE'] == '1'
    assert env['K6_FSK_RX_SERVICE'] == '1'
    assert env['K6_SOTTO_SERVICE'] == '0'
    assert gate_environment(resolve(SPEC, ['base']), POL)['K6_SCAN_SERVICE'] == '0'


def test_new_capability_requires_gate_or_always_on():
    spec = json.loads(json.dumps(SPEC))
    spec['packs']['base']['capabilities'].append('new-radio-feature')
    with pytest.raises(ValueError, match='missing explicit service gate'):
        validate(spec, POL)


def test_unified_cli_routes_without_radio_access(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(k6.subprocess, 'call', lambda cmd, cwd: calls.append(cmd) or 0)
    assert k6.main(['plan', 'scan']) == 0
    assert not calls
    import os
    if os.name == 'nt':
        with pytest.raises(SystemExit) as exc:k6.main(['build','scan','data'])
        assert exc.value.code==2 and not calls
    else:
        assert k6.main(['build', 'scan', 'data']) == 0
        assert Path(calls[-1][1]).name == 'k6_pack_build.py' and calls[-1][2:] == [
            'scan', 'data', '--tx-profile', 'ce', '--theme', 'flux']
        token = 'custom-01-c0f0fcfefcf0c000'
        assert k6.main(['build', 'base', '--theme', token]) == 0
        assert calls[-1][2:] == ['base', '--tx-profile', 'ce', '--theme', token]
    assert k6.main(['catalog']) == 0
    assert Path(calls[-1][-2]).name == 'k6_store.py' and calls[-1][-1] == 'serve'
    assert k6.main(['radio', '--port', '/dev/ttyUSB0', 'status']) == 0
    assert Path(calls[-1][-4]).name == 'k6_install.py' and calls[-1][-3:] == ['--port', '/dev/ttyUSB0', 'status']


def test_pack_radio_cli_dispatch(monkeypatch, tmp_path):
    import k6_pack_install

    calls = []
    monkeypatch.setattr(k6_pack_install, 'provision_pack',
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    arguments = [str(tmp_path), '--port', '/dev/ttyUSB0']
    with pytest.raises(SystemExit):
        k6.main(['flash-pack', *arguments])
    assert not calls
    assert k6.main(['flash-pack', *arguments, '--already-update', '--yes']) == 0
    assert calls[-1][1] == {'action': 'flash', 'mode': 'update'}
    assert k6.main(['install-pack', *arguments, '--preview']) == 0
    assert calls[-1][1]['action'] == 'preview'
    assert k6.main(['install-pack', *arguments, '--yes']) == 0
    assert calls[-1][1]['action'] == 'install'
