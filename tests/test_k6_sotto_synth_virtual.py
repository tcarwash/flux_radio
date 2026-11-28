"""Synthetic PCM oracle and module guards (full virtual RF via CLI runner)."""
import numpy as np
import pytest
from tools.k6_sotto_synth_virtual import pcm,PROFILES,N_SAMPLES
from tools.k6_sotto_module import build

@pytest.mark.parametrize('profile',range(4))
def test_synthetic_pcm_oracle_deterministic_and_bounded(profile):
    a,b=pcm(profile),pcm(profile)
    assert a.shape==(N_SAMPLES,) and a.dtype==np.dtype('<i2')
    assert np.array_equal(a,b)
    assert a.min()>=-32768 and a.max()<=32767
    assert (a==0).all() if profile==0 else (a!=0).any()

def test_distinct_profiles_and_invalid_fails_closed():
    options=[pcm(i) for i in range(4)]
    for i,a in enumerate(options):
        for b in options[i+1:]:assert not np.array_equal(a,b)
    with pytest.raises(ValueError):pcm(4)
    with pytest.raises(ValueError):pcm(True)

def test_on_radio_module_fits_14k_arena_with_all_four_optimizations():
    for profile in range(4):
        code,entry=build(host_resources=True,synthetic_tx=True,synth_profile=profile,
                        fast_div=True,fast_history_copy=True,fast_cb_pair=True,fast_lag_pair=True)
        assert 0<len(code)<=9784 and entry==16
        assert 14976-1040-24-len(code)>=4080+48

def test_synthetic_tx_requires_resources_and_disallows_unsafe_frequency():
    with pytest.raises(ValueError):build(synthetic_tx=True)
    with pytest.raises(ValueError):build(host_resources=True,synthetic_tx=True,synth_hz=146520000)
    with pytest.raises(ValueError):build(host_resources=True,synthetic_tx=True,synth_profile=4)
    with pytest.raises(ValueError):build(host_resources=True,synthetic_tx=True,live_tx=True)
