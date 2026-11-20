#!/usr/bin/env python3
"""Build a K6 capability pack and lock its module artifacts; never writes to radio."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from k6_feature_packs import DEFAULT, resolve, THEMES, DEFAULT_THEME, validate_theme, TX_PROFILES, DEFAULT_TX_PROFILE, validate_tx_profile
import k6_install
from k6_flash_policy import assess, flash_bytes
from k6_pack_policy import POLICY, validate, gate_environment, storage_environment
from k6_build_lock import exclusive_build
from k6_capability_receipt import write as write_receipt
from k6_build_identity import image_build_id


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def assemble(plan, output, firmware, *, module_builder=None, verified_firmware=False, theme_name='', system_page=None, packed=False, system_pages=()):
    """Stage a deterministic lockfile and module bundles from a prebuilt firmware image."""
    output = Path(output)
    firmware = Path(firmware)
    if not firmware.is_file():
        raise ValueError(f'firmware image missing: {firmware}')
    output.mkdir(parents=True, exist_ok=True)
    dest = output / 'firmware.bin'
    if firmware.resolve() != dest.resolve():
        shutil.copyfile(firmware, dest)
    modules = []
    for name in plan['modules']:
        source = ROOT / 'c/k6/mod' / f'{name}.c'
        if not source.is_file():
            raise ValueError(f'module source missing: {source}')
        folder = output / 'modules' / name
        folder.mkdir(parents=True, exist_ok=True)
        if module_builder:
            module_builder(name, source, folder)
        else:
            k6_install.cmd_module_bundle(SimpleNamespace(source=str(source),
                id=f'org.blowfang.k6.{name}', level=None, output=str(folder)), log=lambda *_: None)
        manifest = json.loads((folder / 'manifest.json').read_text())
        module_file = folder / manifest['library'][0]['file']
        if manifest['library'][0]['level'] > 24:
            raise ValueError(f'module {name} requires unsupported API level {manifest["library"][0]["level"]}')
        modules.append({'name': name, 'id': manifest['library'][0]['id'],
                        'api_level': manifest['library'][0]['level'],
                        'path': module_file.relative_to(output).as_posix(), 'sha256': digest(module_file),
                        'manifest_sha256': digest(folder / 'manifest.json')})
    lock = {'format': 1, 'target': plan['target'], 'theme': plan.get('theme', DEFAULT_THEME),
            'tx_profile': plan.get('tx_profile', DEFAULT_TX_PROFILE), 'packs': plan['packs'],
            'cargo_features': plan['cargo_features'], 'capabilities': plan['capabilities'],
            'firmware': {'path': 'firmware.bin', 'sha256': digest(dest), 'bytes': dest.stat().st_size,
                         'capabilities_verified': verified_firmware},
            'modules': modules, 'install': {'automatic': False,
                'note': 'The guided flash-and-install action installs bundled modules, the named theme, and startup artwork after reboot when included. OTA packs require a key; cable-only packs use CRC32 integrity.'}}
    pages=([system_page] if system_page is not None else [])+list(system_pages)
    if packed or pages:
        lock['storage']={'format':2,'system_pages':[]}
    if pages:
        from k6_system_install import pack as pack_system
        pack_system(dest.read_bytes(),[Path(p).read_bytes() for p in pages],8192)
        for page in pages:
            page=Path(page);target=output/page.name
            if page.resolve()!=target.resolve():shutil.copyfile(page,target)
            lock['storage']['system_pages'].append({'path':target.name,'sha256':digest(target),'bytes':target.stat().st_size})
    if 'designer' in plan['packs']:
        from k6_theme_package import pack, display_name
        theme_path = output / 'theme.fluxtheme'
        shown_name=display_name(theme_name)
        theme_path.write_bytes(pack(plan.get('theme', DEFAULT_THEME), shown_name))
        lock['theme_package'] = {'path': theme_path.name, 'sha256': digest(theme_path),
                                 'bytes': theme_path.stat().st_size, 'name': shown_name,
                                 'install': 'guided flash-and-install places it in an automatically selected empty library slot; standalone install is also available'}
        splash_source = Path(os.environ.get('K6_FLUX_ART_DIR', ROOT / 'flux-art-kit/flux-arrays')) / 'splash.bin'
        splash_path = output / 'boot-splash.bin'
        artwork = splash_source.read_bytes()
        if len(artwork) != 1024:
            raise ValueError('full-screen boot artwork must be exactly 1024 bytes')
        splash_path.write_bytes(artwork)
        lock['boot_splash'] = {'path': splash_path.name, 'sha256': digest(splash_path),
                               'bytes': 1024, 'install': 'guided flash-and-install writes it with an existing bitmap backup; standalone install is also available'}
    (output / 'pack.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
    return lock


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('packs', nargs='+')
    p.add_argument('--output', type=Path, default=ROOT / 'firmware/k6-aura/out-fd6818b/packs')
    p.add_argument('--spec', type=Path, default=DEFAULT)
    p.add_argument('--skip-firmware-build', action='store_true', help='use an existing firmware image')
    p.add_argument('--firmware', type=Path, help='required with --skip-firmware-build')
    p.add_argument('--allow-unverified-firmware', action='store_true',
                   help='explicitly allow staging a prebuilt image without verifying capabilities')
    p.add_argument('--theme', type=validate_theme, default=DEFAULT_THEME, metavar="THEME")
    p.add_argument('--iq', action='store_true', help='experimental serial IQ (Data only)')
    p.add_argument('--theme-name', default='', help='optional display name carried by the theme package')
    p.add_argument('--tx-profile', type=validate_tx_profile, choices=TX_PROFILES, default=DEFAULT_TX_PROFILE)
    args = p.parse_args()
    try:
        spec = json.loads(args.spec.read_text())
        policy = json.loads(POLICY.read_text())
        validate(spec, policy)
        plan = resolve(spec, args.packs, args.theme, args.tx_profile)
        if args.skip_firmware_build:
            if args.iq:p.error('IQ requires a fresh build')
            if args.theme != DEFAULT_THEME:
                p.error('non-default themes require a fresh firmware build')
            if args.firmware is None:
                p.error('--firmware is required with --skip-firmware-build')
            if not args.allow_unverified_firmware:
                p.error('prebuilt firmware capability set is unverified; pass --allow-unverified-firmware to stage only')
            image = args.firmware
        else:
            env = os.environ.copy()
            env.pop('K6_MOD_KEY_FILE', None)
            env['K6_AURA_FEATURES'] = ','.join(plan['cargo_features'])
            env['K6_THEME'] = DEFAULT_THEME if 'designer' in plan['packs'] else plan['theme']
            env['K6_TX_PROFILE'] = plan['tx_profile']
            env.pop('K6_DESIGNER_TEST_SLOT', None)
            env['K6_IMAGE_NAME'] = 'flux-fd6818b-pack'
            env.update(gate_environment(plan, policy))
            env.update(storage_environment(plan,iq=args.iq))
            if "data" in plan["packs"]:
                env.update(K6_CONFIG_FSK="1", K6_LEAN_TXDOG="1", K6_NATIVE_FM="0",
                           K6_FONT_SUBSET="1", K6_RASTER_BOOT="1")
            with exclusive_build():
                subprocess.run(['sh', str(ROOT / 'firmware/k6-aura/build-fd6818b.sh')],
                               cwd=ROOT, env=env, check=True)
                image = ROOT / 'firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin'
                size_file = ROOT / 'firmware/k6-aura/out-fd6818b/size.txt'
                budget = assess(flash_bytes(size_file.read_text()), plan['packs'])
                if not budget['pass']:
                    raise ValueError(f'firmware exceeds {budget["policy"]}: {budget["used"]} > {budget["limit"]}')
                image_build_id(image.read_bytes())  # new linked builds must carry an ID
                lock = assemble(plan, args.output, image, verified_firmware=False, theme_name=args.theme_name, system_page=image.parent/'channel-system.bin' if env.get('K6_CHANNEL_PAGE')=='1' else None, packed=env.get('K6_USER_STORE')=='1',system_pages=[image.parent/'iq-system.bin'] if args.iq else [])
                if args.iq:lock['optional_services']=['serial-iq']
                lock['flash_budget'] = budget
                (args.output / 'pack.lock.json').write_text(json.dumps(lock, indent=2) + '\n')
                write_receipt(args.output / 'firmware.receipt.json', args.output / 'firmware.bin', plan, {**gate_environment(plan, policy),**storage_environment(plan,iq=args.iq)})
            image = None
        if args.skip_firmware_build:
            (args.output / 'firmware.receipt.json').unlink(missing_ok=True)
            lock = assemble(plan, args.output, image, verified_firmware=False, theme_name=args.theme_name)
        print(json.dumps({'lockfile': str(args.output / 'pack.lock.json'),
                          'firmware_bytes': lock['firmware']['bytes'],
                          'modules': [m['name'] for m in lock['modules']],
                          'automatic_install': False}, indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError, k6_install.InstallError) as exc:
        p.error(str(exc))


if __name__ == '__main__':
    main()
