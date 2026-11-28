"""Build and compare exact Rust variants offline. No on-radio build switch."""
import numpy as np
import pytest
from tools.k6_gp0_noise_filter_model import (
    SOURCE,INPUT_RATE,OUT_RATE,MODES,make_noise,to_counts,compile_variants,run,model)


def test_noise_profiles_are_bounded_and_reproducible():
    n=2000
    for mode in ('white','highpass','inband'):
        x=make_noise(mode,n,2027)
        y=make_noise(mode,n,2027)
        np.testing.assert_array_equal(x,y)
        b=to_counts(x)
        assert b.dtype==np.uint8 and np.max(b)<=64 and np.min(b)>=0
    with pytest.raises(ValueError):make_noise('not-real',n,11)


def test_compiled_actual_rust_is_offline_only_and_preserves_group_count(tmp_path):
    variants=compile_variants(tmp_path)
    assert set(variants)==set(MODES)
    counts=np.full(87260,dtype=np.uint8,fill_value=32)
    expected=(len(counts)*7500)//87260
    for name,exe in variants.items():
        y=run(exe,counts)
        assert len(y)==expected
        assert y.dtype==np.int16
        assert np.max(abs(y.astype(np.int32)))<=32767


def test_conditional_filter_benefit_not_universal():
    result=model(duration=.5)
    w=result['noise_models']['white']
    low=result['noise_models']['inband']
    assert result['offline_only'] and result['excluded_from_firmware_build_gate']
    assert w['legacy']['snr_gain_vs_unfiltered_db']==0
    assert w['aa3']['snr_gain_vs_unfiltered_db']>1
    assert w['fir11_late']['snr_gain_vs_unfiltered_db']>0
    assert low['aa3']['snr_gain_vs_unfiltered_db']<w['aa3']['snr_gain_vs_unfiltered_db']
    assert -7<result['speech_band_tone_response']['2800']['aa3']<-3
    assert -3<result['speech_band_tone_response']['2800']['fir11_late']<-.5
    with pytest.raises(ValueError):model(duration=.2)
