"""TX-15X pilot-only fast gain response scorer, synthetic controls only."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx15x_gain_dynamics_analyze import score_audio, RATES, SYMBOLS


def synthetic(*, responsive=True, timing_offset=0.0, seed=4):
    rng=np.random.default_rng(seed)
    fs=64000
    t=np.arange(int(1.5*fs))/fs
    onset=.2
    freq=1090.93
    amp=np.zeros_like(t)
    amp[(t>=onset)&(t<onset+.1)]=1187.5
    cursor=onset+.1+timing_offset
    for _,_,period in RATES:
        for i in range(SYMBOLS):
            start=cursor+i*period
            value=(1018.2 if i%2==0 else 2713.1) if responsive else 1800.
            amp[(t>=start)&(t<start+period)]=value
        cursor+=SYMBOLS*period
    amp[(t>=cursor)&(t<cursor+.1)]=1187.5
    y=-400+amp*np.sin(2*np.pi*freq*(t-onset)+.4)+rng.normal(0,8,len(t))
    return y,t,onset


def test_pilot_only_fit_recovers_all_three_gain_update_rates():
    y,t,onset=synthetic()
    r=score_audio(y,t,onset)
    assert r["pilot_only_fitted_tone_hz"]==pytest.approx(1090.93,abs=.04)
    assert r["fit_uses_dynamic_gain_symbols"] is False
    assert abs(r["timing_offset_from_RF_onset_s"])<.001
    assert r["50_symbols_s_block_is_timing_training_not_independent_qualification"]
    blocks=r["fast_gain_blocks"]
    assert [b["actual_schedule_rate_symbols_s"] for b in blocks]==pytest.approx(
        [50,100,10000/67],abs=.001)
    assert all(b["high_minus_low_over_pilot24"]>1.2 for b in blocks)
    assert r["direct_rf_iq_amplitude_or_phase_measured"] is False


def test_gain_insensitive_control_is_not_mistaken_for_gain_response():
    y,t,onset=synthetic(responsive=False)
    r=score_audio(y,t,onset)
    assert all(abs(b["high_minus_low_over_pilot24"])<.04
               for b in r["fast_gain_blocks"])


def test_first_rate_estimates_common_timing_offset_for_faster_holdouts():
    y,t,onset=synthetic(timing_offset=-.0075)
    r=score_audio(y,t,onset)
    assert r["timing_offset_from_RF_onset_s"]==pytest.approx(-.0075,abs=.0005)
    assert all(b["high_minus_low_over_pilot24"]>1.2
               for b in r["fast_gain_blocks"])


def test_rejects_short_or_mismatched_discriminator_vectors():
    y,t,onset=synthetic()
    with pytest.raises(ValueError):
        score_audio(y[:-1],t,onset)
    with pytest.raises(ValueError):
        score_audio(np.zeros(1000),np.arange(1000)/64000,.2)
