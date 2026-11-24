"""Actual receive-only SoapyFang SigMF/ChipModel tests, never radio hardware."""
import json
import numpy as np
import pytest

from tools.k6_soapyfang_modlab import (
    MODES, RATE, _iq_reference_alignment, fixture, main, run_mode,
)


@pytest.mark.parametrize('mode',MODES)
def test_six_virtual_waveforms_replayed_through_soapyfang_frontend(mode,tmp_path):
    r=run_mode(mode,snr_db=30,temp_dir=tmp_path)
    assert r['pass'], r
    assert r['source']=='SigMF RX-only IQ replay'
    assert r['rf_model']=='SoapyFang ChipModel'
    assert r['tx_model']=='ideal'
    assert not r['physical_tx'] and not r['chip_native_decode']
    assert r['received_samples']==r['expected_samples']
    assert r['fm_pcm_samples']>0
    assert r['readback_rssi_raw']>0
    assert r['alignment_lag_samples']==3
    assert np.isfinite(r['frontend_mse_ratio'])
    assert r['frontend_mse_ratio']>=0
    if mode=='cw':
        assert r['key_sample_accuracy']>.995
        assert r['max_envelope_error']<.1
    elif mode=='am':
        assert abs(r['modulation_depth']-.7)<.04
        assert r['envelope_correlation']>.99
    elif mode in ('ssb','ssb-lsb'):
        assert r['image_rejection_db']>40
        assert r['recovered_audio_correlation']>.99
    elif mode=='qpsk':
        assert r['bit_error_rate']==0
        assert r['evm_pct']<1
    elif mode=='ofdm':
        assert r['bit_error_rate']==0
        assert 5<r['evm_pct']<15  # FIR channel shapes high subcarriers


@pytest.mark.parametrize('mode',('cw','am','ssb','ssb-lsb','ofdm'))
def test_phase_only_shows_envelope_loss_and_ofdm_distortion(mode,tmp_path):
    r=run_mode(mode,snr_db=60,tx_model='phase_only',temp_dir=tmp_path)
    assert not r['pass'],r
    assert r['received_samples']==r['expected_samples']
    assert not r['physical_tx']
    if mode=='am':
        assert r['modulation_depth']<.05
    if mode=='ofdm':
        assert r['bit_error_rate']>0
        assert r['evm_pct']>30


def test_qpsk_phase_only_survives_ideal_genie_receiver_with_evm_cost(tmp_path):
    clean=run_mode('qpsk',snr_db=60,temp_dir=tmp_path)
    phase=run_mode('qpsk',snr_db=60,tx_model='phase_only',temp_dir=tmp_path)
    assert clean['pass'] and phase['pass']
    assert clean['bit_error_rate']==phase['bit_error_rate']==0
    assert phase['evm_pct']>clean['evm_pct']+5


def test_replay_noise_degrades_reference_digital_evm(tmp_path):
    for mode in ('qpsk','ofdm'):
        clean=run_mode(mode,snr_db=60,temp_dir=tmp_path)
        noisy=run_mode(mode,snr_db=6,temp_dir=tmp_path)
        assert noisy['evm_pct']>clean['evm_pct']+5


def test_invalid_parameters_and_short_reference(tmp_path):
    with pytest.raises(ValueError):
        run_mode('no_such_mode',temp_dir=tmp_path)
    with pytest.raises(ValueError):
        run_mode('cw',snr_db=float('nan'),temp_dir=tmp_path)
    with pytest.raises(ValueError):
        run_mode('cw',snr_db=-2,temp_dir=tmp_path)
    with pytest.raises(ValueError):
        run_mode('cw',tx_model='hardware',temp_dir=tmp_path)
    with pytest.raises(ValueError):
        _iq_reference_alignment(np.ones(20),np.zeros(20))


def test_cli_writes_evidence_with_explicit_no_rf_scope(tmp_path):
    out=tmp_path/'report.json'
    assert main(['--modes','qpsk,ofdm','--snr-db','30','--json',str(out)])==0
    r=json.loads(out.read_text())
    assert r['pass_all'] and len(r['results'])==2
    assert r['physical_tx'] is False and r['actual_radio_chip'] is False
    assert all(v['rf_model']=='SoapyFang ChipModel' for v in r['results'])
    with pytest.raises(SystemExit):
        main(['--modes','cw,cw'])


def test_fixtures_are_bounded_complex_and_have_correct_clock():
    for mode in MODES:
        x,_=fixture(mode)
        assert x.ndim==1 and x.size>=1024
        assert np.all(np.isfinite(x))
        assert RATE>=48000
