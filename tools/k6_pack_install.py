#!/usr/bin/env python3
"""Verify, flash, and install Flux feature packs."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from k6_feature_packs import DEFAULT, resolve
from k6_build_identity import image_build_id


class PackError(ValueError):
    pass


def checked_file(root, name, sha):
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise PackError('unsafe artifact path')
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise PackError(f'missing or unsafe artifact: {name}')
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != sha:
        raise PackError(f'hash mismatch: {name}')
    return path


def verify_pack(root, spec, *, require_attested=True):
    root = Path(root).resolve()
    lock = json.loads((root / 'pack.lock.json').read_text())
    if lock.get('format') != 1 or lock.get('target') != spec['target']:
        raise PackError('unsupported lockfile or radio target')
    plan = resolve(spec, lock['packs'], lock.get('theme', 'flux'), lock.get('tx_profile', 'ce'))
    for key in ('packs', 'capabilities', 'modules', 'cargo_features', 'tx_profile'):
        if key == 'modules':
            actual = [m['name'] for m in lock['modules']]
        else:
            actual = lock[key]
        if actual != plan[key]:
            raise PackError(f'lockfile {key} does not match resolved dependencies')
    firmware = lock['firmware']
    fw = checked_file(root, firmware['path'], firmware['sha256'])
    if fw.stat().st_size != firmware['bytes']:
        raise PackError('firmware byte count mismatch')
    storage=lock.get('storage')
    if storage is not None:
        from k6_system_install import pack as pack_system
        pages=storage.get('system_pages',[])
        if storage.get('format')!=2 or not isinstance(pages,list) or (pages and 'ota' in lock['packs']):
            raise PackError('unsupported packed storage plan')
        images=[]
        for page in pages:
            path=checked_file(root,page['path'],page['sha256']);data=path.read_bytes()
            if len(data)!=page['bytes']:raise PackError('system page size mismatch')
            images.append(data)
        if images:pack_system(fw.read_bytes(),images,8192)
    splash = lock.get('boot_splash')
    if splash is not None:
        splash_path = checked_file(root, splash['path'], splash['sha256'])
        if splash_path.stat().st_size != splash['bytes'] or splash_path.stat().st_size != 1024:
            raise PackError('boot splash byte count mismatch')
    theme = lock.get('theme_package')
    if theme is not None:
        theme_path = checked_file(root, theme['path'], theme['sha256'])
        if theme_path.stat().st_size != theme['bytes']:
            raise PackError('theme package byte count mismatch')
        from k6_theme_package import unpack
        try:
            unpack(theme_path.read_bytes(), include_name=True)
        except ValueError as exc:
            raise PackError(f'invalid theme package: {exc}') from exc
    if require_attested:
        # A mutable lockfile boolean is not proof of a radio/firmware ABI match.
        # Keep all radio-writing paths disabled until an independent verifier exists.
        raise PackError('firmware capability attestation is not implemented; refusing installation')
    for mod in lock['modules']:
        if mod['id'] != f'org.blowfang.k6.{mod["name"]}':
            raise PackError(f'module identity mismatch: {mod["name"]}')
        module_path = checked_file(root, mod['path'], mod['sha256'])
        manifest_path = module_path.parent / 'manifest.json'
        checked_file(root, str(manifest_path.relative_to(root)), mod['manifest_sha256'])
        manifest = json.loads(manifest_path.read_text())
        if len(manifest.get('library', [])) != 1:
            raise PackError(f'invalid module manifest: {mod["name"]}')
        entry = manifest['library'][0]
        if entry['id'] != mod['id'] or entry['level'] != mod['api_level'] or entry['file'] != module_path.name:
            raise PackError(f'module manifest differs from lockfile: {mod["name"]}')
        hashes = manifest.get('sha256')
        if hashes is not None:
            if not isinstance(hashes, dict) or hashes.get(module_path.name) != mod['sha256']:
                raise PackError(f'module manifest hash differs from lockfile: {mod["name"]}')
        if manifest.get('build_id', '0000000000000000') != '0000000000000000':
            raise PackError(f'module bundle unexpectedly bound to firmware build: {mod["name"]}')
        if not 1 <= mod['api_level'] <= 24:
            raise PackError(f'unsupported module API level: {mod["name"]}')
    return lock



CAPABILITY_FLAGS = {
    'module-loader': 'module_loader',
    'explore-scanner-api': 'scanner',
    'explore-engine-api': 'scanner',
    'explore-ui-api': 'scanner',
    'candidate-ranking': 'scanner',
    'cw-tone-observation': 'scanner',
    'fd6818-fsk-rx': 'fsk_rx',
    'authenticated-ota': 'ota',
    'theme-renderer': 'designer',
    'sotto-decoder': 'sotto',
}


def compatibility_report(lock, capabilities, expected_build_id=None):
    """Compare a verified artifact plan with an unauthenticated radio report."""
    missing = [name for name in lock['capabilities']
               if capabilities.get(CAPABILITY_FLAGS.get(name, ''), False) is not True]
    ceiling = capabilities.get('module_api_max', 0)
    if type(ceiling) is not int or not 1 <= ceiling <= 255:
        raise PackError('invalid reported module API ceiling')
    unsupported = [m['name'] for m in lock['modules'] if m['api_level'] > ceiling]
    reported_build_id = capabilities.get('build_id')
    identity_match = bool(expected_build_id and reported_build_id == expected_build_id)
    return {'expected_build_id': expected_build_id, 'reported_build_id': reported_build_id,
            'build_id_match': identity_match, 'capabilities_match': not missing and not unsupported,
            'missing_capabilities': missing, 'unsupported_modules': unsupported,
            'reported_api_max': ceiling, 'authenticated': False,
            'build_identity_verified': False, 'install_ready': False}


def radio_preflight(root, spec, port, key_file=None):
    from k6_install import open_port, open_cable, InstallError
    lock = verify_pack(root, spec, require_attested=False)
    firmware = Path(root) / lock['firmware']['path']
    try:
        expected_build_id = image_build_id(firmware.read_bytes())
    except ValueError:
        expected_build_id = None  # legacy artifact; cannot match a build ID
    if key_file is not None:
        from modem import k6mod
        key = k6mod.read_key(str(key_file))
        if key == k6mod.PLACEHOLDER:
            raise PackError('placeholder is not a provisioned module key')
    else:
        key = None
    with pack_port(port):
        connection = open_port(SimpleNamespace(port=port))
        cable = None
        try:
            cable = open_cable(connection, lambda *_: None, wait=3.0)
            if cable is None:
                raise PackError('no Flux module-host cable session')
            caps = cable.capabilities()
            proven = key is not None and not caps.get('cable_only')
            if proven:
                caps = cable.authenticated_capabilities(key)
            report = compatibility_report(lock, caps, expected_build_id)
            report['key_possession_verified'] = proven
            report['capability_report_authenticated'] = proven
            # A holder of the shared key can impersonate firmware; no secure boot or
            # hardware measurement is available. Never enable installation here.
            report['packs'] = lock['packs']
            return report
        except InstallError as exc:
            raise PackError(str(exc)) from exc
        finally:
            try:
                if cable is not None:
                    cable.end()
            finally:
                connection.close()


@contextmanager
def pack_port(port):
    """Serialize pack operations from the CLI and catalog on the same device."""
    from k6_port_lock import serial_lock
    from contextlib import ExitStack
    with ExitStack() as stack:
        try:
            stack.enter_context(serial_lock(port))
        except BlockingIOError as exc:
            raise PackError(str(exc)) from exc
        yield


def provision_artifacts(root, spec, key_file):
    from k6_install import find_key, k6mod
    from k6_capability_receipt import verify
    from k6_pack_policy import POLICY, gate_environment, storage_environment, validate

    lock = verify_pack(root, spec, require_attested=False)
    firmware = Path(root) / lock['firmware']['path']
    plan = resolve(spec, lock['packs'], lock.get('theme', 'flux'), lock.get('tx_profile', 'ce'))
    policy = json.loads(POLICY.read_text())
    validate(spec, policy)
    receipt = json.loads((Path(root) / 'firmware.receipt.json').read_text())
    verify(receipt, firmware, plan, {**gate_environment(plan, policy),**(storage_environment(plan) if lock.get('storage') else {})})
    data = firmware.read_bytes()
    if hashlib.sha256(data).hexdigest() != lock['firmware']['sha256']:
        raise PackError('firmware changed during verification')
    identity = image_build_id(data)
    ota = 'ota' in lock['packs']
    if data.count(k6mod.PLACEHOLDER) != int(ota):
        raise PackError('firmware key placeholder disagrees with selected OTA mode')
    key = k6mod.read_key(str(key_file) if key_file else find_key()) if ota else None
    if key == k6mod.PLACEHOLDER:
        raise PackError('the module key is a placeholder')
    return lock, data, identity, key


def provision_pack(root, spec, port, key_file=None, *, action, mode='cable', log=print, recovery=None):
    import k6_install as radio

    if mode not in ('cable', 'update'):
        raise PackError(f'unknown pack flash mode: {mode}')
    if action not in ('flash', 'install', 'preview', 'flash-install'):
        raise PackError(f'unknown pack operation: {action}')
    lock, firmware, identity, key = provision_artifacts(root, spec, key_file)
    packed=lock.get('storage',{}).get('format')==2
    is_flash=action in ('flash','flash-install')
    firmware_only=action=='flash'
    if recovery is not None and (not packed or not is_flash):raise PackError('recovery requires a packed flash-install')
    if packed and is_flash:action='flash-install'
    prepared=None
    backup_dir=Path.home()/'.local/share/flux/backups'
    if packed:
        from k6_user_store_restore import prepare,restore,complete,load_recovery
        from k6_system_install import install as install_system
        pages=[checked_file(Path(root),p['path'],p['sha256']).read_bytes() for p in lock['storage']['system_pages']]

    if action in ('flash', 'flash-install'):
        from k6_flash_policy import assess
        if not assess(len(firmware), lock['packs'])['pass']:
            raise PackError('firmware binary exceeds its profile flash budget')
        if firmware.count(radio.k6mod.PLACEHOLDER) != int(key is not None):
            raise PackError('firmware placeholder count disagrees with requested authentication mode')
    entries = []
    if action != 'flash' and not firmware_only:
        for module in lock['modules']:
            path = Path(root) / module['path']
            code = path.read_bytes()
            if hashlib.sha256(code).hexdigest() != module['sha256']:
                raise PackError(f"module changed during verification: {module['name']}")
            radio.k6mod.bin_entry(code)
            entries.append({'path': path, 'code': code, 'id': module['id'], 'name': module['name'],
                            'kind': 'module', 'level': module['api_level']})

    def prepare_upgrade(cable):
        if recovery is not None:return load_recovery(recovery,cable,identity,port),Path(recovery)
        fingerprint=hashlib.sha256(cable.read(0xf000,4096)).hexdigest()
        for candidate in backup_dir.glob('flux-library-*.json'):
            rec=json.loads(candidate.read_text())
            if rec.get('status')=='pending' and rec.get('calibration_sha256')==fingerprint:
                raise PackError(f'pending upgrade: resume with --recover {candidate}')
        return prepare(cable,backup_dir,identity,port,log)

    with pack_port(port):
        connection = radio.open_port(SimpleNamespace(port=port))
        cable = None
        try:
            if action in ('flash', 'flash-install') and mode == 'update':
                probe = radio.open_cable(connection, log, wait=3.0)
                actual_mode = 'update'
                if probe is not None:
                    # The radio answered a cable session, so it is not in the
                    # bootloader: authenticate it like cable mode instead of
                    # blind-flashing. Legacy firmware and failed proofs refuse
                    # here with no fallback; a true UPDATE bootloader answers
                    # no cable session and proceeds below.
                    try:
                        probe_caps = probe.capabilities()
                        if not probe_caps.get('cable_only'):
                            current = key if key is not None else radio.k6mod.read_key(
                                str(key_file) if key_file else radio.find_key())
                            probe_caps = probe.authenticated_capabilities(current)
                        if bool(probe_caps.get('cable_only')) != (key is None):
                            raise radio.InstallError(
                                'connected radio authentication mode does not match pack')
                    except radio.InstallError as exc:
                        probe.end()
                        raise PackError(str(exc)) from exc
                    if packed:prepared,journal=prepare_upgrade(probe)
                    actual_mode = 'cable'
                elif packed:
                    raise PackError('packed updates require a running Flux session for pre-flash backup; use cable mode')
                radio.flash(connection, firmware, key, actual_mode, log=log)
                if action == 'flash':
                    return
                log('  waiting for the flashed firmware to start before updating its modules...')
                cable = radio.open_cable(connection, log, wait=30.0)
            else:
                cable = radio.open_cable(connection, log, wait=3.0)
                if cable is None:
                    raise PackError('no Flux module-host cable session; enter UPDATE mode for a first flash')
                caps = cable.capabilities()
                if not caps.get('cable_only'):
                    # Authenticate the *current* signed radio, including a migration
                    # to the new cable-only build. Never fall back on proof failure.
                    current_key = key if key is not None else radio.k6mod.read_key(
                        str(key_file) if key_file else radio.find_key())
                    caps = cable.authenticated_capabilities(current_key)
                if action in ('flash', 'flash-install'):
                    if packed:prepared,journal=prepare_upgrade(cable)
                    cable = None  # flash owns the restart and bootloader handshake
                    radio.flash(connection, firmware, key, mode, log=log)
                    if action == 'flash':
                        return
                    log('  waiting for the flashed firmware to start before updating its modules...')
                    cable = radio.open_cable(connection, log, wait=30.0)
            if cable is None:
                raise PackError('firmware flashed, but the module host did not return; complete any first-boot format prompt, then use Install modules on this build')
            caps = cable.capabilities()
            if not caps.get('cable_only'):
                caps = cable.authenticated_capabilities(key)
            if bool(caps.get('cable_only')) != (key is None):
                raise PackError('connected radio authentication mode does not match pack')
            report = compatibility_report(lock, caps, identity)
            if not report['build_id_match'] or not report['capabilities_match']:
                raise PackError(f'connected firmware does not match the pack: {json.dumps(report)}')

            if packed and is_flash:
                if prepared is None:raise PackError('missing pre-flash user library backup')
                restore(cable,bytes.fromhex(identity),prepared,log,key=key)
                if pages:
                    install_system(cable,firmware,pages,log=log)
                    cable=radio.open_cable(connection,log,wait=30.0)
                    if cable is None:raise PackError('system pages installed but module session did not return')
                    if cable.capabilities().get('build_id')!=identity:raise PackError('firmware identity changed after system provisioning')
                complete(journal)
            if firmware_only:return
            serials = radio.Serials()
            if cable.info()['protocol'] >= 3:
                try:
                    serials.advance_after(cable.serial_watermark())
                except radio.InstallError as exc:
                    if 'refused command 0x4a at 0: error 03' not in str(exc):
                        raise
            if key is None:
                radio.check_key(cable, None, serials, log)
            allocated = radio.allocate_slots(cable, entries, cable.info(), key)
            for entry in allocated:
                operation = 'existing' if entry.get('_update') else 'available'
                log(f"  {entry['id']} → slot {entry['slot']} ({operation})")
            if action in ('install', 'flash-install'):
                if cable.info()['protocol'] < 2 and any(e.get('_update') for e in allocated):
                    raise PackError('radio cannot safely remove existing modules; update its cable protocol first')
                for entry in allocated:
                    if entry.get('_update'):
                        expected = radio.sign(entry, 0, key)
                        installed = radio.library_read(cable,entry['slot'],len(expected))
                        if installed[:8] == expected[:8] and installed[12:-8] == expected[12:-8]:
                            log(f"  {entry['id']} in slot {entry['slot']} is already current")
                            continue
                        log(f"  replacing {entry['id']} in slot {entry['slot']}: erasing old module...")
                        if cable.info()['protocol'] < 5:
                            cable.remove_slot(entry['slot'], key, serials.next())
                radio.write_entries(cable, allocated, key, serials, log=log,
                                    verify=True, skip_current=True)
                log('  bundled modules installed and read back successfully')
            if action == 'flash-install' and lock.get('theme_package'):
                theme = lock['theme_package']
                theme_path = checked_file(Path(root), theme['path'], theme['sha256'])
                from k6_theme_package import pack, unpack
                theme_token, theme_name = unpack(theme_path.read_bytes(), include_name=True)
                theme_blob = pack(theme_token, theme_name)
                if cable.info()['protocol'] < 3:
                    raise PackError('Designer typed theme protocol 3 is required')
                theme_slot = radio.find_empty_theme_slot(cable)
                address = radio.LIB_BASE + theme_slot * radio.LIB_SLOT
                if radio.library_read(cable,theme_slot,radio.LIB_SLOT) != b'\xff' * radio.LIB_SLOT:
                    raise PackError('selected theme slot is no longer empty')
                theme_serial = serials.next()
                theme_image = radio.k6mod.image(theme_blob, 0, theme_serial, key,
                                                lib=theme_slot, theme=True)
                result_code, _have, _count, written = cable.send_image(theme_image, theme_serial & 255)
                if (result_code != 3 or written != len(theme_blob) or
                        radio.library_read(cable,theme_slot,len(theme_blob)) != theme_blob):
                    raise PackError(f'theme install/readback failed: {result_code}, {written}')
                if theme_name:
                    log(f"  theme verified in slot {theme_slot}: {theme_name!r} is registered in DISP > THEME")
                else:
                    log(f'  theme verified in slot {theme_slot}; registered in DISP > THEME')
        except radio.InstallError as exc:
            raise PackError(str(exc)) from exc
        finally:
            try:
                if cable is not None:
                    cable.end()
            finally:
                connection.close()
        if action == 'flash-install' and lock.get('boot_splash'):
            splash = lock['boot_splash']
            splash_path = checked_file(Path(root), splash['path'], splash['sha256'])
            radio.install_boot_logo(port, str(splash_path), yes=True, log=log)
        if action == 'flash-install':
            log('  firmware, bundled modules, and all included theme/startup artwork installed and verified')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pack', type=Path)
    parser.add_argument('--spec', type=Path, default=DEFAULT)
    parser.add_argument('--inspect-unverified', action='store_true', help='inspect only; never install')
    parser.add_argument('--radio-preflight', action='store_true', help='query compatibility only; no flash writes (ends cable session with restart)')
    parser.add_argument('--port', help='explicit radio serial port')
    parser.add_argument('--key-file', type=Path, help='explicit existing key for fresh read-only capability challenge')
    parser.add_argument('--recover',type=Path,help='resume a pending packed upgrade using its recovery journal (writes radio)')
    args = parser.parse_args()
    if args.radio_preflight and (not args.port or args.inspect_unverified):
        parser.error('--radio-preflight requires --port and cannot be combined with --inspect-unverified')
    if args.recover and (not args.port or args.radio_preflight or args.inspect_unverified):
        parser.error('--recover requires --port and cannot be combined with inspection')
    if args.key_file and not (args.radio_preflight or args.recover):
        parser.error('--key-file requires --radio-preflight')
    if args.port and not (args.radio_preflight or args.recover):
        parser.error('--port requires --radio-preflight')
    try:
        spec = json.loads(args.spec.read_text())
        if args.recover:
            provision_pack(args.pack,spec,args.port,args.key_file,action='flash-install',recovery=args.recover)
            return 0
        if args.radio_preflight:
            report = radio_preflight(args.pack, spec, args.port, args.key_file)
            print(json.dumps(report, indent=2))
            return 0 if report['capabilities_match'] and report['build_id_match'] else 1
        lock = verify_pack(args.pack, spec, require_attested=not args.inspect_unverified)
        print(json.dumps({'status': 'verified artifacts' if args.inspect_unverified else 'verified lockfile',
                          'install_ready': not args.inspect_unverified,
                          'packs': lock['packs'], 'modules': [m['name'] for m in lock['modules']],
                          'firmware': lock['firmware']['path']}, indent=2))
        print('No radio writes performed. Use k6.py flash-pack or install-pack to provision a radio.')
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    sys.exit(main())
