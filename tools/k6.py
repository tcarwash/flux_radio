#!/usr/bin/env python3
"""Unified Flux radio CLI. Radio writes require explicit subcommands and approval."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from k6_feature_packs import DEFAULT, resolve, THEMES, DEFAULT_THEME, validate_theme, TX_PROFILES, DEFAULT_TX_PROFILE, validate_tx_profile


def command(argv):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Delegate legacy flags verbatim, including --port before the radio verb.
    if argv and argv[0] in ('radio', 'catalog'):
        tool = 'k6_install.py' if argv[0] == 'radio' else 'k6_store.py'
        rest = argv[1:] or (['serve'] if argv[0] == 'catalog' else [])
        if not rest:
            raise ValueError('radio requires a subcommand (status, backup, flash, load, ...)')
        return subprocess.call([sys.executable, str(ROOT / 'tools' / tool), *rest], cwd=ROOT)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    plan = sub.add_parser('plan', help='resolve capability packs without building')
    plan.add_argument('packs', nargs='+')
    plan.add_argument('--spec', type=Path, default=DEFAULT)
    build = sub.add_parser('build', help='build firmware and modules; no radio writes')
    build.add_argument('packs', nargs='+')
    build.add_argument('--output', type=Path)
    build.add_argument('--spec', type=Path)
    for cmd_parser in (plan, build):
        cmd_parser.add_argument('--theme', type=validate_theme, default=DEFAULT_THEME, metavar="THEME")
        cmd_parser.add_argument('--tx-profile', type=validate_tx_profile, choices=TX_PROFILES, default=DEFAULT_TX_PROFILE)
    build.add_argument('--theme-name', default='', help='optional display name carried by the theme package')
    starter = sub.add_parser('starter-install', help='guided first install with SPI backup and format confirmation')
    starter.add_argument('pack', type=lambda value: Path(value).expanduser().resolve())
    starter.add_argument('--port', required=True)
    inspect = sub.add_parser('inspect', help='check staged feature-pack integrity (no radio writes)')
    inspect.add_argument('pack', type=Path)
    inspect.add_argument('--spec', type=Path)
    preflight = sub.add_parser('preflight', help='read-only connected-radio compatibility check; restarts radio on session close')
    preflight.add_argument('pack', type=Path)
    preflight.add_argument('--port', required=True)
    preflight.add_argument('--spec', type=Path)
    preflight.add_argument('--key-file', type=Path, help='authenticate capability report using an existing key')
    for verb in ('flash-pack', 'install-pack', 'flash-install-pack'):
        help_text = {'flash-pack': 'flash pack firmware', 'install-pack': 'install compatible pack modules',
                     'flash-install-pack': 'flash firmware, then replace and install its bundled modules'}
        pack = sub.add_parser(verb, help=help_text[verb])
        pack.add_argument('pack', type=lambda value: Path(value).expanduser().resolve())
        pack.add_argument('--port', required=True)
        pack.add_argument('--key-file', type=Path)
        confirm = pack.add_mutually_exclusive_group(required=True)
        confirm.add_argument('--yes', action='store_true', help='authorize this radio write')
        if verb != 'install-pack':
            pack.add_argument('--recover', type=Path, help='resume a pending packed upgrade journal')
            pack.add_argument('--already-update', action='store_true', help='the radio is already showing UPDATE')
        else:
            confirm.add_argument('--preview', action='store_true', help='show module destinations without writing')
    matrix = sub.add_parser('matrix', help='verify flash budgets of separately linked profiles')
    matrix.add_argument('--size-dir', type=Path)
    matrix.add_argument('--build', action='store_true')
    matrix.add_argument('--build-dir', type=Path)
    matrix.add_argument('--output', type=Path)
    catalog = sub.add_parser('catalog', help='launch Flux local module catalog web UI')
    catalog.add_argument('args', nargs=argparse.REMAINDER)
    radio = sub.add_parser('radio', help='radio cable operations (status, backup, flash, load)')
    radio.add_argument('args', nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    if args.command == 'starter-install':
        from k6_starter_install import install
        from k6_install import InstallError
        try:
            install(args.pack, json.loads(DEFAULT.read_text()), args.port)
        except (InstallError, ValueError, OSError) as exc:
            p.error(str(exc))
        return 0
    if args.command == 'plan':
        print(json.dumps(resolve(json.loads(args.spec.read_text()), args.packs, args.theme, args.tx_profile), indent=2))
        return 0
    if args.command in ('flash-pack', 'install-pack', 'flash-install-pack'):
        from k6_pack_install import provision_pack
        from k6_install import InstallError
        action = ('flash' if args.command == 'flash-pack' else
                  'flash-install' if args.command == 'flash-install-pack' else
                  'preview' if args.preview else 'install')
        try:
            provision_pack(args.pack, json.loads(DEFAULT.read_text()), args.port, args.key_file,
                           action=action, mode='update' if getattr(args, 'already_update', False) else 'cable', **({'recovery':args.recover} if getattr(args,'recover',None) else {}))
        except (InstallError, ValueError, OSError) as exc:
            p.error(str(exc))
        return 0
    if args.command == 'build':
        if os.name == 'nt':
            p.error('firmware builds require Linux or WSL; use a prebuilt starter on Windows')
        cmd = ['k6_pack_build.py', *args.packs, '--tx-profile', args.tx_profile, '--theme', args.theme]
        if args.theme_name: cmd += ['--theme-name', args.theme_name]
        if args.output: cmd += ['--output', str(args.output.expanduser().resolve())]
        if args.spec: cmd += ['--spec', str(args.spec.expanduser().resolve())]
    elif args.command == 'inspect':
        cmd = ['k6_pack_install.py', str(args.pack.expanduser().resolve()), '--inspect-unverified']
        if args.spec: cmd += ['--spec', str(args.spec.expanduser().resolve())]
    elif args.command == 'preflight':
        cmd = ['k6_pack_install.py', str(args.pack.expanduser().resolve()), '--radio-preflight', '--port', args.port]
        if args.key_file: cmd += ['--key-file', str(args.key_file.expanduser().resolve())]
        if args.spec: cmd += ['--spec', str(args.spec.expanduser().resolve())]
    elif args.command == 'matrix':
        cmd = ['k6_pack_matrix.py']
        if args.size_dir: cmd += ['--size-dir', str(args.size_dir)]
        if args.build: cmd += ['--build']
        if args.build_dir: cmd += ['--build-dir', str(args.build_dir)]
        if args.output: cmd += ['--output', str(args.output.expanduser().resolve())]
    elif args.command == 'catalog':
        cmd = ['k6_store.py', *(args.args or ['serve'])]
    else:
        if not args.args:
            p.error('radio requires a subcommand; use radio status, backup, flash, load, etc.')
        cmd = ['k6_install.py', *args.args]
    return subprocess.call([sys.executable, str(ROOT / 'tools' / cmd[0]), *cmd[1:]], cwd=ROOT)


def main(argv=None):
    try:
        return command(argv)
    except (OSError, ValueError, KeyError) as exc:
        print(f'k6: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
