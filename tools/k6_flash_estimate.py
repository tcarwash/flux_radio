"""Approximate feature-pack flash usage for the catalog planner."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ESTIMATES = ROOT / 'config/k6-flash-estimates.json'
TOTAL = 122_880
BASE_DESIGN_GOAL = 98_304  # Informational target only; the 120 KiB limit controls eligibility


def estimate(plan):
    data = json.loads(ESTIMATES.read_text())
    packs = list(plan['packs'])
    used = data['base_bytes']
    rows = [{'pack': 'base', 'bytes': used, 'source': data['base_source']}]
    unknown = []
    for pack in packs:
        if pack == 'base':
            continue
        item = data['deltas'].get(pack)
        if item is None:
            unknown.append(pack)
            continue
        used += item['bytes']
        rows.append({'pack': pack, **item})
    if plan.get('iq') and set(packs) in ({'base'}, {'base','scan','designer','data'}):
        used=97372 if len(packs)==1 else 120180
        rows=[{'pack': 'linked IQ profile', 'bytes': used, 'source': '2026-11-19 all-lock linked build; other TX profiles may vary'}]
    base_only = set(packs) == {'base'}
    base_target_over = base_only and used > BASE_DESIGN_GOAL
    limit = TOTAL
    return {
        'used': used,
        'limit': limit,
        'total_limit': TOTAL,
        'base_design_goal_bytes': BASE_DESIGN_GOAL,
        'remaining': limit - used,
        'percent': min(100, round(used * 100 / TOTAL)),
        'allowed': used <= limit and not unknown,
        'base_target_over': base_target_over,
        'base_target_over_by': max(0, used - BASE_DESIGN_GOAL) if base_only else 0,
        'components': rows,
        'unknown_packs': unknown,
        'note': data['note'],
    }
