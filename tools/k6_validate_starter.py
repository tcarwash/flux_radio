#!/usr/bin/env python3
"""Smoke-test an extracted starter archive without contacting any radio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


def verify(archive):
    archive = Path(archive).resolve()
    expected = archive.with_name(archive.name + '.sha256').read_text().split()[0]
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise ValueError('starter archive checksum mismatch')
    with tempfile.TemporaryDirectory(prefix='flux-starter-check-') as directory:
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:
                raise ValueError('damaged ZIP')
            z.extractall(directory)
        root = Path(directory) / archive.stem
        for name in ('install.bat', 'START-HERE.txt', 'starter-release.json',
                     'tools/k6_port_lock.py', 'docs/manual/recovery.md', 'requirements.txt'):
            if not (root / name).is_file():
                raise ValueError(f'missing {name}')
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        def run(*args):
            subprocess.run([sys.executable, *args], cwd=root, env=env, check=True)
        run('tools/k6.py', 'starter-install', '--help')
        run('tools/k6.py', 'inspect', 'starter')
        run('-c', '''
import json, sys
from pathlib import Path
sys.path.insert(0, 'tools')
from k6_feature_packs import DEFAULT
from k6_pack_install import provision_artifacts
from k6_store import ModuleStore
lock, firmware, identity, key = provision_artifacts(Path('starter'), json.loads(DEFAULT.read_text()), None)
meta = json.loads(Path('starter-release.json').read_text())
assert key is None and lock['packs'] == meta['packs']
assert lock['tx_profile'] == meta['tx_profile']
assert identity and len(firmware) <= 122880
store = ModuleStore(config=Path('test-settings/module-store.json'))
sources = store.sources()
expected = {m['name'] for m in lock['modules']}
actual = {Path(s['source']).name for s in sources if Path(s['source']).is_dir() and Path(s['source']).parent.name == 'local-modules'}
assert expected.issubset(actual), (expected, actual)
print('Verified extracted starter:', meta['profile'], meta['tx_profile'], len(firmware), sorted(expected))
''')
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    verify(parser.parse_args().archive)
