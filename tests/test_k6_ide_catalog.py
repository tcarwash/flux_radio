"""Workbench module picker should not bury supported modules in debug builds."""
from sim.k6ide import module_bundle_catalog


def test_catalog_curates_current_modules_and_keeps_experiments_accessible():
    curated = module_bundle_catalog()
    all_bundles = module_bundle_catalog(include_experimental=True)
    assert len(curated) <= len(all_bundles)
    assert {b['path'] for b in curated} <= {b['path'] for b in all_bundles}
    assert len({b['path'] for b in curated}) == len(curated)
    assert all(b['path'].split('/')[-1] in
               {'surf', 'cardlink', 'explore_scan', 'aprs', 'aprs-beacon', 'aprs-gps-sync', 'aprs-kiss-rx'} for b in curated)
