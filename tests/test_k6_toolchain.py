import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import k6_toolchain as tc


def executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\nexit 0\n')
    path.chmod(0o755)
    return str(path)


def test_discovery_precedence_and_spaces(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    monkeypatch.setattr(Path, 'home', lambda: home)
    default = executable(home / '.cargo/bin/cargo')
    custom = executable(tmp_path / 'custom cargo/bin/cargo')
    onpath = executable(tmp_path / 'path/cargo')
    explicit = executable(tmp_path / 'override cargo')
    assert tc.resolve_cargo({'PATH': ''}) == default
    assert tc.resolve_cargo({}) == default
    assert tc.resolve_cargo({'CARGO_HOME': str(tmp_path / 'custom cargo')}) == custom
    assert tc.resolve_cargo({'PATH': str(tmp_path / 'path'), 'CARGO_HOME': str(tmp_path / 'custom cargo')}) == onpath
    assert tc.resolve_cargo({'CARGO': explicit}) == explicit
    assert tc.resolve_cargo({'K6_CARGO': explicit, 'CARGO': onpath}) == explicit
    with pytest.raises(ValueError, match='Configured Cargo'):
        tc.resolve_cargo({'K6_CARGO': '/nonexistent/cargo', 'PATH': str(tmp_path / 'path')})


def test_missing_cargo(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    with pytest.raises(ValueError, match='Install Rust.*K6_CARGO'):
        tc.resolve_cargo({'PATH': ''})


def test_symlink_keeps_rustup_dispatch_name(tmp_path):
    rustup = Path(executable(tmp_path / 'rustup'))
    cargo = tmp_path / 'cargo'
    cargo.symlink_to(rustup)
    assert tc.resolve_cargo({'K6_CARGO': str(cargo)}) == str(cargo)


def test_windows_executable_name(tmp_path, monkeypatch):
    calls = []
    def which(name, path):
        calls.append(name)
        return '/tools/cargo.exe' if name == 'cargo.exe' else None
    monkeypatch.setattr(tc.shutil, 'which', which)
    from types import SimpleNamespace
    monkeypatch.setattr(tc, 'os', SimpleNamespace(name='nt', path=os.path, environ=os.environ))
    assert tc.resolve_cargo({'PATH': '/tools'}).endswith('cargo.exe')
    assert calls == ['cargo.exe']


def test_preflight_propagates_and_checks_target(tmp_path, monkeypatch):
    cargo = executable(tmp_path / 'rust/bin/cargo')
    rustc = executable(tmp_path / 'rust/bin/rustc')
    libs = tmp_path / 'target-libs'
    libs.mkdir()
    (libs / 'libcore-test.rlib').touch()
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return str(libs) if command[0] == rustc else 'cargo 1.0'
    monkeypatch.setattr(tc.subprocess, 'check_output', run)
    real_which = tc.shutil.which
    monkeypatch.setattr(tc.shutil, 'which', lambda name, path: real_which(name, path=path) or '/tools/' + name)
    env = tc.preflight(tmp_path, {'K6_CARGO': cargo, 'PATH': ''})
    assert env['CARGO'] == env['K6_CARGO'] == cargo
    assert env['PATH'].startswith(str(Path(cargo).parent))
    assert calls[1][0] == [rustc, '--print', 'target-libdir', '--target', tc.TARGET]
    (libs / 'libcore-test.rlib').unlink()
    with pytest.raises(ValueError, match='rustup target add'):
        tc.preflight(tmp_path, env)


def test_broken_toolchain_is_actionable(tmp_path, monkeypatch):
    cargo = executable(tmp_path / 'cargo')
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0], output='no default toolchain')
    monkeypatch.setattr(tc.subprocess, 'check_output', fail)
    with pytest.raises(ValueError, match='no default toolchain.*rustup toolchain install'):
        tc.preflight(tmp_path, {'K6_CARGO': cargo})


def test_shell_missing_cargo_fails_before_patches(tmp_path):
    env = dict(os.environ, K6_CARGO=str(tmp_path / 'missing-cargo'))
    result = subprocess.run(['sh', str(ROOT / 'firmware/k6-aura/build-fd6818b.sh')],
                            env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Build environment error:' in result.stderr
    assert 'K6_CARGO' in result.stderr
    assert 'Applied' not in result.stdout
    assert 'Traceback' not in result.stderr


def test_catalog_cli_reports_missing_cargo(tmp_path):
    result = subprocess.run([sys.executable, str(ROOT / 'tools/k6.py'), 'build',
                             'base', '--output', str(tmp_path / 'pack')],
                            env=dict(os.environ, K6_CARGO=str(tmp_path / 'missing')),
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Build environment error:' in result.stderr
    assert 'Restart the catalog' in result.stderr
    assert 'Applied' not in result.stdout
    assert not (tmp_path / 'pack/pack.lock.json').exists()


def test_missing_c_tool_fails_preflight(tmp_path, monkeypatch):
    cargo = executable(tmp_path / 'cargo')
    (tmp_path / 'libcore-test.rlib').touch()
    monkeypatch.setattr(tc.subprocess, 'check_output', lambda *a, **kw: str(tmp_path))
    real_which = tc.shutil.which
    monkeypatch.setattr(tc.shutil, 'which',
                        lambda name, path: None if name == 'clang' else
                        (real_which(name, path=path) or '/tools/' + name))
    with pytest.raises(ValueError, match="clang.*missing"):
        tc.preflight(tmp_path, {'K6_CARGO': cargo})
