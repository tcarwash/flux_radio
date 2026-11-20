"""Cargo discovery and early Rust checks shared by firmware build entry points."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

TARGET = 'thumbv6m-none-eabi'


def resolve_cargo(env=None):
    env = os.environ if env is None else env
    override = env.get('K6_CARGO') or env.get('CARGO')
    name = 'cargo.exe' if os.name == 'nt' else 'cargo'
    candidates = [override] if override else [
        name, str(Path(env.get('CARGO_HOME') or Path.home() / '.cargo') / 'bin' / name)]
    for candidate in candidates:
        found = shutil.which(os.path.expanduser(candidate), path=env.get('PATH', ''))
        if found:
            # Do not resolve symlinks: rustup dispatches using argv[0].
            return os.path.abspath(found)
    detail = f'Configured Cargo executable {override!r} is unavailable.' if override else 'Cargo was not found.'
    raise ValueError(detail + ' Install Rust with rustup, or set K6_CARGO to the full path to cargo'
                     ' (cargo.exe on Windows). Restart the catalog after changing its environment.')


def preflight(work, env=None):
    env = dict(os.environ if env is None else env)
    cargo = resolve_cargo(env)
    env['K6_CARGO'] = env['CARGO'] = cargo
    env['PATH'] = str(Path(cargo).parent) + os.pathsep + env.get('PATH', '')
    cwd = work if Path(work).is_dir() else Path(work).parent.parent
    def run(command):
        try:
            return subprocess.check_output(command, cwd=cwd, env=env, stderr=subprocess.STDOUT,
                                           text=True).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            detail = ' '.join((getattr(exc, 'output', None) or str(exc)).split())
            raise ValueError(f'Rust toolchain preflight failed: {detail.strip()}. '
                             f'Run rustup toolchain install stable and rustup target add {TARGET}; '
                             'check K6_CARGO and restart the catalog.') from None
    run([cargo, '--version'])
    rustc = env.get('RUSTC') or shutil.which('rustc', path=env['PATH'])
    if not rustc:
        raise ValueError('Rust compiler missing beside Cargo or on PATH. Install Rust with rustup.')
    libdir = Path(run([rustc, '--print', 'target-libdir', '--target', TARGET]))
    if not any(libdir.glob('libcore-*.rlib')):
        raise ValueError(f'Rust target {TARGET} is missing. Run rustup target add {TARGET} '
                         'for the firmware toolchain, then retry the catalog build.')
    for tool in (env.get('K6_CC', 'clang'), env.get('K6_AR', 'llvm-ar'),
                 env.get('K6_OBJCOPY', 'llvm-objcopy'), env.get('HOSTCC', 'cc'),
                 'llvm-size', 'llvm-objcopy', 'git'):
        if not shutil.which(tool, path=env['PATH']):
            raise ValueError(f'Build tool {tool!r} is missing. Install the LLVM/C build tools '
                             'and Git, add them to the catalog PATH, then restart the catalog.')
    return env


if __name__ == '__main__':
    try:
        print(preflight(Path(sys.argv[1]))['K6_CARGO'])
    except ValueError as exc:
        sys.exit(f'Build environment error: {exc}')
