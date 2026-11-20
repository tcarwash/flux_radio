"""Acceptance probe never executes a module, keys PTT or touches the loader."""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_install import InstallError
from k6_live import BURST_DATA, BURST_LONG
from k6_live_acceptance import checked_rx, observe, abandon_stream


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def rx(*, tx=False, standby=True, sleep=False, hz=147790000):
    return dict(standby_idle=standby, transmitting=tx, sleeping=sleep,
                master=0, vfos=[dict(hz=hz), dict(hz=147180000)])


class Link:
    def __init__(self):
        self.state = rx()
        self.calls = []
        self.seq = 7
        self.buf = b"junk"
        self.text = b"junk"

    def status(self):
        self.calls.append("status")
        return self.state

    def info(self):
        self.calls.append("info")
        return {"build_id": bytes.fromhex("f693147e70527a58")}

    def capabilities(self):
        self.calls.append("caps")
        return {"features": ["status", "bulk_stream"]}


def test_checked_rx_fails_closed():
    link = Link()
    state, build = checked_rx(link, expected_id="f693147e70527a58")
    assert state is link.state and build == "f693147e70527a58"
    for state in (rx(tx=True, standby=False), rx(sleep=True),
                  rx(standby=False)):
        link.state = state
        with pytest.raises(InstallError, match="awake RX"):
            checked_rx(link)
    link.state = rx()
    with pytest.raises(InstallError, match="Build mismatch"):
        checked_rx(link, expected_id="0000000000000000")
    with pytest.raises(InstallError, match="VFO settings changed"):
        checked_rx(link, previous=rx(hz=146520000))


def test_observe_logs_transitions_and_unknown_without_retry(tmp_path):
    link = Link()
    clock = Clock()
    states = [rx(), rx(tx=True, standby=False), rx(tx=False)]
    reads = 0
    def status():
        nonlocal reads
        reads += 1
        if reads == 4:
            raise InstallError("No live response")
        return states[min(reads - 2 if reads >= 2 else 0, len(states)-1)]
    link.status = status
    path = tmp_path / "operator.jsonl"
    result = observe(link, duration=1, interval=.25, output=path,
                     clock=clock.monotonic, sleep=clock.sleep)
    import json
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    assert len(rows) == 4
    assert sum(row["type"] == "no-valid-status" for row in rows) == 1
    assert result["invalid_samples"] == 1 and result["samples"] == 3
    assert result["no_automatic_ptt_or_power"] is True
    assert link.calls.count("info") == 1
    with pytest.raises(FileExistsError):
        observe(link, duration=1, interval=.25, output=path,
                clock=clock.monotonic, sleep=clock.sleep)


class Port:
    def __init__(self):
        self.baudrate = 115200
        self.resets = 0

    def reset_input_buffer(self):
        self.resets += 1


def test_abandoned_read_verifies_recovery_without_mutation():
    snapshot = bytes((i * 37 + i // 256) & 255 for i in range(2 * 1024 * 1024))
    clock = Clock()
    link = Link()
    port = Port()
    def request(command, arg):
        assert command == BURST_LONG
        assert port.baudrate == 115200
        link.addr = (arg & 0x7fff) * 64
        link.calls.append("burst-long")
        link.seq += 1
        return struct.pack("<I", 500000)
    def frame(deadline):
        assert port.baudrate == 500000 and deadline == clock.now + 2
        return (BURST_DATA, link.seq,
                struct.pack("<I", link.addr) + snapshot[link.addr:link.addr+256])
    def old_burst(addr, count, rate):
        assert port.baudrate == 115200 and count == 16 and rate == 500000
        link.calls.append("old-burst")
        return snapshot[addr:addr+4096]
    link.request = request
    link._frame = frame
    link._burst = old_burst
    result = abandon_stream(link, port, snapshot, expected_id="f693147e70527a58",
                            count=3, clock=clock.monotonic, sleep=clock.sleep)
    assert result["ok"] and len(result["attempts"]) == 3
    assert result["physical_usb_disconnected"] is False
    assert link.calls.count("burst-long") == 3
    assert link.calls.count("old-burst") == 3
    assert port.resets == 3 and port.baudrate == 115200
    assert link.buf == link.text == b""
    assert result["tx_requested"] is False


def test_abandoned_read_requires_full_backup_and_valid_features():
    link, port = Link(), Port()
    with pytest.raises(ValueError, match="complete 2 MiB"):
        abandon_stream(link, port, b"x", expected_id="f693147e70527a58")
    link.capabilities = lambda: {"features": []}
    with pytest.raises(InstallError, match="long read-only streams"):
        abandon_stream(link, port, bytes(2*1024*1024),
                       expected_id="f693147e70527a58")


def test_burst_failure_waits_for_autonomous_baud_recovery():
    snapshot = bytes(2 * 1024 * 1024)
    clock = Clock()
    link, port = Link(), Port()
    def request(command, arg):
        assert command == BURST_LONG
        link.calls.append("launch")
        link.seq += 1
        return struct.pack("<I", 500000)
    link.request = request
    link._frame = lambda deadline: None
    with pytest.raises(InstallError, match="First stream frame"):
        abandon_stream(link, port, snapshot, expected_id="f693147e70527a58",
                       count=1, clock=clock.monotonic, sleep=clock.sleep)
    assert port.baudrate == 115200
    assert port.resets == 1
    assert clock.now >= 1.55
    assert link.calls.count("launch") == 1
    assert "old-burst" not in link.calls
