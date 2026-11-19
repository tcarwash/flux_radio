from pathlib import Path
import sys
import shutil
import pytest
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from k6_build import source_tree, build


def test_materialize_text_links(tmp_path):
    root = tmp_path / 'checkout'
    c = root / 'c'
    (c / 'k6').mkdir(parents=True)
    (c / 'k6/example.c').write_text('example')
    (c / 'fixed').write_text('../sotto/c/fixed')
    (c / 'sotto.h').write_text('../sotto/c/sotto.h')
    sotto = root / 'sotto/c'
    (sotto / 'fixed').mkdir(parents=True)
    (sotto / 'fixed/fx.h').write_text('header')
    for name in ('sotto.h', 'sotto_internal.h', 'sotto_lr_dec.inc', 'sotto_lr_tables.h',
                 'sotto_vlr.h', 'sotto_vlr_dec.inc', 'sotto_vlr_tables.c', 'sotto_vlr_tables.h'):
        (sotto / name).write_text(name)
    actual = source_tree(root, tmp_path / 'staging')
    assert (actual / 'fixed/fx.h').read_text() == 'header'
    assert (actual / 'sotto.h').read_text() == 'sotto.h'
    assert (c / 'fixed').read_text() == '../sotto/c/fixed'


@pytest.mark.skipif(not all(shutil.which(t) for t in ('clang','llvm-ar')), reason='requires LLVM')
@pytest.mark.parametrize('scan', ['0', '1'])
def test_portable_host_build(tmp_path, scan):
    if not (ROOT / 'sotto/c/fixed/fx.h').is_file():
        pytest.skip('requires Sotto submodule')
    env = dict(os.environ, K6_VOICE='0', K6_CB='spi', K6_CODEC='', K6_EXPLORE_HOST='1',
               K6_SCAN_SERVICE=scan, K6_PKT_YIELD='0', K6_OTA_SERVICE='0')
    archive = build(tmp_path / 'output with spaces', env=env)
    assert archive.read_bytes().startswith(b'!<arch>\n')
    names = subprocess.check_output(['llvm-ar','t',str(archive)], text=True).splitlines()
    assert 'sp_novoice.o' in names
    assert ('scan_host_unavailable.o' in names) == (scan == '0')
    assert ('xscan_modhost.o' in names) == (scan == '1')
