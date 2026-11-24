"""TX-34 independent model and SoapyFang received-IQ qualification."""
import json

import numpy as np
import pytest
from tools.k6_tx34_step_response import PATHS,generate,main,run,pattern,score


@pytest.mark.parametrize('path',PATHS)
def test_receive_only_soapyfang_path_and_heldout_pilot_separation(path,tmp_path):
    r=run(path,temp_dir=tmp_path)
    assert r['offline_model_pass'],r
    assert r['heldout_ber']==0
    assert r['pilot_ber']==0
    assert r['eye_opening_sigma']>2
    assert r['physical_gate'] in ('NOT_AUTHORIZED','DEFERRED_DEPENDENCY')
    assert r['rf_model']=='SoapyFang SigmfSource -> RFBackend -> ChipModel'
    assert r['provenance']=='SYNTHETIC_NOT_BENCH' and r['receive_only']
    assert not r['physical_tx']
    assert r['rx_group_delay_us']>=0
    assert r['median_transition_hz']>300
    assert 0<=r["max_filtered_phase_increment_rad"]<=np.pi


def test_independent_pilot_is_not_fit_to_heldout(tmp_path):
    expected=run('native_fsk_4800',temp_dir=tmp_path)
    assert expected['heldout_ber']==0 and expected['pilot_ber']==0
    inverted=run('native_fsk_4800',control='reversed_levels',temp_dir=tmp_path)
    assert not inverted['offline_model_pass']
    assert inverted['heldout_ber']>.9
    assert inverted['pilot_ber']>.9


def test_register_bus_is_causal_and_one_transaction_at_a_time():
    iq,truth=generate('reg51_signed_400',spi_us=117)
    times=truth['write_times_us']
    assert len(times)==len(pattern())
    assert np.min(np.diff(times))>=117
    assert np.all(times >= np.arange(len(times))*2500)
    assert len(iq)==len(pattern())*600
    _,slow=generate('reg51_signed_400',control='slow_bus')
    assert np.all(np.diff(slow['write_times_us'])>=4500)
    assert slow['write_times_us'][-1]>times[-1]
    _,native=generate('native_fsk_4800')
    _,reg40=generate('reg40_frame_scale_4800')
    assert native['write_times_us'] is None and reg40['write_times_us'] is None


def test_negative_controls_are_visible_in_received_iq(tmp_path):
    healthy=run('reg51_signed_400',temp_dir=tmp_path)
    absent=run('reg51_signed_400',control='missing_gate',temp_dir=tmp_path)
    assert not absent['offline_model_pass']
    assert absent['pilot_level_separation_hz']<healthy['pilot_level_separation_hz']*.1
    slow=run('reg51_signed_400',control='slow_bus',temp_dir=tmp_path)
    assert slow['heldout_ber']>.1 and not slow['offline_model_pass']
    filter134=run('reg51_signed_400',control='subaudio_134hz',temp_dir=tmp_path)
    assert filter134['median_transition_hz']<healthy['median_transition_hz']
    noisier=run('native_fsk_4800',control='noise',temp_dir=tmp_path)
    clean=run('native_fsk_4800',temp_dir=tmp_path)
    assert noisier['eye_opening_sigma']<clean['eye_opening_sigma']


def test_phase_impulse_detectable_without_rf(tmp_path):
    clean=run('native_fsk_4800',temp_dir=tmp_path)
    impulse=run('native_fsk_4800',control='phase_impulse',temp_dir=tmp_path)
    assert impulse['max_filtered_phase_increment_rad']>clean['max_filtered_phase_increment_rad']


def test_cli_outputs_explicitly_synthetic_evidence(tmp_path):
    out=tmp_path/'results.json'
    assert main(['--paths','native_fsk_4800,reg51_signed_400',
                 '--json',str(out)])==0
    doc=json.loads(out.read_text())
    assert doc['issue']==37 and doc['physical_transmissions']==0
    assert not doc['hardware_access'] and doc['kind']=='OFFLINE'
    assert all(x['offline_model_pass'] for x in doc['results'])
    with pytest.raises(SystemExit):
        main(['--paths','native_fsk_4800,native_fsk_4800'])


@pytest.mark.parametrize('spi',[-2,0,float('nan'),float('inf'),20000])
def test_reject_unbounded_spi_clock(spi):
    with pytest.raises(ValueError):
        generate('reg51_signed_400',spi_us=spi)
