"""No-hardware path enumerator, compatibility and SoapyFang reference tests."""
import json
import numpy as np
import pytest

from sim.modulation.tx_paths import (
    ENVELOPES, ACTUATORS, SAMPLE_RATE, all_configurations,compatibility,
    envelope_response,synthesize,deviation_components,
)
from tools.k6_tx_path_lab import fixture,metrics,normalized_error,matrix_rows,main,sample_one


def test_complete_bounded_cross_product_with_fail_closed_conflicts():
    n=len(ACTUATORS)
    assert n==11
    expected= (n + n*(n-1)//2 + n*(n-1)*(n-2)//6)*len(ENVELOPES)
    configs=all_configurations()
    assert len(configs)==expected==2310
    assert len({(tuple(x['actors']),x['envelope']) for x in configs})==len(configs)
    assert all(x['hardware_qualified'] is False for x in configs)
    assert any(x['compatibility']=='conflict' for x in configs)
    assert any(x['compatibility']=='unverified_combination' for x in configs)
    assert all_configurations(1)[0]['envelope'] in ENVELOPES
    with pytest.raises(ValueError):all_configurations(4)


@pytest.mark.parametrize('combo',[
    ('native_fsk','dual_tone'),('native_fsk','tone'),('subaudio','signed_dac'),
    ('subaudio','gp0'),('subaudio','dcs_bits'),('signed_dac','dcs_bits'),
    ('gp0','dcs_bits'),('ctcss_phase','dcs_bits'),
    ('reg40',),('tone','reg40'),('ctcss_phase','subaudio'),
])
def test_register_mux_conflicts_never_synthesized(combo):
    assert compatibility(combo)[0]=='conflict'
    signal,_=fixture('qpsk')
    with pytest.raises(ValueError):synthesize(signal,combo,'constant_pa')


def test_evidence_distinguishes_ideal_physics_and_hypotheses():
    assert compatibility(('ideal',))[0]=='reference_only'
    assert compatibility(('native_fsk',))[0]=='observed_individual'
    assert compatibility(('ctcss_phase',))[0]=='unverified_hardware'
    assert compatibility(('native_fsk','subaudio'))[0]=='unverified_combination'
    assert compatibility(('ideal','tone'))[0]=='conflict'


def test_identical_ideal_iq_reference_for_all_five_mode_families():
    for mode in ('cw','am','ssb','ssb-lsb','qpsk','ofdm'):
        source,_=fixture(mode)
        y=synthesize(source,('ideal',),'ideal')
        assert np.allclose(y,source,atol=1e-5)
        assert normalized_error(source,y)<.001
        assert not np.isnan(metrics(source,y)['papr_db'])


def test_cw_measured_asymmetric_edges_and_driver_gate():
    n=9600; on=np.zeros(n);on[1500:6500]=1
    measured=envelope_response(on,'measured_gate')
    assert measured[1500] < measured[1500+240]
    assert measured[6500] < measured[6500-36]
    assert (measured >=0).all() and (measured<=1).all()
    exact=envelope_response(on,'hard_gate')
    assert exact[1500]==1 and exact[6500]==0
    constrained=envelope_response(on,'driver6_gate')
    assert constrained[1200]==0 and constrained[6700]==0
    assert constrained[3000]>.99


def test_quantized_paths_do_not_claim_ideal_phase_or_amplitude():
    source,_=fixture('qpsk')
    ideal=synthesize(source,('ideal',),'constant_pa')
    fsk=synthesize(source,('native_fsk',),'constant_pa')
    assert np.allclose(abs(ideal),1,atol=1e-5)
    assert np.allclose(abs(fsk),1,atol=1e-5)
    assert normalized_error(source,fsk)>30
    assert len(fsk)==len(source)
    assert np.array_equal(fsk,synthesize(source,('native_fsk',),'constant_pa'))


def test_observed_signed_subaudio_dcs_aliased_registers_prohibited():
    signal,_=fixture('ssb')
    assert np.all(np.isfinite(deviation_components(signal,('dcs_bits',))))
    assert np.all(np.isfinite(deviation_components(signal,('ctcss_phase',))))
    with pytest.raises(ValueError):deviation_components(signal,('reg40',))


def test_offline_soapyfang_receiver_for_ideal_and_impaired_paths():
    baseline=sample_one('qpsk',('ideal',),'ideal')
    fsk=sample_one('qpsk',('native_fsk',),'constant_pa')
    assert baseline['receiver']['pass']
    assert baseline['receiver']['bit_error_rate']==0
    assert not fsk['receiver']['pass']
    assert not baseline['receiver']['physical_tx']
    assert baseline['receiver']['tx_model']=='offline_path_model'
    cw=sample_one('cw',('carrier',),'driver6_gate')
    assert cw['receiver']['pass']
    assert cw['receiver']['max_envelope_error']<.2


def test_short_full_model_matrix_preserves_conflicts_and_not_real_rf():
    rows=matrix_rows(['am'],max_actors=1)
    assert len(rows)==len(ACTUATORS)*len(ENVELOPES)
    assert all(r['hardware_qualified'] is False for r in rows)
    assert sum(x['status']=='conflict' for x in rows)>=10
    simulated=[x for x in rows if x['status']=='simulated']
    assert len(simulated)>50
    assert all(np.isfinite(x['waveform_error_pct']) for x in simulated)


def test_cli_does_not_try_hardware_and_writes_results(tmp_path):
    path=tmp_path/'matrix.json'
    assert main(['--modes','cw,qpsk','--max-actors','1','--skip-soapy',
                 '--json',str(path)])==0
    result=json.loads(path.read_text())
    assert result['physical_rf'] is False
    assert result['hardware_qualification'] is False
    assert result['config_count']==len(ACTUATORS)*len(ENVELOPES)
    assert all(x['status'] in ('simulated','blocked') for x in result['sampled_replays'])
    with pytest.raises(SystemExit):main(['--modes','bogus','--json',str(path)])
    with pytest.raises(SystemExit):main(['--modes','cw,cw','--json',str(path)])


def test_spi_update_budget_limited_and_hypothetical_fast_spi():
    from sim.modulation.tx_paths import spi_sample_hold
    signal=np.linspace(0,1,240)
    stock=spi_sample_hold(signal,write_us=117)
    fast=spi_sample_hold(signal,write_us=5)
    assert np.count_nonzero(np.diff(stock))<np.count_nonzero(np.diff(fast))
    assert stock.shape==fast.shape==signal.shape
    assert np.allclose(fast,signal)
    with pytest.raises(ValueError):spi_sample_hold(signal,write_us=float('nan'))
    with pytest.raises(ValueError):spi_sample_hold(signal,write_us=-1)
    with pytest.raises(ValueError):spi_sample_hold(signal,write_us=5,writes=0)


def test_path_simulation_is_spi_latency_sensitive_on_amplitude():
    source,_=fixture('am')
    baseline=synthesize(source,('carrier',),'driver6',spi_write_us=117)
    fast=synthesize(source,('carrier',),'driver6',spi_write_us=5)
    assert not np.array_equal(baseline,fast)
    assert normalized_error(source,baseline)>=0
    assert normalized_error(source,fast)>=0
