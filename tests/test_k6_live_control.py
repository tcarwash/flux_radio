import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from k6_live import LiveLink, LiveUnavailable, request_bytes, decode_status, STATUS, READ, TUNE
from k6_install import crc16_xmodem, cps_frame, InstallError


def status_data():
    return b"FLIV\x01" + struct.pack("<BBBIIHiBBBB", 0, 1, 6, 146520000, 446000000, 812, -92, 0, 3, 1, 0)


class Port:
    def __init__(self, reply=None):
        self.reply = reply
        self.buf = b""
        self.writes = []

    def write(self, data):
        self.writes.append(data)
        if self.reply:
            self.buf += self.reply(data)

    def read(self, n):
        result, self.buf = self.buf[:n], self.buf[n:]
        return result


def test_wire_format_and_status():
    req = request_bytes(TUNE, 7, 146520000)
    assert len(req) == 14 and req[:6] == b"FLXLV1"
    assert req[-2:] == struct.pack(">H", crc16_xmodem(req[:-2]))
    p = Port(lambda d: b"K6 DEBUG\n" + cps_frame(d[6], d[7], b"\0" + status_data()))
    result = LiveLink(p, initial_seq=0).status()
    assert result["vfos"][0]["hz"] == 146520000
    assert result["vfos"][1]["mode"] == "CW"
    assert result["battery_volts"] == 8.12 and result["rssi_dbm"] == -92
    assert p.writes == [request_bytes(STATUS, 1)]


def test_read_bounds_and_busy():
    p = Port(lambda d: cps_frame(d[6], d[7], b"\x00" + bytes(range(64))))
    link = LiveLink(p)
    assert link.read(0xb000, 64) == bytes(range(64))
    assert len(p.writes) == 1
    with pytest.raises(ValueError): link.read(0xb040, 1)
    with pytest.raises(ValueError): link.read(1, 64)
    p.reply = lambda d: cps_frame(d[6], d[7], b"\x01")
    with pytest.raises(InstallError, match="busy"): link.read(0, 64)


def test_no_legacy_handshake_or_end_even_on_timeout():
    p = Port()
    with pytest.raises(LiveUnavailable):
        LiveLink(p, timeout=.001, initial_seq=0).status()
    assert p.writes == [request_bytes(STATUS, 1)]


def test_tune_requires_valid_range_and_returns_status():
    def reply(d):
        return cps_frame(d[6], d[7], b"\0" + (status_data() if d[6] == STATUS else b""))
    p = Port(reply)
    link = LiveLink(p)
    with pytest.raises(ValueError): link.tune(300000000)
    assert not p.writes
    assert link.tune(146520000)["vfos"][0]["hz"] == 146520000
    assert [x[6] for x in p.writes] == [TUNE, STATUS]


def test_status_rejects_bad_version():
    with pytest.raises(InstallError): decode_status(b"FLIV\x02" + status_data()[5:])


def test_catalog_routes_and_explicit_legacy(monkeypatch):
    import k6_store
    import k6_radio_memories
    from contextlib import nullcontext
    import k6_install
    import k6_port_lock
    calls = []
    def read(port, what, *, legacy=False):
        calls.append((port, what, legacy))
        return {"lat": 45, "lon": -122}
    monkeypatch.setattr(k6_radio_memories, "read_radio", read)
    monkeypatch.setattr(k6_port_lock, "serial_lock", lambda _: nullcontext())
    p = Port(lambda d: cps_frame(d[6], d[7], b"\0" + (status_data() if d[6] == STATUS else b"")))
    monkeypatch.setattr(k6_install, "open_port", lambda _: nullcontext(p))
    client = k6_store.create_app().test_client()
    assert client.post("/radio/live/status").status_code == 400
    with client.session_transaction() as session:
        session["radio_port"] = "mock-port"
    client.get("/radio")
    client.get("/memories")
    with client.session_transaction() as session:
        live_token=session["live_radio_token"]
        memory_token=session["memory_token"]
    assert client.post("/radio/live/status").status_code == 403
    assert client.post("/radio/live/status", json={"token":live_token}).json["version"] == 1
    assert client.post("/radio/live/tune", json={"hz": 146520000,"token":live_token}).status_code == 200
    assert client.post("/radio/live/tune", json={"hz": 300000000,"token":live_token}).status_code == 400
    assert client.post("/radio/live/ptt").status_code == 404
    assert client.post("/memories/radio/location",json={"token":memory_token}).status_code == 200
    assert client.post("/memories/radio/location?legacy=1",json={"token":memory_token}).status_code == 200
    import k6_memory_radio
    monkeypatch.setattr(k6_memory_radio,"operate_live_read",lambda port:{"channels":[],"opaque_slots":[7],"pending":True})
    assert client.post("/memories/radio/live-bank",json={"token":memory_token}).json["pending"]
    assert client.post("/memories/radio/live-bank").status_code == 403
    assert calls == [("mock-port", "location", False), ("mock-port", "location", True)]
