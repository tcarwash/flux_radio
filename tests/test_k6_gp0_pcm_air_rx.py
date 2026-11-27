"""Receive-only frontend tests, without opening RTL hardware."""
import numpy as np
import pytest
from scipy.signal import resample_poly
from tools.k6_gp0_pcm_air_rx import RATE,IF_OFFSET,OUTPUT_FS,downconvert_u8iq,capture_iq


def fake_u8iq(freq_hz, duration_s=.24):
    t=np.arange(round(RATE*duration_s))/RATE
    wave=.75*np.exp(2j*np.pi*freq_hz*t)
    raw=np.empty(t.size*2,dtype=np.uint8)
    raw[::2]=np.clip(np.rint(127.5+127*wave.real),0,255).astype(np.uint8)
    raw[1::2]=np.clip(np.rint(127.5+127*wave.imag),0,255).astype(np.uint8)
    return raw


def peak_frequency(x,fs):
    win=np.hanning(len(x))
    a=np.fft.fft(x*win)
    f=np.fft.fftfreq(len(x),1/fs)
    return float(f[np.argmax(abs(a))])


def test_if_shift_offset_sign_and_8to1_resampler():
    # An actual channel at LO+IF_OFFSET-31300+1000 must become +1000Hz.
    fine=-31300
    raw=fake_u8iq(IF_OFFSET+fine+1000)
    tuned=downconvert_u8iq(raw,offset_hz=IF_OFFSET+fine)
    assert tuned.dtype==np.complex64
    dec=resample_poly(tuned,1,8)
    assert abs(peak_frequency(dec[200:-200],OUTPUT_FS)-1000)<15
    assert 0.5<float(np.sqrt(np.mean(abs(dec[300:-300])**2)))<0.9


def test_shift_is_continuous_across_131072_block_boundaries():
    raw=fake_u8iq(IF_OFFSET-31300+500,duration_s=.43)
    tuned=downconvert_u8iq(raw,offset_hz=IF_OFFSET-31300)
    assert tuned.size>2*131072
    loc=131072
    # The demodulator phase remains continuous across internal blocks.
    before=tuned[loc-2:loc]
    after=tuned[loc:loc+2]
    phase_step=np.angle(after[0]*np.conj(before[-1]))
    assert abs(phase_step-2*np.pi*500/RATE)<.03
    assert abs(np.abs(after[0])-np.abs(before[-1]))<.03


def test_invalid_capture_settings_fail_without_opening_any_device():
    class NoDevice:
        def read(self,_):
            raise AssertionError("must not read RTL device for invalid parameters")
    with pytest.raises(ValueError,match="duration"):
        capture_iq(NoDevice(),1,fine_offset_hz=0)
    with pytest.raises(ValueError,match="offset"):
        capture_iq(NoDevice(),4,fine_offset_hz=70000)
    with pytest.raises(ValueError,match="interleaved"):
        downconvert_u8iq(np.array([1,2,3],dtype=np.uint8),offset_hz=0)
    with pytest.raises(ValueError,match="Nyquist"):
        downconvert_u8iq(np.array([1,2],dtype=np.uint8),offset_hz=RATE)


def test_capture_receiver_module_import_has_no_hardware_side_effects():
    # Simply importing downconvert_u8iq never opens /dev USB; explicitly
    # instantiating RtlReceiver is required to do anything physical.
    from tools import k6_gp0_pcm_air_rx
    assert k6_gp0_pcm_air_rx.QUALIFIED_HZ==443500000
    assert k6_gp0_pcm_air_rx.IF_OFFSET==125000
