"""Legacy Aura manifests must build on hosts with BSD sed as well as GNU sed."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'firmware/k6-aura/ensure-host-feature.py'
spec = importlib.util.spec_from_file_location('host_feature', SCRIPT)
host_feature = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_feature)


def test_legacy_manifest_adds_feature_once_even_with_comment(tmp_path):
    path = tmp_path / 'Cargo.toml'
    path.write_text('[features]\n# blowfang-host is supplied by Flux\nspilib = []\nother = []\n')
    host_feature.ensure_host_feature(path)
    expected = ('[features]\n# blowfang-host is supplied by Flux\n'
                'spilib = []\nblowfang-host = ["spilib"]\nother = []\n')
    assert path.read_text() == expected
    host_feature.ensure_host_feature(path)
    assert path.read_text() == expected


def test_existing_feature_preserved_byte_for_byte(tmp_path):
    path = tmp_path / 'Cargo.toml'
    original = b'[features]\r\nspilib = []\r\nblowfang-host = ["spilib"]\r\n'
    path.write_bytes(original)
    host_feature.ensure_host_feature(path)
    assert path.read_bytes() == original


@pytest.mark.parametrize('text', ['[features]\n', 'spilib = []\nspilib = []\n'])
def test_missing_or_ambiguous_anchor_leaves_manifest_untouched(tmp_path, text):
    path = tmp_path / 'Cargo.toml'
    path.write_text(text)
    with pytest.raises(ValueError, match='exactly one'):
        host_feature.ensure_host_feature(path)
    assert path.read_text() == text
