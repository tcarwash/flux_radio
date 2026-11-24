"""CW RF envelope sweep analysis: input validation and model-only checks."""
import json
import numpy as np
import pytest
from sim.modulation.cw_tx_response import (
    analyze_tone,fit_single_pole,synth_tone,bounded_lowpass_preemphasis)
from tools.k6_cw_tx_sweep_analyze import (
    DEFAULT_FREQS,analyze_synthetic,read_manifest,cw_forecast,main)
from sim.modulation.cw_envelope import raised_cosine_envelope,text_to_schedule


def test_synthetic_lowpass_sweep_recovers_known_cutoff_without_hardware():
    data=analyze_synthetic(cutoff_hz=90,am_pm_deg=12)
    fit=fit_single_pole(data['points'])
    assert fit['fit_credible'] and not fit['physical_rf_evidence']
    assert fit['estimated_cutoff_hz']==pytest.approx(90,abs=1.5)
    assert fit['response_fit_rms_db']<.03
    assert len(data['points'])==len(DEFAULT_FREQS)
    assert all(p['physical_rf_evidence'] is False for p in data['points'])
    assert all(p['input_provenance']=='synthetic_model_not_hardware' for p in data['points'])
    assert data['declarations_verified'] is False


def test_wider_synthetic_corner_recovers_350hz_and_gain_loss():
    data=analyze_synthetic(cutoff_hz=350,am_pm_deg=0)
    fit=fit_single_pole(data['points'])
    assert fit['fit_credible']
    assert fit['estimated_cutoff_hz']==pytest.approx(350,abs=3)
    assert data['points'][0]['normalized_am_gain']>.99
    assert data['points'][-1]['normalized_am_gain']<.36


def test_measured_frequency_transfer_estimates_lag_and_am_pm():
    u,iq=synth_tone(80,cutoff_hz=90,rf_am_pm_deg=12,snr_db=80)
    p=analyze_tone(u,iq,48000,80)
    assert p['normalized_am_gain']==pytest.approx(.747,abs=.015)
    assert p['lag_degrees']==pytest.approx(41.6,abs=1.5)
    assert 2<p['am_pm_ripple_deg']<2.5
    assert p['envelope_thd_percent']<.05
    assert p['rf_carrier_cfo_hz']==pytest.approx(230,abs=.1)
    no_phase=synth_tone(80,cutoff_hz=90,rf_am_pm_deg=0,snr_db=80)
    r=analyze_tone(no_phase[0],no_phase[1],48000,80)
    assert r['am_pm_ripple_deg']<.05
    assert p['samples_analyzed']>1000


def test_joint_harmonic_fit_does_not_invent_thd_at_noncoherent_120hz():
    u,iq=synth_tone(120,cutoff_hz=90,snr_db=100)
    p=analyze_tone(u,iq,48000,120)
    assert p['envelope_thd_percent']<.05


def test_bad_non_one_pole_response_is_rejected_not_misrepresented_as_cutoff():
    inputs=[{'frequency_hz':f,'normalized_am_gain':g} for f,g in
            zip((10,30,90,300,1000),(.95,.08,.88,.04,.6))]
    fit=fit_single_pole(inputs)
    assert not fit['fit_credible']
    assert fit['response_fit_rms_db']>2
    with pytest.raises(ValueError):fit_single_pole(inputs[:3])
    with pytest.raises(ValueError):fit_single_pole([inputs[0]]*4)


def test_bounded_inverse_exactly_recovers_baseline_at_strength_zero():
    target=raised_cosine_envelope(text_to_schedule('CQ',20),48000,.005)
    same=bounded_lowpass_preemphasis(target,cutoff_hz=90,strength=0)
    np.testing.assert_array_equal(same,target)
    corrected=bounded_lowpass_preemphasis(target,cutoff_hz=90,strength=1)
    assert corrected.min()==0
    assert corrected.max()<=1
    assert np.count_nonzero(corrected)!=0
    assert not np.any(corrected[target==0])
    limited=bounded_lowpass_preemphasis(target,cutoff_hz=90,ceiling=.5)
    assert limited.max()<=.5
    assert np.all(limited>=0)


def test_cw_forecast_reports_all_keyoffs_not_misleading_file_tail():
    forecasts=cw_forecast(90,write_us=117)
    assert len(forecasts)==8
    for ramp in (4.,5.,8.,12.):
        naive=next(x for x in forecasts if x['ramp_ms']==ramp and x['method']=='naive')
        corrected=next(x for x in forecasts if x['ramp_ms']==ramp and x['method']=='clipped_one_pole_inverse')
        assert corrected['max_amplitude_error']<naive['max_amplitude_error']
        assert corrected['mean_absolute_error']<naive['mean_absolute_error']
        assert corrected['post_keyoff_5ms_peak']<naive['post_keyoff_5ms_peak']
        assert naive['post_keyoff_1ms_peak']>0
        assert naive['simulated_write_count']>100
        assert naive['physical_rf'] is False and corrected['modeled_only']
    five=next(x for x in forecasts if x['ramp_ms']==5 and x['method']=='clipped_one_pole_inverse')
    assert five['max_amplitude_error']==pytest.approx(.144,abs=.02)
    assert five['post_keyoff_5ms_peak']<.01


def test_forecast_faster_envelope_makes_preemphasized_cw_nearer_ideal():
    lo=cw_forecast(90)
    hi=cw_forecast(350)
    get=lambda row:next(x for x in row if x['ramp_ms']==5 and x['method']=='clipped_one_pole_inverse')
    assert get(hi)['max_amplitude_error']<get(lo)['max_amplitude_error']
    assert get(hi)['max_amplitude_error']<.03
    assert get(hi)['post_keyoff_5ms_peak']<1e-4


def test_manifest_requires_actual_same_clock_command_trace_and_provenance(tmp_path):
    frequencies=(10.,40.,120.,350.,1000.)
    rows=[]
    for i,hz in enumerate(frequencies):
        u,x=synth_tone(hz,cutoff_hz=90,snr_db=60)
        x.astype('<c8').tofile(tmp_path/f'capture_{i}.sigmf-data')
        u.astype('<f4').tofile(tmp_path/f'control_{i}.bin')
        rows.append({'frequency_hz':hz,'iq_cf32le':f'capture_{i}.sigmf-data',
                     'command_f32le':f'control_{i}.bin'})
    doc=dict(sample_rate_hz=48000,source_kind='synthetic',runs=rows)
    path=tmp_path/'manifest.json';path.write_text(json.dumps(doc))
    read=read_manifest(path)
    assert read['provenance']=='synthetic'
    assert fit_single_pole(read['points'])['estimated_cutoff_hz']==pytest.approx(90,abs=2)
    assert all(p['physical_rf_evidence'] is False for p in read['points'])
    doc['source_kind']='operator_declared_conducted_iq'
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='AGC-off'):
        read_manifest(path)
    doc.update(sdr_agc_disabled=True,sdr_fixed_gain=True,
               external_dummy_load_confirmed=True,gain_settings_recorded=True,
               tx_restore_verified=True)
    path.write_text(json.dumps(doc))
    declared=read_manifest(path)
    assert declared['provenance']=='operator_declared_conducted_iq'
    assert declared['declarations_verified'] is False
    assert all(p['physical_rf_evidence'] for p in declared['points'])
    doc['runs'][0]['command_f32le']=rows[1]['command_f32le']
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='identical sample count'):
        read_manifest(path)


def test_cli_synthetic_report_and_manifest_validation(tmp_path):
    out=tmp_path/'report.json'
    assert main(['--synthetic','--synthetic-cutoff-hz','90',
                 '--json',str(out)])==0
    report=json.loads(out.read_text())
    assert not report['physical_rf_verified']
    assert report['one_pole_fit']['estimated_cutoff_hz']==pytest.approx(90,abs=2)
    assert len(report['cw_forecasts'])==8
    assert {r['method'] for r in report['cw_forecasts']}=={'naive','clipped_one_pole_inverse'}
    assert main(['--synthetic','--synthetic-cutoff-hz','350',
                 '--json',str(out)])==0
    assert json.loads(out.read_text())['one_pole_fit']['estimated_cutoff_hz']==pytest.approx(350,abs=3)
    with pytest.raises(SystemExit):main(['--synthetic','--write-us','0'])
    with pytest.raises(SystemExit):main(['--manifest',str(tmp_path/'missing.json')])


@pytest.mark.parametrize('bad',[
    lambda:analyze_tone(np.zeros(200),np.zeros(200,dtype=complex),48000,100),
    lambda:analyze_tone(np.ones(1000),np.ones(1000,dtype=complex),48000,100),
    lambda:analyze_tone(np.full(1000,.5),np.zeros(1000,dtype=complex),48000,100),
    lambda:analyze_tone(np.ones(2000)*1.01,np.ones(2000,dtype=complex),48000,100),
    lambda:analyze_tone(np.zeros(2000),np.zeros(2001,dtype=complex),48000,100),
    lambda:synth_tone(100,cutoff_hz=0),
    lambda:synth_tone(100,rf_am_pm_deg=120),
    lambda:bounded_lowpass_preemphasis(np.ones(100),cutoff_hz=0),
    lambda:bounded_lowpass_preemphasis(np.ones(100),strength=-.2),
    lambda:bounded_lowpass_preemphasis(np.ones(100),ceiling=2),
    lambda:bounded_lowpass_preemphasis(np.full(100,np.nan)),
    lambda:cw_forecast(0),
])
def test_bad_inputs_fail_closed(bad):
    with pytest.raises(ValueError):bad()
