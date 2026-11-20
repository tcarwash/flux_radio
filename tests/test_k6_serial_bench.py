"""The speed test must reject corrupted data and never hide loss with retries."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import k6_serial_bench as bench
from k6_install import InstallError, cps_frame


class Port:
    def __init__(self, response=b''):
        self.response = response
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, n):
        out, self.response = self.response[:n], self.response[n:]
        return out

    def reset_input_buffer(self):
        self.response = b''


def test_missing_reply_is_not_retried():
    port = Port()
    cable = bench.BenchCable(port, timeout=.001, log=lambda s: None)
    with pytest.raises(InstallError):
        cable.calls([(bench.CMD_ECHO, 7, bytes(range(96)))])
    assert len(port.writes) == 1


def test_stream_checks_binary_pattern_and_sequence():
    data = bytes((i * 73) & 255 for i in range(256))
    port = Port(cps_frame(bench.CMD_STREAM, 0, data) +
                cps_frame(bench.CMD_STREAM, 1, bytes(b ^ 1 for b in data)))
    result = bench.stream(bench.BenchCable(port, log=lambda s: None), 2)
    assert result['bytes'] == 512
    assert len(port.writes) == 1
    bad = Port(cps_frame(bench.CMD_STREAM, 1, data))
    with pytest.raises(RuntimeError, match='mismatch'):
        bench.stream(bench.BenchCable(bad, log=lambda s: None), 1)


def test_corrupt_frame_cannot_be_hidden_by_a_valid_duplicate():
    frame = cps_frame(bench.CMD_STREAM, 0, bench.pattern(0))
    damaged = frame[:-1] + bytes([frame[-1] ^ 1])
    port = Port(damaged + frame)
    cable = bench.BenchCable(port, timeout=.001, log=lambda s: None)
    # The general parser may resynchronize; a benchmark must still report the CRC loss.
    with pytest.raises(RuntimeError):
        bench.stream(cable, 1)
    assert cable.bad_response_crcs >= 1
    assert len(port.writes) == 1


def test_burst_queues_commands_before_fast_output_and_returns_to_baseline(monkeypatch):
    import struct
    monkeypatch.setattr(bench.time, 'sleep', lambda seconds: None)
    response = (cps_frame(bench.CMD_BAUD, 0, struct.pack('<I', 500000)) +
                cps_frame(bench.CMD_STREAM, 0, bench.pattern(0)) +
                cps_frame(bench.CMD_BAUD, 0, struct.pack('<I', 115246)))
    port = Port(response)
    port.flush = lambda: None
    port.baudrate = 115200
    cable = bench.BenchCable(port, log=lambda s: None)
    actual, result = bench.burst(cable, 500000, 1)
    assert actual == 500000
    assert result['bytes'] == 256
    assert port.baudrate == 115200
    assert port.writes == [
        cps_frame(bench.CMD_BAUD, 0, struct.pack('<I', 500000)) +
        cps_frame(bench.CMD_STREAM, 0, struct.pack('<H', 1)) +
        cps_frame(bench.CMD_BAUD, 0, struct.pack('<I', 115200))
    ]
