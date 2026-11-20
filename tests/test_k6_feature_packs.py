import json
from pathlib import Path
import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_feature_packs import resolve

SPEC = json.loads((Path(__file__).resolve().parents[1] / 'config/k6-feature-packs.json').read_text())

def test_scan_data_deduplicates_base():
    plan = resolve(SPEC, ['scan', 'data'])
    assert plan['packs'] == ['base', 'scan', 'data']
    assert plan['modules'] == ['surf', 'explore_scan', 'cardlink']
    assert len(plan['cargo_features']) == len(set(plan['cargo_features']))

def test_unknown_and_unavailable_rejected():
    with pytest.raises(ValueError, match='unknown'):
        resolve(SPEC, ['missing'])

def test_sotto_pack_resolves_with_decoder_capability():
    from k6_pack_policy import POLICY, gate_environment, validate
    policy = json.loads(POLICY.read_text())
    validate(SPEC, policy)
    plan = resolve(SPEC, ['sotto'])
    assert plan['packs'] == ['base', 'sotto']
    assert 'sotto-decoder' in plan['capabilities']
    assert gate_environment(plan, policy)['K6_SOTTO_SERVICE'] == '1'
    assert gate_environment(resolve(SPEC, ['base']), policy)['K6_SOTTO_SERVICE'] == '0'

def test_cycles_rejected():
    spec = json.loads(json.dumps(SPEC))
    spec['packs']['base']['requires'] = ['scan']
    with pytest.raises(ValueError, match='cyclic'):
        resolve(spec, ['scan'])

def test_aprs_is_catalog_only_and_raw_data_is_optional():
    from k6_pack_policy import POLICY,gate_environment
    policy=json.loads(POLICY.read_text())
    base=resolve(SPEC,['base'])
    assert base['modules']==[]
    assert gate_environment(base,policy)['K6_FSK_RX_SERVICE']=='0'
    data=resolve(SPEC,['data'])
    assert data['modules']==['cardlink']
    assert gate_environment(data,policy)['K6_FSK_RX_SERVICE']=='1'
    with pytest.raises(ValueError,match='unknown'):
        resolve(SPEC,['aprs'])

def test_tx_profile_is_explicit_and_defaults_to_ce():
    from k6_feature_packs import TX_PROFILES
    assert resolve(SPEC, ['base'])['tx_profile'] == 'ce'
    for name, profile in TX_PROFILES.items():
        assert resolve(SPEC, ['base'], tx_profile=name)['tx_profile'] == name
    with pytest.raises(ValueError, match='transmit profile'):
        resolve(SPEC, ['base'], tx_profile='outside-range')

def test_storage_profile_defaults_are_explicit():
    from k6_pack_policy import storage_environment
    for packs in (['base'],['data'],['ota'],['sotto']):
        gates=storage_environment(resolve(SPEC,packs))
        assert gates['K6_USER_STORE']==gates['K6_MEMORY_BANK']=='1'
        assert gates['K6_SERIAL_IQ']==gates['K6_IQ_PAGE']=='0' # unqualified experimental overlay

def test_iq_opt_in_data_only():
    from k6_pack_policy import storage_environment
    with pytest.raises(ValueError):storage_environment(resolve(SPEC,['base']),iq=True)
    with pytest.raises(ValueError):storage_environment(resolve(SPEC,['data','ota']),iq=True)
    gates=storage_environment(resolve(SPEC,['data']),iq=True)
    assert gates['K6_MEMORY_BANK']==gates['K6_SERIAL_IQ']==gates['K6_IQ_PAGE']=='1'
