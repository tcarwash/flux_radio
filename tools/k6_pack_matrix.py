#!/usr/bin/env python3
"""Build and budget-check purpose-built K6 profiles, never flash a radio."""
import argparse
import itertools
import json
from pathlib import Path
import subprocess
import os
import shutil
from k6_pack_policy import POLICY, validate, gate_environment, storage_environment
from k6_build_lock import exclusive_build
from k6_capability_receipt import write as write_receipt
from k6_build_identity import image_build_id
import sys

ROOT = Path(__file__).resolve().parents[1]
from k6_feature_packs import DEFAULT, resolve
from k6_flash_policy import assess, flash_bytes


def profiles(spec, features):
    """Base, individual optional packs, and every available combination."""
    available = [p for p in features if not spec['packs'][p].get('status', '').startswith('planned')]
    for count in range(len(available) + 1):
        for subset in itertools.combinations(available, count):
            yield ('base',) + subset, resolve(spec, subset or ('base',))


def report(profile_sizes, *, required=('scan', 'data')):
    rows = []
    for names, used in profile_sizes:
        packs = list(names)
        result = assess(used, packs)
        rows.append({'packs': packs, 'bytes': used, 'remaining': result['remaining'],
                     'limit': result['limit'], 'pass': result['pass']})
    combo = next((r for r in rows if set(r['packs']) == {'base', *required}), None)
    return {'profiles': rows, 'all_fit': all(r['pass'] for r in rows),
            'required_combo': list(required),
            'required_combo_tested': combo is not None,
            'required_combo_fits': bool(combo and combo['pass'])}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, default=DEFAULT)
    p.add_argument('--features', nargs='+', default=['scan', 'data'])
    p.add_argument('--size-dir', type=Path, help='read PROFILE/size.txt from this directory instead of building')
    p.add_argument('--build', action='store_true', help='compile all profiles sequentially; no radio writes')
    p.add_argument('--build-dir', type=Path, default=ROOT / 'firmware/k6-aura/out-fd6818b/profile-matrix')
    p.add_argument('--output', type=Path, help='write JSON report')
    args = p.parse_args()
    spec = json.loads(args.spec.read_text())
    policy = json.loads(POLICY.read_text())
    validate(spec, policy)
    if args.build and args.size_dir:
        p.error('--build and --size-dir are mutually exclusive')
    results = []
    for names, plan in profiles(spec, args.features):
        label = '-'.join(names)
        if args.build:
            env = os.environ.copy()
            env['K6_AURA_FEATURES'] = ','.join(plan['cargo_features'])
            env['K6_THEME'] = plan['theme']
            env['K6_IMAGE_NAME'] = f'flux-matrix-{label}'
            env.update(gate_environment(plan, policy))
            env.update(storage_environment(plan))
            print(f'Building {label}: {gate_environment(plan, policy)}', file=sys.stderr, flush=True)
            with exclusive_build():
                subprocess.run(['sh', str(ROOT / 'firmware/k6-aura/build-fd6818b.sh')],
                               cwd=ROOT, env=env, check=True)
                source = ROOT / 'firmware/k6-aura/out-fd6818b'
                dest = args.build_dir / label
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / 'size.txt', dest / 'size.txt')
                shutil.copy2(source / f'flux-matrix-{label}.bin', dest / 'firmware.bin')
                image_build_id((dest / 'firmware.bin').read_bytes())
            (dest / 'plan.json').write_text(json.dumps(plan, indent=2) + '\n')
            write_receipt(dest / 'firmware.receipt.json', dest / 'firmware.bin', plan, {**gate_environment(plan, policy),**storage_environment(plan)})
            size = dest / 'size.txt'
        elif args.size_dir:
            size = args.size_dir / label / 'size.txt'
        else:
            p.error('pass --build to compile profiles or --size-dir to inspect existing results')
        if not size.is_file():
            p.error(f'missing linked size for {label}: {size}')
        results.append((names, flash_bytes(size.read_text())))
    result = report(results)
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['all_fit'] and result['required_combo_fits'] else 1


if __name__ == '__main__':
    sys.exit(main())
