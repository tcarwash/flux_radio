#!/usr/bin/env python3
"""Validate explicit pack ownership of optional firmware capabilities and build gates."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / 'config/k6-service-gates.json'


def validate(spec, gates):
    packs = spec['packs']
    caps = {c for pack in packs.values() for c in pack.get('capabilities', [])}
    owned = set()
    for name, gate in gates['services'].items():
        if gate['pack'] not in packs:
            raise ValueError(f'{name}: unknown owning pack')
        if gate['capability'] not in packs[gate['pack']]['capabilities']:
            raise ValueError(f'{name}: capability not owned by declared pack')
        if gate['capability'] in owned:
            raise ValueError(f'{name}: capability has duplicate gate ownership')
        owned.add(gate['capability'])
        if not gate['environment'] or gate['disabled'] == gate['enabled']:
            raise ValueError(f'{name}: invalid build gate')
    ungated = caps - owned - set(gates.get('always_on_capabilities', []))
    if ungated:
        raise ValueError(f'capabilities missing explicit service gate or always-on declaration: {sorted(ungated)}')
    return True


def gate_environment(plan, gates):
    return {gate['environment']: gate['enabled'] if gate['pack'] in plan['packs'] else gate['disabled']
            for gate in gates['services'].values()}


def main():
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--spec', type=Path, default=ROOT / 'config/k6-feature-packs.json')
    p.add_argument('--policy', type=Path, default=POLICY)
    args = p.parse_args()
    validate(json.loads(args.spec.read_text()), json.loads(args.policy.read_text()))
    print('Feature capability ownership policy: OK')


if __name__ == '__main__':
    main()


def storage_environment(plan):
    """All new catalog builds use KUL2. External channel code is a separate gate."""
    return {'K6_USER_STORE':'1','K6_SYSTEM_INSTALL':'0','K6_CHANNEL_PAGE':'0'}
