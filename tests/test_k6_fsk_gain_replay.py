"""Offline replay checks for gain-to-deviation RF plateau estimator."""
from pathlib import Path
import sys
import numpy as np
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"tools"))
from k6_fsk_gain_replay import measure


def test_known_two_plateau_synthetic_rf():
    fs=1_024_000
    seconds=1.25
    n=int(fs*seconds)
    t=np.arange(n,dtype=np.float64)/fs
    bit=(np.floor(t*4800)%2).astype(np.float64)
    deviation=1200
    f=-280_500+deviation*(2*bit-1)
    phase=np.cumsum(f)*2*np.pi/fs
    amp=np.where((t>.8)&(t<1.04),30.,0.)
    iq=amp*np.exp(1j*phase)
    raw=np.empty(2*n,dtype=np.uint8)
    raw[::2]=np.clip(np.rint(iq.real+127.5),0,255).astype(np.uint8)
    raw[1::2]=np.clip(np.rint(iq.imag+127.5),0,255).astype(np.uint8)
    out=measure(raw)
    assert abs(out["separation_hz"]-2400)<200
    assert out["calibrated"] is False
    assert out["transmitted"] is False


def test_invalid_input():
    with pytest.raises(ValueError):
        measure(np.zeros(100,dtype=np.uint8))
    with pytest.raises(ValueError):
        measure(np.zeros(2*1_024_000,dtype=np.float32))
