"""Pure host KISS wire tests. No radio, TCP listener or SDR."""
import importlib.util
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("k6_kiss_tnc", ROOT / "tools/k6_kiss_tnc.py")
k = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(k)


def encoded_control(op, status=0, payload=b""):
    return k.kiss_frame(k.HARDWARE, k.PREFIX + bytes([op | 0x80, status]) + payload)


def decode_one(wire):
    frames = []
    parser = k.KISSDecoder()
    for b in wire:
        frames += parser.feed(bytes([b]))
    assert len(frames) == 1
    return frames[0]


@pytest.mark.parametrize("payload",[
    b"\x00\xC0\xDB\x01",
    bytes(range(256)),
    b"FX\x01\x06",
    b"\xC0" * 50,
    b"\xDB" * 50
])
def test_round_trip_all_kiss_reserved_bytes(payload):
    assert decode_one(k.kiss_frame(0, payload)) == b"\x00" + payload


def test_decoder_handles_incremental_and_repeated_boundaries():
    dec = k.KISSDecoder()
    part = k.kiss_frame(0, b"ABC")
    assert dec.feed(part[:3]) == []
    assert dec.feed(part[3:] + b"\xC0\xC0") == [b"\x00ABC"]


@pytest.mark.parametrize("payload", [
    b"\xC0\x00\xDB\x00\xC0",
    b"\xC0\x00\xDB\xC0",
    b"\xC0\x00\x11\xDB\x01\xC0",
])
def test_malformed_escape_is_discarded_without_poisoning_next_frame(payload):
    dec = k.KISSDecoder()
    assert dec.feed(payload + k.kiss_frame(0, b"OK")) == [b"\x00OK"]


def test_buffer_overflow_drops_frame_and_resynchronizes():
    dec = k.KISSDecoder(max_frame=7)
    assert dec.feed(k.kiss_frame(0, b"LONG-TRUNCATED") + k.kiss_frame(0, b"GOOD")) == [
        b"\x00GOOD"
    ]


def test_unknown_unframed_data_ignored():
    dec = k.KISSDecoder()
    assert dec.feed(b"debug log without framing" + k.kiss_frame(0, b"good")) == [
        b"\x00good"
    ]


def test_mode_list_descriptor_and_terminator():
    r = k.decode_control(decode_one(encoded_control(k.LIST, payload=
        b"\x01\x00\x01\x4b\x00\x09APRS-1200")))
    assert r["op"] == k.LIST
    m = k.parse_mode_reply(r)
    assert m == {"id":1,"name":"APRS-1200","flags":1,"max_payload":75,
                 "packet_tx":True,"packet_rx":False,"voice":False}
    sentinel = k.decode_control(decode_one(encoded_control(
        k.LIST, payload=b"\xff\xff\x00\x00\x00\x00")))
    assert k.parse_mode_reply(sentinel) is None


@pytest.mark.parametrize("raw",[
    b"\x01",
    b"\x01\x00\x01\x4b\x00\x05A",
    b"\x01\x00\x01\x4b\x00\x03\xff\xff\xff",
    b"\x00\x00\x01\x4b\x00\x01X",
    b"\xff\xff\x01\x00\x00\x00",
])
def test_reject_invalid_mode_reply(raw):
    r = {"op":k.LIST,"status":0,"payload":raw}
    with pytest.raises(k.ProtocolError):
        k.parse_mode_reply(r)


class FakeSerial:
    def __init__(self, responses):
        self.buffer = bytearray(b"".join(responses))
        self.writes = []
    def write(self, data):
        self.writes.append(data)
        return len(data)
    def read(self, count):
        if not self.buffer:
            raise TimeoutError("test response consumed")
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data


def test_control_list_get_set_and_validation():
    s = FakeSerial([
        encoded_control(k.LIST,payload=b"\x01\x00\x01\x4b\x00\x09APRS-1200"),
        encoded_control(k.LIST,payload=b"\xff\xff\x00\x00\x00\x00")
    ])
    assert k.list_modes(s)[0]["name"] == "APRS-1200"
    assert decode_one(s.writes[0]) == bytes([k.HARDWARE]) + k.PREFIX + bytes([k.LIST])
    s = FakeSerial([encoded_control(k.GET,payload=b"\x01\x00")])
    assert k.get_mode(s) == 1
    s = FakeSerial([encoded_control(k.SET,payload=b"\x01\x00")])
    assert k.set_mode(s,1) == 1
    assert decode_one(s.writes[0]) == bytes([k.HARDWARE]) + k.PREFIX + bytes([k.SET,1,0])


def test_set_never_keys_and_refuses_wrong_ack():
    s = FakeSerial([encoded_control(k.SET,payload=b"\x02\x00")])
    with pytest.raises(k.ProtocolError):
        k.set_mode(s,1)
    for mode in (0,0xffff,0x10000):
        with pytest.raises(k.ProtocolError):
            k.set_mode(s,mode)


def test_refused_operation_is_reported_not_ignored():
    s = FakeSerial([encoded_control(k.SET,status=3)])
    with pytest.raises(k.ProtocolError,match="refused"):
        k.set_mode(s,1)


def test_wrong_extension_reply_is_ignored_until_requested_response():
    s = FakeSerial([
        k.kiss_frame(0, b"APRS frame"),
        encoded_control(k.STATUS,payload=b"ignored"),
        encoded_control(k.GET,payload=b"\x01\x00")
    ])
    assert k.get_mode(s) == 1


def test_cli_nonlocal_listener_requires_explicit_opt_in():
    with pytest.raises(SystemExit):
        k.cli(["serve","--listen","0.0.0.0"])
