"""No hardware required: RF source, DSP, register bus and workbench regressions."""
import json
import struct
import time
from types import SimpleNamespace
import wave
import io

import numpy as np
import pytest

from sim.k6emu.rf.backend import RFBackend, create_backend
from sim.k6emu.rf.sources import SyntheticSource, SigmfSource, Station, SoapySource, write_sigmf
from sim.k6emu.__main__ import K6Board, APP_BASE, RAM_BASE


def regs_for(hz):
    n = hz // 10
    return {0x38: n & 0xffff, 0x39: n >> 16}


def tiny_image(tmp_path):
    # Valid vector table and a Thumb infinite branch; no hardware image needed.
    p = tmp_path / "loop.bin"
    p.write_bytes(struct.pack("<IIH", RAM_BASE + 24*1024, APP_BASE + 9, 0xe7fe))
    return p


def test_synthetic_rssi_audio_and_retune():
    hz = 440_850_000
    b = RFBackend(SyntheticSource([Station(hz, -75), Station(hz+50_000, -95)],
                                  rate=240_000, center_hz=hz))
    try:
        r = regs_for(hz)
        strong = b.read(0x67, r, .06)
        assert abs(strong / 2 - 174 + 75) < 3
        assert b.read(0x65, r, .07) < 60
        assert b.read(0x0c, {**r, 0x0c: 0x80}, .08) == 0x82
        _, pcm = b.chip.pcm_after(0)
        assert len(pcm) > 300
        f = np.fft.rfftfreq(len(pcm), 1 / b.chip.pcm_rate)
        assert abs(f[np.argmax(abs(np.fft.rfft(pcm * np.hanning(len(pcm)))))] - 1000) < 40
        weak = b.read(0x67, regs_for(hz+50_000), .1)
        assert strong - weak > 30
        assert b.read(0x12, r, .11) is None
        assert b.status()["receive_only"]
    finally:
        b.close()


@pytest.mark.parametrize("datatype,dtype,raw,expected", [
    ("cf32_le", "<c8", [1+2j, 3+4j], [1+2j, 3+4j]),
    ("ci16_le", "<i2", [16384, -16384, 8192, 0], [.5-.5j, .25+0j]),
    ("ci8", "i1", [64, -64, 32, 0], [.5-.5j, .25+0j]),
])
def test_sigmf_replay(tmp_path, datatype, dtype, raw, expected):
    base = tmp_path / "test"
    np.asarray(raw, dtype=dtype).tofile(str(base)+".sigmf-data")
    (tmp_path/"test.sigmf-meta").write_text(json.dumps({"global":{"core:datatype":datatype,"core:sample_rate":240000},
        "captures":[{"core:sample_start":0,"core:frequency":440850000}]}))
    src = SigmfSource(base)
    try:
        np.testing.assert_allclose(src.read(4), [*expected, 0, 0])
        assert not src.tune(146520000)
    finally:
        src.close()
    src = SigmfSource(base, loop=True)
    try:
        np.testing.assert_allclose(src.read(5), [*expected, *expected, expected[0]])
    finally:
        src.close()


def test_sigmf_rssi_and_replay(tmp_path):
    hz = 440_850_000
    iq = SyntheticSource([Station(hz, -80)], rate=240000, center_hz=hz).read(24000)
    meta = write_sigmf(tmp_path/"tone", iq, 240000, hz)
    b = create_backend("sigmf:"+meta)
    try:
        assert abs(b.read(0x67, regs_for(hz), .05)/2-174+80) < 3
        assert b.read(0x67, regs_for(146_520_000), .06) < 120
    finally:
        b.close()


def test_board_bus_latches_once(tmp_path):
    calls = []
    class Backend:
        def read(self, a, regs, seconds):
            calls.append(a)
            return 0xA55A if a == 0x67 else None
        def close(self):
            calls.append("close")
    board = K6Board(tiny_image(tmp_path), rf_backend=Backend())
    board.fd_regs[0x12] = 0x1234
    def read(a):
        board._fd_pin_change(8, 0)
        for i in range(7, -1, -1):
            bit = (((a | 0x80) >> i) & 1) << 6
            board._fd_pin_change(bit, bit | 32)
        value = 0
        for _ in range(16):
            board._fd_pin_change(0, 32)
            value = value << 1 | board.fd_read_bit
            board._fd_read_sampled()
        board._fd_pin_change(0, 8)
        return value
    assert read(0x67) == 0xA55A
    assert read(0x12) == 0x1234
    assert calls == [0x67, 0x12]
    board.close_rf()
    assert read(0x67) == 0
    assert calls[-1] == "close"


def test_soapy_receive_only_and_disconnect():
    calls = []
    class Device:
        def __init__(self, args): pass
        def setSampleRate(self, direction, ch, rate): assert direction == 1; self.rate = rate
        def getSampleRate(self, direction, ch): return self.rate
        def setFrequency(self, direction, ch, hz): assert direction == 1; self.hz = hz
        def getFrequency(self, direction, ch): return self.hz
        def setupStream(self, direction, fmt, channels): calls.append(direction); return 1
        def activateStream(self, stream): pass
        def readStream(self, stream, buffers, n, timeoutUs): return SimpleNamespace(ret=-99)
        def deactivateStream(self, stream): calls.append("deactivate")
        def closeStream(self, stream): calls.append("close")
    soapy = SimpleNamespace(Device=Device, SOAPY_SDR_RX=1, SOAPY_SDR_CF32="CF32")
    source = SoapySource(rate=240000, soapy=soapy)
    b = RFBackend(source)
    try:
        end = time.monotonic()+2
        while not b.chip.error and time.monotonic()<end:
            time.sleep(.01)
        assert b.status()["error"]
        assert b.read(0x67, regs_for(440850000), .01) == 0
        assert b.read(0x0c, {**regs_for(440850000), 0x0c:0x82}, .01) == 0x80
    finally:
        b.close()
    assert calls == [1, "deactivate", "close"]


def test_workbench_audio_status_reboot(tmp_path):
    from sim.k6ide import K6Workbench, create_app
    b = K6Workbench(tiny_image(tmp_path), rf_factory=lambda: create_backend("synthetic", rate=240000))
    try:
        b.set_running(False)
        old = b.board.rf_backend
        with b.lock:
            old.read(0x67, regs_for(440850000), .06)
        client = create_app(b).test_client()
        assert client.get("/api/state").json["rf_backend"]["tuned_hz"] == 440850000
        response = client.get("/api/rf/audio.wav")
        assert response.status_code == 200
        with wave.open(io.BytesIO(response.data)) as wav:
            assert wav.getnchannels() == 1 and wav.getnframes() > 0
        assert b"SDR-backed RF model" in client.get("/").data
        b.reboot()
        assert old.closed and b.board.rf_backend is not old
    finally:
        b.close()


def test_default_workbench_unchanged(tmp_path):
    from sim.k6ide import K6Workbench, create_app
    b = K6Workbench(tiny_image(tmp_path))
    try:
        client = create_app(b).test_client()
        assert client.get("/api/state").json["rf_backend"] is None
        assert client.get("/api/rf/audio.wav").status_code == 400
    finally:
        b.close()


def test_npy_archive_requires_explicit_metadata(tmp_path):
    p = tmp_path / "archive.npy"
    np.save(p, np.array([.1+.2j, .3+.4j], dtype=np.complex64))
    with pytest.raises(ValueError, match="requires"):
        create_backend("npy:"+str(p))
    b = create_backend("npy:"+str(p), rate=240000, center_hz=440850000)
    try:
        np.testing.assert_allclose(b.source.read(3), [.1+.2j, .3+.4j, 0])
        assert b.status()["replay_eof"]
    finally:
        b.close()


def test_soapy_reads_rx_samples_and_retunes():
    calls = []
    class Device:
        def __init__(self, args): pass
        def setSampleRate(self, direction, ch, rate): assert direction == 1; self.rate = rate
        def getSampleRate(self, direction, ch): return self.rate
        def setFrequency(self, direction, ch, hz): assert direction == 1; self.hz = hz; calls.append(hz)
        def getFrequency(self, direction, ch): return self.hz
        def setupStream(self, direction, fmt, channels): assert direction == 1; return 1
        def activateStream(self, stream): pass
        def readStream(self, stream, buffers, n, timeoutUs):
            buffers[0][:n] = .25+.5j
            return SimpleNamespace(ret=n)
        def deactivateStream(self, stream): pass
        def closeStream(self, stream): pass
    soapy = SimpleNamespace(Device=Device, SOAPY_SDR_RX=1, SOAPY_SDR_CF32="CF32")
    source = SoapySource(rate=240000, center_hz=440850000, soapy=soapy)
    try:
        np.testing.assert_allclose(source.read(4096), .25+.5j)
        source.tune(146520000)
        np.testing.assert_allclose(source.read(4096), .25+.5j)
        assert calls == [440910000, 146580000]
    finally:
        source.close()
