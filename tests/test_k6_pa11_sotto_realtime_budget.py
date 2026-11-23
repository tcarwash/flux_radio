"""Offline PA11 ADC vs slim Sotto CPU feasibility, anchored to prior K6 capture."""
from pathlib import Path
import json
import math
import pytest
from tools.k6_pa11_sotto_realtime_budget import (MCU_HZ,PCM_HZ,one_budget,model,
                                                capture_params,spi_irq_block_sensitivity)
CAPTURE=Path('/home/tyler/flux-bench/micvoice-20261123/micvoice-capture.json')


def prior_capture():
    return {'samples':8164,'turns':200,'timing_ticks_100us':10261,
            'measured_sample_rate_hz':7956.33953805672}


def test_measured_adc_capture_rate_and_turn_cycles():
    x=capture_params(prior_capture())
    assert x['capture_turns']==200
    assert x['documented_turn_cycles']==59
    assert x['observed_equivalent_cycles_per_turn']==pytest.approx(60.329,abs=.002)
    assert x['rate_derived_from_ticks_hz']==pytest.approx(7956.339538,abs=.0001)
    assert x['observed_cpu_cycles_per_sample']>12000
    assert MCU_HZ==96000000 and PCM_HZ==8000


def test_unmodified_sampler_cannot_coexist_with_encoder_and_spi():
    r=model(prior_capture())
    x=next(x for x in r['scenarios'] if x['turns']==200)
    assert x['adc_equivalent_cpu_mhz']>96 # even ADC-only at 8k
    assert x['total_cpu_mhz']>140
    assert not x['nominal_fits']
    assert x['nominal_margin_mhz']<-45


def test_w90_is_candidate_not_hardware_proof():
    r=model(prior_capture())
    assert r['max_turns_at_nominal_budget']==98
    a={x['turns']:x for x in r['scenarios']}
    assert a[90]['nominal_fits']
    assert 3<a[90]['nominal_margin_mhz']<5
    assert 55<a[90]['adc_sample_active_us']<58
    assert 67<a[90]['gap_between_adc_bursts_us']<70
    assert not a[100]['nominal_fits']
    assert not a[110]['nominal_fits']
    assert not a[120]['nominal_fits']
    assert a[80]['nominal_fits']
    assert r['proof_level']=='OFFLINE_SOURCE_AND_RECORDED_CAPTURE_MODEL_ONLY'
    assert not r['radio_access']


def test_budget_requires_encoder_and_spi_headroom():
    measured=capture_params(prior_capture())['observed_equivalent_cycles_per_turn']
    a=one_budget(90,measured)
    b=one_budget(90,measured,codec_hz=0,spi_read_us=0,overhead=0,reserve=0)
    assert b['nominal_margin_mhz']>a['nominal_margin_mhz']+40
    assert b['nominal_fits']
    with pytest.raises(ValueError):one_budget(0,measured)
    with pytest.raises(ValueError):one_budget(90,measured,reserve=1.0)


def test_spi_irq_blocking_collision_sensitivity_is_hypothetical():
    r=model(prior_capture())
    x=r['spi_irq_blocking_hypothesis']
    assert x['phase_sweep_count']==25
    assert x['cases_with_any_jitter']>0
    assert x['maximum_jitter_us']<=34.000001
    assert spi_irq_block_sensitivity(phase_us=0)['delayed_sample_deadlines']>0
    with pytest.raises(ValueError):spi_irq_block_sensitivity(phase_us=-1)


def test_captured_private_metadata_is_compatible_but_no_audio_read():
    if not CAPTURE.exists():pytest.skip('Prior private K6 capture metadata unavailable')
    x=json.loads(CAPTURE.read_text())
    r=model(x)
    assert r['capture']['capture_samples']==8164
    assert r['capture']['capture_turns']==200
    assert r['capture']['measured_sample_rate_hz']==pytest.approx(7956.339538,abs=.001)
    assert r['max_turns_at_nominal_budget']==98


def test_reject_inconsistent_capture_metadata():
    x=prior_capture();x['measured_sample_rate_hz']=8000
    with pytest.raises(ValueError,match='inconsistent'):capture_params(x)
