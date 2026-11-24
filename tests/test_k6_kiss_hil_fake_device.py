"""NO-HARDWARE end-to-end mock tests for the K6 Flux TNC one-shot HIL harness.

OFFLINE: no radio, serial port, SDR, bench lock, flashing, transmit, network,
or GitHub access is touched here. The device (Cable/LiveLink/serial port),
both locks, and the GitHub lease check are all faked; the freeze manifest,
the exact module build (real modem.k6mod), and the KISS wire client (real
tools/k6_kiss_tnc.py) are real.

Covers tools/k6_kiss_hil.py execute path:
* full successful one-shot cycle (snapshot 31 slots / slot 30 empty, exact
  install + readback, one live RUN, KISS LIST/GET/SET/APRS-1200/STATUS/STOP,
  exact-gated uninstall, post-run hash/VFO/idle verification, evidence.json)
* lost-STOP transport failure: no erase, bench HOLD, temp image kept, no retry
* preflight refusals (wrong build / occupied slot 30): never mutate
* default (no --execute) mode touches no hardware

SPDX-License-Identifier: MIT
"""
import hashlib
import importlib.util
import json
import sys
import time
import types
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = str(Path(__file__).resolve().parents[1])
HARNESS_PATH = ROOT + "/tools/k6_kiss_hil.py"
KISS_PATH = ROOT + "/tools/k6_kiss_tnc.py"

PORT = "/dev/ttyUSB0"


def load_harness(name="k6_kiss_hil_fake_device"):
    spec = importlib.util.spec_from_file_location(name, HARNESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_kiss(name="k6_kiss_tnc_fake_device"):
    spec = importlib.util.spec_from_file_location(name, KISS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


KISS = load_kiss()


# ---------------------------------------------------------------- fake device

APRS_MODE = (b"\x01\x00" b"\x01" b"\x4b\x00" b"\x09APRS-1200")
SENTINEL = b"\xff\xff\x00\x00\x00\x00"


def _slot_blob(slot, n=256):
    return (("ORIG%02d:" % slot).encode() * 64)[:n]


class FakeDevice:
    """Stand-in for the radio: KUL2 library slots + live state + KISS mode."""

    def __init__(self, hil, preoccupy_slot30=False, wrong_build=False,
                 legacy_blank=False, caps_wrong=False, caps_missing=False):
        self.hil = hil
        self.build_hex = ("00" * 8) if wrong_build else hil.EXPECTED_BUILD_HEX
        self.legacy_blank = legacy_blank
        self.caps_wrong = caps_wrong
        self.caps_missing = caps_missing
        self.blobs = {s: _slot_blob(s) for s in range(32) if s != 30}
        if preoccupy_slot30:
            self.blobs[30] = _slot_blob(30)
        self.addrs = {s: s * 0x4000 for s in self.blobs}
        self.mode = 1
        self.stop_fail = False
        self.live_cmds = []
        self.cable_calls = []
        self.remove_calls = []
        self.stop_writes = 0
        self.kiss_writes = []

    def snapshot_blobs(self):
        return {s: bytes(b) for s, b in self.blobs.items()}


class FakePort:
    """Fake serial port. Cable/LiveLink fakes bypass its bytes; the real KISS
    client drives write()/read() with synthetically encoded reply frames."""

    def __init__(self, device):
        self.device = device
        self._rx = bytearray()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    baudrate = 115200

    def _reply(self, op, payload=b""):
        return KISS.kiss_frame(KISS.HARDWARE,
                               KISS.PREFIX + bytes([op | 0x80, 0]) + payload)

    def write(self, data):
        data = bytes(data)
        frames = KISS.KISSDecoder().feed(data)
        assert len(frames) == 1, "harness must send one framed KISS op"
        frame = frames[0]
        self.device.kiss_writes.append(frame)
        assert frame[0] != 0x00, "DATA frame must never reach the wire fake"
        assert frame[0] == KISS.HARDWARE and frame[1:4] == KISS.PREFIX
        op, body = frame[4], frame[5:]
        if op == KISS.LIST:
            assert body == b""
            self._rx += self._reply(op, APRS_MODE) + self._reply(op, SENTINEL)
        elif op == KISS.GET:
            assert body == b""
            self._rx += self._reply(op, self.device.mode.to_bytes(2, "little"))
        elif op == KISS.SET:
            assert len(body) == 2
            self.device.mode = int.from_bytes(body, "little")
            self._rx += self._reply(op, body)
        elif op == KISS.STATUS:
            assert body == b""
            self._rx += self._reply(op, b"\x00\x00\x00\x00")
        elif op == KISS.STOP:
            assert body == b""
            self.device.stop_writes += 1
            if self.device.stop_fail:
                raise OSError("fake transport: STOP reply lost")
            self._rx += self._reply(op, b"")
        else:
            raise AssertionError("unexpected KISS op %r" % op)
        return len(data)

    def read(self, count):
        if not self._rx:
            return b""
        out = bytes(self._rx[:count])
        del self._rx[:count]
        return out


class FakeInstallError(Exception):
    pass


class FakeLiveUnavailable(FakeInstallError):
    pass


class FakeSerials:
    def __init__(self, start=None):
        self.n = int(time.time()) if start is None else start

    def next(self):
        self.n += 1
        return self.n

    def advance_after(self, serial):
        self.n = max(self.n, int(serial))


class FakeCable:
    def __init__(self, port, device, log=None, timeout=None):
        self.port = port
        self.device = device
        self._log = log

    def handshake(self, key, wait=20.0, gap=0.5, fresh=True):
        self.device.cable_calls.append("handshake")
        return True

    def info(self):
        self.device.cable_calls.append("info")
        return {"protocol": 5, "slots": 32, "arena": 0,
                "build_id": None if self.device.legacy_blank else bytes.fromhex(self.device.build_hex)}

    def capabilities(self):
        self.device.cable_calls.append("caps")
        return {"format": 1 if self.device.caps_missing else 2,
                "module_api_max": 29,
                "build_id": ("00" * 8) if self.device.caps_wrong else self.device.build_hex}

    def library_directory(self):
        return {s: (self.device.addrs[s], len(self.device.blobs[s]))
                for s in self.device.blobs}

    def slot_head(self, slot):
        if slot not in self.device.blobs:
            return None
        return (b"K6M" + b"\x00" * 13, "ORIG%02d" % slot)

    def library_read(self, slot, n):
        if slot not in self.device.blobs:
            return b"\xff" * n
        blob = self.device.blobs[slot]
        return blob[:n] + b"\xff" * (n - len(blob[:n]))

    def serial_watermark(self):
        return 1000

    def send_image(self, img, xfer):
        self.device.cable_calls.append("send_image")
        self.device.blobs[30] = bytes(img)
        self.device.addrs[30] = 30 * 0x4000
        return (3, 1, 1, len(img))

    def remove_slot(self, slot, key, serial):
        self.device.remove_calls.append(slot)
        if slot != 30:
            raise FakeInstallError("refusing erase of non-temp slot %r" % slot)
        self.device.blobs.pop(slot, None)
        self.device.addrs.pop(slot, None)

    def end(self):
        pass


class FakeLiveLink:
    def __init__(self, port, device, hil, timeout=None):
        self.port = port
        self.device = device
        self.hil = hil

    def info(self):
        return {"protocol": 5, "slots": 32, "arena": 0,
                "build_id": None if self.device.legacy_blank else bytes.fromhex(self.device.build_hex)}

    def module_capabilities(self):
        if self.device.caps_missing:
            raise FakeInstallError("no module CAPS feature")
        return {"format": 2, "module_api_max": 29,
                "build_id": ("00" * 8) if self.device.caps_wrong else self.device.build_hex}

    def status(self):
        return {"transmitting": False, "standby_idle": True,
                "vfos": [{"hz": self.hil.EXPECTED_VFO_A_HZ},
                         {"hz": self.hil.EXPECTED_VFO_B_HZ}]}

    def request(self, cmd, arg=0):
        self.device.live_cmds.append((cmd, arg))
        if cmd == 0x66:  # RUN
            return b"launched"
        raise FakeInstallError("unexpected live cmd %r" % cmd)


def _install_fakes(monkeypatch, hil, device):
    """Replace every hardware/network/lock touchpoint with fakes.

    k6_install / k6_live / k6_port_lock / serial go through sys.modules so
    the harness's own local imports pick them up; locks never engage."""
    k6_install = types.ModuleType("k6_install")
    k6_install.Cable = lambda port, log=None, timeout=None: FakeCable(
        port, device, log=log, timeout=timeout)
    k6_install.Serials = FakeSerials
    k6_install.InstallError = FakeInstallError
    k6_install.CABLE_HANDSHAKE = b"PROGRAMBF-K6BLWF"

    def open_port(a):
        assert a.port == PORT, "harness must stay on the canonical port"
        return FakePort(device)

    k6_install.open_port = open_port

    k6_live = types.ModuleType("k6_live")
    k6_live.LiveLink = lambda port, timeout=None: FakeLiveLink(
        port, device, hil, timeout=timeout)
    k6_live.LiveUnavailable = FakeLiveUnavailable
    k6_live.RUN = 0x66

    k6_port_lock = types.ModuleType("k6_port_lock")
    serial_lock_calls = []

    @contextmanager
    def serial_lock(port, folder=None):
        serial_lock_calls.append(port)
        yield

    k6_port_lock.serial_lock = serial_lock

    serial_mod = types.ModuleType("serial")

    def explode_serial(*a, **k):
        raise AssertionError("real serial opened in a no-hardware test")

    serial_mod.Serial = explode_serial

    monkeypatch.setitem(sys.modules, "k6_install", k6_install)
    monkeypatch.setitem(sys.modules, "k6_live", k6_live)
    monkeypatch.setitem(sys.modules, "k6_port_lock", k6_port_lock)
    monkeypatch.setitem(sys.modules, "serial", serial_mod)

    import fcntl

    def explode_flock(*a, **k):
        raise AssertionError("real flock engaged in a no-hardware test")

    monkeypatch.setattr(fcntl, "flock", explode_flock)
    return serial_lock_calls


def _run_execute(monkeypatch, tmp_path, device, **kw):
    hil = load_harness()
    serial_lock_calls = _install_fakes(monkeypatch, hil, device)
    hold_path = tmp_path / "bench.hold"
    monkeypatch.setattr(hil, "BENCH_HOLD_FILE", str(hold_path))

    bench_calls = []

    @contextmanager
    def fake_bench_flock(path, timeout_s):
        bench_calls.append((path, timeout_s))
        yield object()

    monkeypatch.setattr(hil, "bench_flock", fake_bench_flock)

    gh_calls = []
    monkeypatch.setattr(hil, "verify_real_github_lease",
                        lambda ack, digest: gh_calls.append(digest) or True)

    manifest, _, _ = hil.freeze()
    digest = hil.session_digest(manifest)
    now = time.time()
    ack = {"issue": 11, "actor": "tcarwash", "session_digest": digest,
           "issued_at": now - 5, "expires_at": now + 600,
           "comment_url": "https://github.com/tcarwash/flux_radio/issues/11"
                          "#issuecomment-999"}
    ack_path = tmp_path / "lease-ack.json"
    ack_path.write_text(json.dumps(ack), encoding="utf-8")

    args = SimpleNamespace(
        lease_ack=str(ack_path), trusted_actor="tcarwash",
        lease_max_age_s=900.0, port=PORT,
        bench_lock=str(tmp_path / "bench.lock"),
        private_dir=str(tmp_path / "priv"),
        lock_timeout=10.0, session_timeout=300.0)
    for k, v in kw.items():
        setattr(args, k, v)
    rc = hil.execute(args)
    return hil, rc, bench_calls, gh_calls, serial_lock_calls, hold_path


def _transcript(priv):
    events = []
    for line in (priv / "transcript.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


# ------------------------------------------------------- full successful cycle

def test_successful_one_shot_cycle(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil)
    pre_blobs = device.snapshot_blobs()
    assert len(pre_blobs) == 31 and 30 not in pre_blobs

    hil, rc, bench_calls, gh_calls, serial_lock_calls, hold_path = _run_execute(
        monkeypatch, tmp_path, device)
    assert rc == 0
    assert not hold_path.exists(), "success must not leave a bench HOLD"

    priv = tmp_path / "priv"
    snap = json.loads((priv / "snapshot.json").read_text(encoding="utf-8"))
    assert len(snap["slot_hashes"]) == 31 and "30" not in snap["slot_hashes"]
    for slot, meta in snap["slot_hashes"].items():
        assert meta["sha256"] == hashlib.sha256(pre_blobs[int(slot)]).hexdigest()

    events = _transcript(priv)
    kinds = [e["kind"] for e in events]
    for want in ("freeze", "bench-lock", "serial-lock", "directory", "snapshot",
                 "live-idle", "envelope", "installed", "readback-exact",
                 "run-launched", "kiss-list", "kiss-get", "kiss-set",
                 "kiss-status", "kiss-stop", "live-restored",
                 "uninstall-readback-exact", "uninstalled",
                 "post-slots-verified", "evidence", "done"):
        assert want in kinds, "transcript missing %s" % want

    # Exactly one live RUN, no DATA/PTT/TX anywhere on the wire.
    assert device.live_cmds == [(0x66, 30 | 0x100)]
    assert device.stop_writes == 1, "STOP must be sent exactly once (no retry)"
    assert len(device.kiss_writes) == 5
    for frame in device.kiss_writes:
        assert frame[0] == KISS.HARDWARE
    assert device.mode == 1, "SET APRS-1200 must stick on the fake device"

    # Exact-gated uninstall: slot 30 gone, original 31 blobs bit-identical.
    assert 30 not in device.blobs and device.remove_calls == [30]
    assert device.snapshot_blobs() == pre_blobs

    evidence = json.loads((priv / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["actor"] == "tcarwash"
    assert evidence["build"] == hil.EXPECTED_BUILD_HEX
    assert evidence["api_max"] == hil.EXPECTED_API_MAX
    assert evidence["temp_slot"] == 30
    assert evidence["pre_snapshot_slots"] == 31
    assert evidence["post_slots"] == 31
    assert evidence["vfos_hz"] == [hil.EXPECTED_VFO_A_HZ, hil.EXPECTED_VFO_B_HZ]
    assert evidence["standby_idle"] is True
    assert evidence["transmitting"] is False
    assert evidence["no_kiss_data_written"] is True
    assert evidence["transmitting_not_observed_at_pre_post_samples"] is True
    assert evidence["tx_proof"]["kiss_writes"] == 5
    assert evidence["tx_proof"]["data_frames_sent"] == 0
    assert evidence["tx_proof"]["ptt_api_called"] is False
    env = next(e for e in events if e["kind"] == "envelope")
    assert evidence["envelope_sha256"] == env["sha"]

    assert bench_calls and all(p == str(tmp_path / "bench.lock")
                               for p, _ in bench_calls)
    assert gh_calls, "GitHub lease must be re-checked, never trusted blindly"
    assert serial_lock_calls and all(p == PORT for p in serial_lock_calls)


# --------------------------------------------- lost STOP: HOLD, keep slot 30

def test_lost_stop_reply_prevents_erase_and_holds_bench(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil)
    device.stop_fail = True
    pre_blobs = device.snapshot_blobs()

    hil, rc, _, _, _, hold_path = _run_execute(monkeypatch, tmp_path, device)
    assert rc == 3, "post-install STOP failure must exit 3 (bench HOLD)"
    assert hold_path.exists(), "bench HOLD file must record the failure"
    hold = json.loads(hold_path.read_text(encoding="utf-8"))
    assert "STOP" in hold["reason"]

    events = _transcript(tmp_path / "priv")
    env = next(e for e in events if e["kind"] == "envelope")
    # Known temporary image stays in slot 30; nothing else touched.
    assert 30 in device.blobs
    assert hashlib.sha256(device.blobs[30]).hexdigest() == env["sha"]
    rest = {s: b for s, b in device.snapshot_blobs().items() if s != 30}
    assert rest == pre_blobs
    assert device.remove_calls == [], "erase must never run after lost STOP"
    assert device.stop_writes == 1, "STOP must never be retried"
    assert not (tmp_path / "priv" / "evidence.json").exists()
    assert any(e["kind"] == "ABORT" for e in events)


# ------------------------------------------- preflight refusals never mutate

def test_preflight_wrong_build_never_mutates(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil, wrong_build=True)
    pre_blobs = device.snapshot_blobs()

    hil, rc, _, _, _, hold_path = _run_execute(monkeypatch, tmp_path, device)
    assert rc == 2, "wrong build must abort before any mutation"
    assert not hold_path.exists(), "pre-install abort must not HOLD the bench"
    assert device.snapshot_blobs() == pre_blobs
    assert "send_image" not in device.cable_calls
    assert device.remove_calls == []
    assert 30 not in device.blobs
    assert not (tmp_path / "priv" / "evidence.json").exists()


def test_preflight_occupied_slot30_never_mutates(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil, preoccupy_slot30=True)
    pre_blobs = device.snapshot_blobs()
    assert 30 in pre_blobs

    hil, rc, _, _, _, hold_path = _run_execute(monkeypatch, tmp_path, device)
    assert rc == 2, "occupied slot 30 must abort (never overwrite)"
    assert not hold_path.exists()
    assert device.snapshot_blobs() == pre_blobs
    assert "send_image" not in device.cable_calls
    assert device.remove_calls == []
    assert not (tmp_path / "priv" / "evidence.json").exists()


# --------------------------------------- default mode touches no hardware

def test_default_mode_touches_no_hardware(monkeypatch, tmp_path):
    import fcntl
    import socket

    hil = load_harness()

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


def test_fake_device_imports_harness_from_current_checkout():
    here = Path(__file__).resolve().parents[1]
    assert Path(HARNESS_PATH).resolve() == here / "tools/k6_kiss_hil.py"
    assert Path(KISS_PATH).resolve() == here / "tools/k6_kiss_tnc.py"

def test_missing_legacy_info_build_uses_exact_capability_record(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil, legacy_blank=True)
    hil, rc, _, _, _, hold_path = _run_execute(monkeypatch, tmp_path, device)
    assert rc == 0
    assert not hold_path.exists()
    assert 30 not in device.blobs
    assert device.live_cmds

def test_missing_legacy_info_with_incorrect_capability_refuses_without_install(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil, legacy_blank=True, caps_wrong=True)
    original = device.snapshot_blobs()
    hil, rc, _, _, _, hold_path = _run_execute(monkeypatch, tmp_path, device)
    assert rc == 2 and device.blobs == original
    assert not device.remove_calls and not hold_path.exists()
    assert "send_image" not in device.cable_calls

def test_missing_legacy_info_without_format2_capabilities_refuses(monkeypatch, tmp_path):
    hil = load_harness()
    device = FakeDevice(hil, legacy_blank=True, caps_missing=True)
    original = device.snapshot_blobs()
    hil, rc, _, _, _, hold_path = _run_execute(monkeypatch, tmp_path, device)
    assert rc == 2 and device.blobs == original
    assert "send_image" not in device.cable_calls
    assert not hold_path.exists()
