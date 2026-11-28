"""Pure PCM fixture checks. Full virtual RF matrix is an explicit tool run."""
import numpy as np
import pytest
from tools.k6_sotto_autonomous_pcm_matrix import SAMPLES,waveform


@pytest.mark.parametrize('profile',['silence','multitone','chirp','bursts'])
def test_deterministic_8khz_pcm_fixture(profile):
    a=waveform(profile)
    b=waveform(profile)
    assert a.shape==(SAMPLES,)
    assert a.dtype==np.dtype('<i2')
    assert (a==b).all()
    assert (a>=-32768).all() and (a<=32767).all()
    if profile=='silence':
        assert not a.any()
    else:
        assert a.any()
        assert a.min()<0 and a.max()>0


def test_profiles_are_distinct_and_bad_name_fails_closed():
    options=[waveform(p) for p in ('silence','multitone','chirp','bursts')]
    for i,a in enumerate(options):
        for b in options[i+1:]:
            assert not np.array_equal(a,b)
    with pytest.raises(ValueError):
        waveform('private_microphone')
