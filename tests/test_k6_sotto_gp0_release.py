"""A reproducible cable-only GP0 TX candidate cannot silently overrun limits."""
from pathlib import Path
import hashlib
import json
import os
import pytest
from tools.k6_sotto_gp0_release import build_release
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope='module')
def firmware_image():
    p=Path(os.environ.get('K6_SOTTO_GP0_RELEASE_IMAGE',
        '/tmp/k6-sotto-gp0-fw-20261125-final/flux-fd6818b-gp0-experimental.bin'))
    if not p.is_file():pytest.skip('No exact compiled firmware image; binary build is opt-in')
    return p


def test_offline_release_manifest_and_module_is_installable(tmp_path,firmware_image):
    out=tmp_path/'release'
    info=build_release(firmware_image,out)
    assert info['module_size_bytes']<=9800
    assert info['module_scratch_margin']>=0
    assert info['firmware_flash_headroom']>=0
    assert not info['flashed_to_radio'] and not info['on_radio_receiver_included']
    assert (out/'module-bundle/manifest.json').exists()
    for fn,want in info['sha256'].items():
        assert hashlib.sha256((out/fn).read_bytes()).hexdigest()==want
    assert json.loads((out/'release.json').read_text())==info
    assert not (out/'module-bundle/manifest.json').read_text().count('slot":')
    assert 'NOT AUTOMATED' in (out/'README.md').read_text()
    with pytest.raises(FileExistsError):build_release(firmware_image,out)


def test_invalid_firmware_rejected_without_creating_release(tmp_path):
    invalid=tmp_path/'oversized.bin';invalid.write_bytes(b'0'*122881)
    with pytest.raises(ValueError,match='flash size'):
        build_release(invalid,tmp_path/'release')
    assert not (tmp_path/'release').exists()
