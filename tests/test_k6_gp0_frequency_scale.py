"""Independently check the *direction* of frequency scaling.

A fixed integer decimator uses 87260 in software but the capture hardware
generates actual_rate groups per wall second. PCM is labeled and played at 8k.
Slower hardware -> fewer PCM samples per second -> a faster-played tone.
"""
import pytest
from tools.k6_gp0_irq_timing import PCM_HZ,PDM_GROUPS_PER_SECOND,GP0InterruptBudget
from tools.k6_gp0_clock_sensitivity import profile,report
from tools.k6_sotto_gp0_hardware_envelope import envelope


def played_pitch(input_hz,actual_rate_hz):
    effective_pcm_rate=PCM_HZ*actual_rate_hz/PDM_GROUPS_PER_SECOND
    return input_hz*PCM_HZ/effective_pcm_rate


@pytest.mark.parametrize("rate",[84960,87260,87770,90500])
def test_pitch_ratio_direction_with_independent_adc_clock(rate):
    p=played_pitch(500,rate)
    assert p==pytest.approx(500*87260/rate,rel=1e-12)
    if rate<87260: assert p>500
    elif rate>87260: assert p<500
    else: assert p==500


def test_500_hz_to_482_hz_requires_faster_not_slower_group_clock():
    inferred=87260*500/482.18
    assert 90400<inferred<90600
    assert played_pitch(500,84960)==pytest.approx(513.54,abs=.02)
    assert played_pitch(500,inferred)==pytest.approx(482.18,rel=1e-12)
    assert GP0InterruptBudget(0,2000,actual_group_rate_hz=inferred).actual_group_rate_hz==inferred


def test_sensitivity_profile_rejects_wrong_sign_assumption():
    r=report()
    assert r['schema']==1 and r['offline_only']
    assert len(r['scenarios'])==4
    assert r['scenarios'][0]['expected_pcm_playback_peak_hz']>500
    assert r['scenarios'][1]['expected_pcm_playback_peak_hz']<500
    assert r['scenarios'][-1]['expected_pcm_playback_peak_hz']==pytest.approx(482.18)
    with pytest.raises(ValueError):profile(0)


def test_envelope_metadata_reports_actual_rate_without_executing_arm():
    slow=envelope(cpi=[2.5],peak_fractions=[.75],execute=False,actual_group_rate_hz=84960)
    fast=envelope(cpi=[2.5],peak_fractions=[.75],execute=False,actual_group_rate_hz=90500)
    assert slow['gp0_actual_group_rate_hz']==84960
    assert slow['gp0_playback_pitch_factor']>1
    assert fast['gp0_playback_pitch_factor']<1
    assert slow['irq_period_us']>fast['irq_period_us']
    assert slow['scenarios']==fast['scenarios']==[]
