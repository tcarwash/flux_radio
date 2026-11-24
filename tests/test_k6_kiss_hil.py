"""Offline tests for the opt-in NO-RF KISS TNC HIL harness.

OFFLINE: no radio, serial port, SDR, bench lock, flashing, transmit, network,
or GitHub access is touched here. Every hardware-touching symbol is stubbed
to explode if called; the harness must succeed in default (freeze-only) mode
without reaching any of them, and fail closed on bad attestations.
SPDX-License-Identifier: MIT
"""
import importlib.util
import json
import os
import sys
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS_PATH = os.path.join(ROOT, "tools", "k6_kiss_hil.py")
KISS_PATH = os.path.join(ROOT, "tools", "k6_kiss_tnc.py")
TNC_SRC = os.path.join(ROOT, "c", "k6", "mod", "fluxtnc.c")


def load_harness():
    spec = importlib.util.spec_from_file_location("k6_kiss_hil", HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_kiss():
    spec = importlib.util.spec_from_file_location("k6_kiss_tnc_t", KISS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def hil():
    return load_harness()


@pytest.fixture()
def kiss():
    return load_kiss()


# ---- default mode must touch no device, lock, or network ----

def test_default_mode_freeze_touches_no_device(hil, tmp_path, monkeypatch):
    import fcntl
    import socket

    def explode(*a, **k):
        raise AssertionError("device/lock/network touched in default mode")

    monkeypatch.setattr(fcntl, "flock", explode)
    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setitem(sys.modules, "serial", explode)
    monkeypatch.setattr(hil, "execute", explode)
    monkeypatch.setattr(hil, "bench_flock", explode)
    out = tmp_path / "freeze.json"
    assert hil.main([]) == 0
    assert hil.main(["--freeze-out", str(out)]) == 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["freeze"]["module_level"] == 14
    assert "session_digest" in saved


def test_execute_requires_lease_and_actor(hil, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("execute must not run without a lease")

    monkeypatch.setattr(hil, "execute", explode)
    assert hil.main(["--execute"]) == 2
    assert hil.main(["--execute", "--lease-ack", "x"]) == 2


def test_unexpected_port_refused(hil, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("must not run on an unexpected port")

    monkeypatch.setattr(hil, "execute", explode)
    assert hil.main(["--execute", "--lease-ack", "a", "--trusted-actor", "b",
                     "--port", "/dev/ttyUSB9"]) == 2


# ---- fail-closed lease ACK validation (pure, no hardware) ----

def _ack_payload(digest, actor="tcarwash", age_s=10, ttl_s=600):
    now = time.time()
    return {"issue": 11, "actor": actor, "session_digest": digest,
            "issued_at": now - age_s,  "expires_at": now - age_s + ttl_s,
            "comment_url": "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-999"}


def test_lease_ack_accepts_fresh_exact(hil, tmp_path):
    digest = "ab" * 32
    p = tmp_path / "ack.json"
    p.write_text(json.dumps(_ack_payload(digest)), encoding="utf-8")
    ack = hil.validate_lease_ack(str(p), digest, "tcarwash", 900, time.time())
    assert ack["actor"] == "tcarwash"


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(issue=12),
    lambda a: a.update(actor="someone-else"),
    lambda a: a.update(session_digest="00" * 32),
    lambda a: a.update(issued_at=time.time() - 3600),  # stale
    lambda a: a.update(expires_at=time.time() - 1),  # expired
    lambda a: a.pop("actor"),
])
def test_lease_ack_rejected_fail_closed(hil, tmp_path, mutate):
    digest = "ab" * 32
    payload = _ack_payload(digest)
    mutate(payload)
    p = tmp_path / "ack.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(hil.HilError):
        hil.validate_lease_ack(str(p), digest, "tcarwash", 900, time.time())


def test_lease_ack_unreadable_fails_closed(hil, tmp_path):
    with pytest.raises(hil.HilError):
        hil.validate_lease_ack(str(tmp_path / "missing.json"), "ab" * 32,
                               "tcarwash", 900, time.time())


# ---- harness naming matches the actual repo modules (no made-up API) ----

def test_harness_allowlist_matches_kiss_tool(hil, kiss):
    assert hil.ALLOWED_KISS_OPS == {kiss.LIST, kiss.GET, kiss.SET,
                                    kiss.STATUS, kiss.STOP}
    assert kiss.LIST == 1 and kiss.GET == 2 and kiss.SET == 3
    assert kiss.STATUS == 5 and kiss.STOP == 6
    assert hil.TEMP_SLOT == 30
    assert hil.EXPECTED_MODULE_LEVEL == 14


def test_module_source_level_is_14_without_build():
    sys.path.insert(0, ROOT)
    from modem import k6mod
    assert k6mod.source_level(TNC_SRC) == 14


# ---- exact synthetic KISS LIST/STOP wire behaviour ----

class FakeSerial:
    def __init__(self, responses):
        self.buffer = bytearray(b"".join(responses))
        self.writes = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def read(self, count):
        if not self.buffer:
            raise TimeoutError("test response consumed")
        data = bytes(self.buffer[:count])
        del self.buffer[:count]
        return data


def _encoded_control(kiss, op, status=0, payload=b""):
    return kiss.kiss_frame(kiss.HARDWARE,
                           kiss.PREFIX + bytes([op | 0x80, status]) + payload)


def _decode_one(kiss, wire):
    parser = kiss.KISSDecoder()
    frames = []
    for byte in wire:
        frames += parser.feed(bytes([byte]))
    assert len(frames) == 1
    return frames[0]


def test_synthetic_list_get_set_status_stop_exact(kiss):
    mode_entry = (b"\x01\x00"  # mode id 1, little-endian
                  b"\x01"  # flags: packet-TX only
                  b"\x4b\x00"  # max payload 75
                  b"\x09APRS-1200")  # name
    sentinel = b"\xff\xff\x00\x00\x00\x00"
    s = FakeSerial([_encoded_control(kiss, kiss.LIST, payload=mode_entry),
                    _encoded_control(kiss, kiss.LIST, payload=sentinel)])
    modes = kiss.list_modes(s)
    assert modes == [{"id": 1, "name": "APRS-1200", "flags": 1,
                      "max_payload": 75, "packet_tx": True,
                      "packet_rx": False, "voice": False}]
    assert _decode_one(kiss, s.writes[0]) == (
        bytes([kiss.HARDWARE]) + kiss.PREFIX + bytes([kiss.LIST]))

    s = FakeSerial([_encoded_control(kiss, kiss.GET, payload=b"\x01\x00")])
    assert kiss.get_mode(s) == 1

    s = FakeSerial([_encoded_control(kiss, kiss.SET, payload=b"\x01\x00")])
    assert kiss.set_mode(s, 1) == 1
    assert _decode_one(kiss, s.writes[0]) == (
        bytes([kiss.HARDWARE]) + kiss.PREFIX + bytes([kiss.SET, 1, 0]))

    s = FakeSerial([_encoded_control(kiss, kiss.STATUS,
                                      payload=b"\x00\x00\x00\x00")])
    replies = kiss.request(s, kiss.STATUS, timeout=1)
    assert replies[0]["op"] == kiss.STATUS
    assert replies[0]["payload"] == b"\x00\x00\x00\x00"

    s = FakeSerial([_encoded_control(kiss, kiss.STOP)])
    assert kiss.stop_module(s) is True
    assert _decode_one(kiss, s.writes[0]) == (
        bytes([kiss.HARDWARE]) + kiss.PREFIX + bytes([kiss.STOP]))

    # No DATA (type 0x00) frame may ever be emitted by this sequence.
    for frame in s.writes:
        assert _decode_one(kiss, frame)[0] != 0


def test_harness_write_audit_flags_data(hil, kiss):
    data_frame = kiss.kiss_frame(0, b"\x00" * 17)
    assert hil.describe_kiss_write(data_frame) == "DATA-FORBIDDEN"
    control_frame = kiss.kiss_frame(
        kiss.HARDWARE, kiss.PREFIX + bytes([kiss.LIST]))
    assert hil.describe_kiss_write(control_frame) == "control"


# ---- offline freeze determinism (needs the C toolchain; skip without it) ----

def test_freeze_manifest_offline(hil):
    pytest.importorskip("numpy")
    try:
        manifest, code, entry = hil.freeze()
    except hil.HilError as exc:
        pytest.skip("toolchain unavailable: %s" % exc)
    assert manifest["module_level"] == 14
    assert manifest["module_name"] == "FLUX TNC"
    assert manifest["entry"] == entry and entry % 2 == 0
    assert manifest["code_sha256"] == __import__("hashlib").sha256(
        code).hexdigest()
    again, _, _ = hil.freeze()
    assert hil.session_digest(manifest) == hil.session_digest(again)


def test_github_lease_must_match_trusted_author_and_latest_claim(hil, monkeypatch):
    import subprocess
    from datetime import datetime, timezone
    digest = "ab" * 32
    ack = {"comment_url":
           "https://github.com/tcarwash/flux_radio/issues/11#issuecomment-999"}
    headline = (
        "BENCH ACK | issue=33 | session=kiss-hil-" + digest[:12] +
        " | approval_sha256=" + digest +
        " | operator=AG7SU | scope=whole-bench"
    )
    created = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base = {"id": 999, "user": {"login":"tcarwash"}, "body":headline,
            "created_at":created}
    items = [base]
    class Ret:
        def __init__(self,text):
            self.stdout = text
    def mocked(argv,**kwargs):
        if argv[1:3] == ["api","--paginate"]:
            return Ret(json.dumps(items))
        return Ret(json.dumps(base))
    monkeypatch.setattr(hil.subprocess,"run",mocked)
    assert hil.verify_real_github_lease(ack,digest)
    items.append({"id":1000,"user":{"login":"tcarwash"},
                  "body":"CLAIM | other experiment"})
    with pytest.raises(hil.HilError,match="different"):
        hil.verify_real_github_lease(ack,digest)
    items.pop()
    base["user"]["login"]="someoneelse"
    with pytest.raises(hil.HilError,match="trusted"):
        hil.verify_real_github_lease(ack,digest)


def test_local_ack_without_github_permalink_is_not_authority(hil,tmp_path):
    digest = "cd" * 32
    payload = _ack_payload(digest)
    payload.pop("comment_url")
    file = tmp_path/"forged.json"
    file.write_text(json.dumps(payload))
    with pytest.raises(hil.HilError,match="GitHub comment"):
        hil.validate_lease_ack(str(file),digest,"tcarwash",900,time.time())

@pytest.mark.parametrize("op,payload", [(1,b""),(2,b""),(3,bytes((1,0))),(5,b""),(6,b"")])
def test_auditor_permits_one_exact_flux_control(hil,kiss,op,payload):
    assert hil.describe_kiss_write(kiss.control(op,payload)) == "control"

@pytest.mark.parametrize("raw,classification", [
    (b"\xc0\x00hello\xc0","DATA-FORBIDDEN"),
    (b"\xc0\x06FX\x01\x03\x00\x01\xc0","unsafe-set"), # non-APRS
    (b"\xc0\x06FX\x01\x09\xc0","unsupported-control"),
    (b"\xc0\x06FX\x01\x02\xc0\xc0\x00X\xc0","multi-or-malformed"),
    (b"\xc0\x06FX\x01\x02","unframed"),
    (b"\xc0\x06FX\x01\x02\xc0garbage","unframed"),
    (b"\xc0\x06FX\x01\x04\xc0","unsupported-control"), # CAPS excluded
    (b"\xc0\x06FX\x01\x05\x01\xc0","unexpected-control-payload"),
])
def test_auditor_rejects_all_non_whitelisted_commands(hil,raw,classification):
    assert hil.describe_kiss_write(raw) == classification

def test_hil_freeze_manifest_attests_source_and_host_script(hil):
    manifest,code,entry = hil.freeze()
    assert len(manifest["git_head"])==40
    assert len(manifest["harness_sha256"])==64
    assert manifest["git_policy"].startswith("clean-tracked-tree")
    assert len(manifest["code_sha256"])==64 and manifest["entry"]==entry
    assert code[:4]==b"K6NM"

@pytest.mark.parametrize("args",[
    ["--session-timeout","301"],
    ["--session-timeout","0"],
    ["--lease-max-age-s","901"],
    ["--lease-max-age-s","0"],
])
def test_execute_clamps_unbounded_session_or_lease_windows(hil,monkeypatch,args):
    def no_run(*a,**k):
        raise AssertionError("rejected parameters must never access hardware")
    monkeypatch.setattr(hil,"execute",no_run)
    assert hil.main(["--execute","--lease-ack","x","--trusted-actor","tcarwash",*args])==2
