"""Pure offline short-record noise metrics. No RF/serial or firmware access."""
import math
import numpy as np
import pytest
from tools.k6_gp0_noise_fingerprint import fingerprint,sine_fit


def mk_pcm(x):
    return np.clip(np.rint(np.asarray(x)*32768),-32768,32767).astype('<i2').tobytes()


def test_precisely_timed_500_1k_hz_have_correct_physical_fundamental():
    rng=np.random.default_rng(101)
    rate=16000000/1876
    t=np.arange(1600)/rate
    for hz in (500,1000):
        x=.3*np.sin(2*np.pi*hz*t)+.008*rng.normal(size=len(t))
        f=fingerprint(mk_pcm(x),1876,tone_hz=hz)
        assert abs(f['fitted_physical_fundamental_hz']-hz)<2
        assert .27<f['fundamental_peak_full_scale']<.33
        assert .006<f['residual_rms_full_scale']<.010
        assert sum(f['welch_noise_energy_percent_by_band'])==pytest.approx(100,abs=.4)
        assert f['samples_near_clip']==0


def test_whiteness_is_reported_as_exploratory_only():
    rng=np.random.default_rng(11)
    x=rng.normal(0,.035,size=1600)
    f=fingerprint(mk_pcm(x),1876)
    assert abs(f['adjacent_correlations_lags_1_2_3_4_8_16'][0])<.15
    assert f['ljung_box_16_lags_exploratory_p']>=0
    assert f['residual_rms_dbfs']<0


def test_harmonic_measure_not_misrepresented_as_certain_analog_thd():
    rate=16000000/1876
    t=np.arange(1600)/rate
    x=.3*np.sin(2*np.pi*1000*t)+.03*np.sin(2*np.pi*2000*t)
    f=fingerprint(mk_pcm(x),1876,tone_hz=1000)
    assert f['fitted_harmonic_to_fundamental_db']==pytest.approx(-20,abs=1.5)
    assert 'noise-floor-limited' in f['harmonic_measurement_caveat']


@pytest.mark.parametrize('pcm,ticks',[(b'',1876),(b'\x00',1876),(b'\x00'*300,0),(b'\x00'*300,2801)])
def test_reject_impossible_or_insufficient_input(pcm,ticks):
    with pytest.raises(ValueError):fingerprint(pcm,ticks)
