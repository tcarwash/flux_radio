"""Offline tests for sim/modulation/ssb_iq.py: sideband rejection on a
coherent 750 Hz tone, LSB mirroring, zero/DC/scaling behavior,
two-tone crest/PAPR, the USB-LSB conjugate relation, input validation,
and a guard that the reference makes no device/on-air claims.
Deterministic, pure NumPy, no I/O. SPDX-License-Identifier: MIT"""
import importlib.util
import os

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "..", "sim", "modulation", "ssb_iq.py")
_spec = importlib.util.spec_from_file_location("ssb_iq_ref", _SRC)
ssb_iq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ssb_iq)

FS = 8000.0
N = 8000  # 1 s: every integer-Hz tone is DFT-coherent
T = np.arange(N) / FS
TONE = np.cos(2 * np.pi * 750.0 * T)  # coherent 750 Hz, amplitude 1


def _mag_at(z, hz):
    spec = np.fft.fft(z)
    return np.abs(spec[int(round(hz * N / FS)) % N])


def _raises(fn):
    try:
        fn()
    except ValueError:
        return
    raise AssertionError("expected ValueError from %r" % (fn,))


def test_usb_keeps_positive_sideband():
    z = ssb_iq.ssb_modulate(TONE, FS, "usb")
    rej = 20 * np.log10(_mag_at(z, 750.0) / _mag_at(z, -750.0))
    assert rej > 60.0  # FFT Hilbert zeroes the mirror up to roundoff


def test_lsb_keeps_negative_sideband():
    z = ssb_iq.ssb_modulate(TONE, FS, "lsb")
    rej = 20 * np.log10(_mag_at(z, -750.0) / _mag_at(z, 750.0))
    assert rej > 60.0


def test_zero_input_gives_zero_output():
    for sb in ("usb", "lsb"):
        z = ssb_iq.ssb_modulate(np.zeros(N), FS, sb)
        assert np.all(z == 0)


def test_dc_passes_through_unchanged():
    for sb in ("usb", "lsb"):
        z = ssb_iq.ssb_modulate(np.full(N, 0.3), FS, sb)
        assert np.max(np.abs(z.real - 0.3)) < 1e-12
        assert np.max(np.abs(z.imag)) < 1e-12


def test_real_audio_scaling():
    a = 0.5 * np.sin(2 * np.pi * 750.0 * T)
    z = ssb_iq.ssb_modulate(a, FS, "usb")
    assert np.max(np.abs(z.real - a)) < 1e-12  # real part is the audio
    assert np.max(np.abs(np.abs(z) - 0.5)) < 1e-9  # tone -> flat envelope


def test_two_tone_crest_and_papr():
    x = np.cos(2 * np.pi * 750.0 * T) + np.cos(2 * np.pi * 1250.0 * T)
    z = ssb_iq.ssb_modulate(x, FS, "usb")
    assert abs(ssb_iq.crest_factor(z) - np.sqrt(2.0)) < 1e-9
    assert abs(10 * np.log10(2.0) - ssb_iq.papr_db(z)) < 1e-6
    assert ssb_iq.crest_factor(np.zeros(8)) == 0.0
    assert ssb_iq.papr_db(np.zeros(8)) == float("-inf")


def test_usb_lsb_conjugate_pair():
    u = ssb_iq.ssb_modulate(TONE, FS, "usb")
    l = ssb_iq.ssb_modulate(TONE, FS, "lsb")
    assert np.max(np.abs(l - np.conj(u))) < 1e-12


def test_carrier_placement():
    u = ssb_iq.ssb_modulate(TONE, FS, "usb", carrier_hz=1000.0)
    l = ssb_iq.ssb_modulate(TONE, FS, "lsb", carrier_hz=1000.0)
    assert int(np.argmax(np.abs(np.fft.fft(u)))) == 1750  # carrier + audio
    assert int(np.argmax(np.abs(np.fft.fft(l)))) == 250  # carrier - audio


def test_input_validation():
    bad_fs = [0, -1.0, np.nan, np.inf, "8k", None]
    for fs in bad_fs:
        _raises(lambda fs=fs: ssb_iq.ssb_modulate(TONE, fs))
    bad_audio = [np.zeros((4, 4)), np.zeros(0), TONE + 1j * TONE,
                 np.full(8, np.nan), np.full(8, np.inf), "tone"]
    for a in bad_audio:
        _raises(lambda a=a: ssb_iq.ssb_modulate(a, FS))
    for sb in ("SSB", "upper", "", 5, None):
        _raises(lambda sb=sb: ssb_iq.ssb_modulate(TONE, FS, sb))
    for fc in (np.nan, np.inf, FS / 2, FS, -FS):
        _raises(lambda fc=fc: ssb_iq.ssb_modulate(TONE, FS, "usb", fc))


def test_no_device_or_on_air_claims():
    with open(_SRC, "r", encoding="utf-8") as f:
        src = f.read().lower()
    assert "offline" in src
    for token in ("transmit", "pa_", "antenna", "dbm", "watt",
                  "emission", "radiat"):
        assert token not in src, token
    assert "hardware" not in src
    for name in dir(ssb_iq):
        assert not name.startswith(("pa_", "tx_", "hw_", "rf_")), name
        assert "transmit" not in name
