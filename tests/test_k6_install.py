"""tools/k6_install.py against an emulated radio: the cable session's frames (cps::run_cable_session
in the Aura patch) answered by the real module loader and SPI library (c/k6/kl_host, test key
00..0f, an emulated SPI flash), and the stock bootloader's UPDATE protocol. Checks that a
bundle loads into the right slots signed with the owner's key, that a wrong key stops before
anything is written, that pages for another image are skipped, that lost or damaged answers are
sent again, that an image is keyed before it is flashed, and that a keyed image is never bundled.
Needs make -C c/k6 kl_host.
SPDX-License-Identifier: MIT"""
import json
import os
import struct
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.path.join(ROOT, "c", "k6", "kl_host")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import k6_install as ki  # noqa: E402
from modem import k6mod  # noqa: E402

KEY = bytes(range(16))
BUILD_ID = bytes.fromhex("0123456789abcdef")
pytestmark = pytest.mark.skipif(not os.path.exists(HOST), reason="needs make -C c/k6 kl_host")


@pytest.fixture(autouse=True)
def isolated_key_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BLOWFANG_KEY", raising=False)


class Radio:
    """A serial port with an emulated K6 behind it: the cable session (kl_host for the loader
    and the SPI flash), then the bootloader for a moment after each restart"""

    def __init__(self, build_id=BUILD_ID, drop=(), mangle=(), chatter=True,
                 protocol=1, serial_watermark=0):
        self.kl = subprocess.Popen([HOST], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        self.out = b""
        self.inb = b""
        self.state = "app"
        self.build_id = build_id
        self.protocol = protocol
        self.serial_watermark = serial_watermark
        self.drop, self.mangle = list(drop), list(mangle)    # answers to lose / damage, by count
        self.answers = 0
        self.chatter = chatter
        self.flashed = None
        self.flashes = []
        self.boot = {}
        self.restarts = 0
        self.spi = {}                                        # reads outside the library: addr -> 64 B
        self.formatted = False
        self.later = b""

    def kl_cmd(self, line):
        self.kl.stdin.write(line + "\n")
        return self.kl.stdout.readline().strip()

    def close(self):
        pass                                                 # the tool closes its port; kl_host stays

    def shutdown(self):
        """kl_host's last lines: erases= progs= and any RULE it saw broken"""
        out, _ = self.kl.communicate("")
        return out

    def reset_input_buffer(self):
        self.out = b""

    def read(self, n):
        if not self.out and self.later:
            self.out, self.later = self.later, b""           # a restarted image's first lines
        b, self.out = self.out[:n], self.out[n:]
        return b

    def write(self, b):
        self.inb += bytes(b)
        self._run()
        return len(b)

    def _answer(self, cmd, addr, data):
        self.answers += 1
        f = ki.cps_frame(cmd, addr, data)
        if self.answers in self.drop:
            return
        if self.answers in self.mangle:
            f = f[:-1] + bytes([f[-1] ^ 0xFF])
        self.out += f

    def _run(self):
        if self.state == "gate":                             # Aura's FORMAT FLASH? screen: its dump session
            i = self.inb.find(ki.AURA_HANDSHAKE)
            if i >= 0:
                self.inb = self.inb[i + len(ki.AURA_HANDSHAKE):]
                self.out += bytes([ki.ACK])
                self.state = "dump"
            else:
                self.inb = self.inb[-16:]
        if self.state == "dump":
            self._cable()
        if self.state in ("stock", "lean"):
            self.inb = b""                                   # nothing listens
        if self.state == "app":
            i = self.inb.find(ki.CABLE_HANDSHAKE)
            if i >= 0:
                self.inb = self.inb[i + len(ki.CABLE_HANDSHAKE):]
                self.out += b"K6 CABLE on\r\n" + bytes([ki.ACK])
                self.state = "cable"
            else:
                self.inb = self.inb[-16:]
        if self.state == "cable":
            self._cable()
        if self.state == "boot":
            self._boot()

    def _cable(self):
        while True:
            i = self.inb.find(bytes([ki.CPS_HEADER]))
            if i < 0 or len(self.inb) - i < 6:
                return
            n = struct.unpack(">H", self.inb[i + 4:i + 6])[0]
            if len(self.inb) - i < 8 + n:
                return
            f, self.inb = self.inb[i:i + 8 + n], self.inb[i + 8 + n:]
            cmd, addr = f[1], struct.unpack(">H", f[2:4])[0]
            data = f[6:6 + n]
            if ki.crc16_xmodem(f[1:6 + n]) != struct.unpack(">H", f[6 + n:])[0]:
                self._answer(ki.CMD_ERROR, addr, bytes([ki.ERR_BAD_CRC]))
            elif self.state == "dump" and cmd not in (ki.CMD_READ, ki.CMD_END):
                self._answer(ki.CMD_ERROR, addr, bytes([3]))
            elif cmd == ki.CMD_INFO:
                self._answer(cmd, addr, b"BLWF" + bytes([self.protocol, 32]) + struct.pack("<H", 11776) + self.build_id)
            elif cmd == ki.CMD_SERIAL:
                self._answer(cmd, addr, struct.pack("<I", self.serial_watermark))
            elif cmd == ki.CMD_CHUNK:
                w = dict(kv.split("=") for kv in self.kl_cmd("pkt " + data.hex()).split())
                r, put = int(w["r"]), int(w.get("install", 0))
                if self.chatter and "install" in w:
                    self.out += f"K6 LIB put slot=? r={put}\r\n".encode()
                self._answer(cmd, addr, struct.pack("<bBBBi", r, 0, 0, 0, put))
            elif cmd == ki.CMD_READ:
                d = self.spi.get(addr * 64) or bytes.fromhex(self.kl_cmd(f"dump {addr * 64:x}"))
                self._answer(cmd, addr, d)
            elif cmd == ki.CMD_END and self.state == "dump":
                self._answer(cmd, addr, b"")
                self.state = "gate"
                return
            elif cmd == ki.CMD_END:
                self._answer(cmd, addr, b"")
                self.out += b"K6 CABLE end\r\n"
                self.state, self.restarts = "boot", self.restarts + 1
                return

    def _boot(self):
        while True:
            i = self.inb.find(b"\xaa")
            if i < 0 or len(self.inb) - i < 5:
                return
            n = self.inb[i + 3] << 8 | self.inb[i + 4]
            if len(self.inb) - i < 8 + n:
                return
            f, self.inb = self.inb[i:i + 8 + n], self.inb[i + 8 + n:]
            assert ki.crc16_xmodem(f[1:5 + n]) == (f[5 + n] << 8 | f[6 + n]) and f[-1] == 0xEF
            cmd, arg, data = f[1], f[2], f[5:5 + n]
            if cmd == 0x42:
                self.out += ki.boot_frame(0x42)
                continue
            if cmd == ki.BOOT_HELLO:
                assert data == b"BOOTLOADER"
            elif cmd == ki.BOOT_COUNT:
                self.boot = {"n": data[0]}
            elif cmd == ki.BOOT_DATA:
                assert len(data) == ki.BOOT_PACKAGE
                self.boot[arg] = data
            elif cmd == ki.BOOT_END:
                self.flashed = b"".join(self.boot[k] for k in range(self.boot["n"]))
                self.flashes.append(self.flashed)
                self.state = "app"
                if b"CBI-IMAGE" in self.flashed:             # the codebook installer's first boot
                    self.state = "lean" if self.formatted else "gate"
                    if self.formatted:
                        self.later += b"K6 CB r=2 mode=2 crc=d5856ecd ms=566.4\r\n"
                        self.spi[0x100000] = b"K6CB".ljust(64, b"\0")
                elif b"VLRI-IMAGE" in self.flashed:
                    self.state = "lean"
                    self.later += b"K6 VLR r=2 crc=0 ms=1.0\r\n"
                    self.spi[0x108000] = b"K6VL".ljust(64, b"\0")
            self.out += ki.boot_frame(cmd, ki.ACK)


def code_named(name, n, seed):
    import random
    body = bytes(random.Random(seed).randrange(256) for _ in range(n))
    return k6mod.NAME_MAGIC + name.encode().ljust(k6mod.NAME_LEN, b"\0") + body


@pytest.fixture
def out_dir(tmp_path):
    """build.sh's out/ as far as a bundle needs it: an unkeyed image holding its build ID, four
    pages linked for it, a help deck, and two modules (made-up code, real names and levels)"""
    d = tmp_path / "out"
    d.mkdir()
    img = b"\x00" * 1000 + k6mod.PLACEHOLDER + b"\x11" * 500 + BUILD_ID + b"\x22" * 3000
    (d / "aura-voice-explore.bin").write_bytes(img)
    for i, page in enumerate(("table", "sweep", "listen", "edit")):
        (d / f"page-{page}.bin").write_bytes(bytes([0x4B, 0x50, i + 1, 8]) + bytes(range(256)) * (2 + i) + BUILD_ID)
    (d / "cards-core.bin").write_bytes(b"K6HC" + bytes(range(256)) * 9)
    (d / "mod-surf.bin").write_bytes(code_named("SURF", 3000, 1))
    (d / "mod-gw.bin").write_bytes(code_named("GROUNDWAVE", 11747 - 16, 2))   # gw's size: 203 L chunks
    (d / "build-id.txt").write_text("abc1234-20261114T1300Z\n")
    mods = tmp_path / "mods.txt"
    mods.write_text("# test set\n0 surf\n7 gw\n")
    return d, mods


@pytest.fixture
def bundle(out_dir, tmp_path):
    d, mods = out_dir
    b = tmp_path / "bundle"
    assert ki.main(["bundle", str(d), "-o", str(b), "--modules", str(mods)], log=lambda *x, **k: None) == 0
    return b


@pytest.fixture
def keyfile(tmp_path):
    k = tmp_path / "k6-mod.key"
    k.write_text(KEY.hex() + "\n")
    return k


def run(argv, radio, monkeypatch, answers=()):
    lines = []
    monkeypatch.setattr(ki, "open_port", lambda a: radio)
    it = iter(answers)
    r = ki.main(argv, log=lambda *x, **k: lines.append(" ".join(map(str, x))), ask=lambda q: next(it, ""))
    return r, "\n".join(lines)


def slot_info(radio, n):
    return dict(kv.split("=") for kv in radio.kl_cmd(f"info {n}").split())


def test_auto_slot_installs_and_updates_same_identity(tmp_path):
    radio = Radio()
    cable = ki.Cable(radio, lambda *a, **k: None)
    assert cable.handshake(ki.CABLE_HANDSHAKE)
    path = tmp_path / "module.bin"
    path.write_bytes(code_named("SURF", 128, 1))
    entry = {"kind": "module", "id": "org.example.surf", "name": "SURF",
             "path": str(path), "level": 1}
    bundle = {"build_id": BUILD_ID.hex(), "library": [entry]}
    serials = ki.Serials()
    try:
        ki.load(cable, bundle, KEY, serials, log=lambda *a, **k: None, verify=True)
        assert slot_info(radio, 0)["r"] == "0"
        path.write_bytes(code_named("SURF", 160, 2))
        ki.load(cable, bundle, KEY, serials, log=lambda *a, **k: None, verify=True)
        assert slot_info(radio, 0)["r"] == "0"
        assert slot_info(radio, 1)["r"] == "-1"
    finally:
        radio.shutdown()


def test_installer_serials_advance_past_radio_replay_watermark():
    serials = ki.Serials(start=100)
    serials.advance_after(250)
    assert serials.next() == 251
    serials.advance_after(200)
    assert serials.next() == 252


def test_capability_record_remains_wire_compatible():
    record = b'K6CP' + bytes([2, 0x11, 23, 0]) + bytes.fromhex('0102030405060708')
    caps = ki.Cable.parse_capabilities(record)
    assert caps['cable_only'] is True
    assert caps['module_api_max'] == 23


def test_load_uses_radio_replay_watermark(tmp_path):
    radio = Radio(protocol=3, serial_watermark=3_000_000_000)
    cable = ki.Cable(radio, lambda *a, **k: None)
    assert cable.handshake(ki.CABLE_HANDSHAKE)
    path = tmp_path / "module.bin"
    path.write_bytes(code_named("TEST", 64, 4))
    bundle = {"build_id": BUILD_ID.hex(), "library": [
        {"kind": "module", "slot": 0, "name": "TEST", "level": 1, "path": str(path)}]}
    try:
        ki.load(cable, bundle, KEY, ki.Serials(start=10), log=lambda *a, **k: None, verify=True)
        fields = dict(kv.split("=") for kv in radio.kl_cmd("info 0").split())
        assert int(fields["serial"]) == 3_000_000_002
    finally:
        radio.shutdown()


def test_auto_slots_never_overwrite_unknown_or_reserved_slots(tmp_path):
    class Flash:
        def read(self, address, count):
            slot = (address - ki.LIB_BASE) // ki.LIB_SLOT
            return (b"unknown" + b"\xff" * 57)[:count] if slot == 0 else b"\xff" * count

    path = tmp_path / "module.bin"
    path.write_bytes(code_named("TEST", 16, 1))
    entries = [{"kind": "module", "id": f"org.example.{n}", "path": str(path)} for n in range(10)]
    planned = ki.allocate_slots(Flash(), entries, {"slots": 32, "arena": 11776}, KEY)
    assert [e["slot"] for e in planned] == list(range(1, 9)) + [16, 17]
    assert all("slot" not in e for e in entries)


def test_auto_slots_full_fail_before_any_install(tmp_path):
    class Flash:
        def read(self, address, count):
            return b"\x00" * count

    path = tmp_path / "module.bin"
    path.write_bytes(code_named("TEST", 16, 1))
    with pytest.raises(ki.InstallError, match="no free module slots"):
        ki.allocate_slots(Flash(), [{"kind": "module", "id": "org.example.test", "path": str(path)}],
                          {"slots": 32, "arena": 11776}, KEY)


def test_auto_slot_rejects_hidden_data_in_erase_range(tmp_path):
    class Flash:
        def read(self, address, count):
            slot = (address - ki.LIB_BASE) // ki.LIB_SLOT
            data = bytearray(b"\xff" * count)
            if slot == 0 and count > 64:
                data[100] = 0
            return bytes(data)

    path = tmp_path / "module.bin"
    path.write_bytes(code_named("TEST", 16, 1))
    result = ki.allocate_slots(Flash(), [{"kind": "module", "id": "org.example.test", "path": str(path)}],
                               {"slots": 32, "arena": 11776}, KEY)
    assert result[0]["slot"] == 1


def test_auto_update_refuses_to_erase_unrelated_data(tmp_path):
    marker = ki.auto_marker("org.example.test")
    old = k6mod.image(code_named("TEST", 16, 1) + marker, 20, 1, KEY, 1, lib=0)
    class Flash:
        def read(self, address, count):
            slot = (address - ki.LIB_BASE) // ki.LIB_SLOT
            data = bytearray(b"\xff" * count)
            if slot == 0:
                data[:min(len(old), count)] = old[:count]
                if count > 4096:
                    data[4096] = 0
            return bytes(data)
    path = tmp_path / "module.bin"
    path.write_bytes(code_named("TEST", 5000, 1))
    with pytest.raises(ki.InstallError, match="unrelated data"):
        ki.allocate_slots(Flash(), [{"kind": "module", "id": "org.example.test", "path": str(path)}],
                          {"slots": 32, "arena": 11776}, KEY)


def test_module_bundle_cli_packages_slot_independent_binary(tmp_path):
    source = tmp_path / "mod.bin"
    source.write_bytes(code_named("SURF", 32, 1))
    dest = tmp_path / "bundle"
    assert ki.main(["module-bundle", str(source), "--id", "org.example.surf", "--level", "9",
                    "-o", str(dest)], log=lambda *a, **k: None) == 0
    entry = ki.load_bundle(str(dest))["library"][0]
    assert entry["id"] == "org.example.surf" and entry["level"] == 9
    assert entry.get("slot") is None
    assert open(entry["path"], "rb").read() == source.read_bytes()


def test_compiled_module_bundle_installs_via_emulated_cable(tmp_path):
    bundle_dir = tmp_path / "bundle"
    source = os.path.join(ROOT, "c/k6/mod/morse.c")
    assert ki.main(["module-bundle", source, "--id", "org.blowfang.k6.morse", "-o", str(bundle_dir)],
                   log=lambda *a, **k: None) == 0
    bundle = ki.load_bundle(str(bundle_dir))
    radio = Radio()
    cable = ki.Cable(radio, lambda *a, **k: None)
    assert cable.handshake(ki.CABLE_HANDSHAKE)
    try:
        ki.load(cable, bundle, KEY, ki.Serials(), log=lambda *a, **k: None)
        assert slot_info(radio, 0)["r"] == "0"
        assert bytes.fromhex(slot_info(radio, 0)["name"]).decode() == "MORSE"
    finally:
        radio.shutdown()


def test_frames_match_aura():
    # Aura's cps::frame::encode: A6, cmd, addr BE, len BE, data, CRC-16/XMODEM over cmd..data BE
    f = ki.cps_frame(0x44, 0x0102, b"")
    assert f[:6] == bytes([0xA6, 0x44, 0x01, 0x02, 0x00, 0x00])
    assert struct.unpack(">H", f[6:])[0] == ki.crc16_xmodem(f[1:6])
    assert ki.crc16_xmodem(b"123456789") == 0x31C3


def test_bundle(bundle):
    m = json.load(open(bundle / "manifest.json"))
    assert m["build_id"] == BUILD_ID.hex()
    slots = {e["slot"]: e for e in m["library"]}
    assert sorted(slots) == [0, 7, 9, 10, 11, 12, 13]
    assert slots[0]["name"] == "SURF" and slots[0]["level"] == k6mod.source_level(os.path.join(ROOT, "c/k6/mod/surf.c"))
    assert slots[9]["kind"] == "page" and slots[13]["kind"] == "cards"
    assert (bundle / "NOTICE.txt").exists()
    assert not m["codebook_images"]


def test_bundle_refuses_a_keyed_image(out_dir, tmp_path):
    d, mods = out_dir
    img = (d / "aura-voice-explore.bin").read_bytes()
    (d / "aura-voice-explore.bin").write_bytes(k6mod.key_image(img, KEY))
    lines = []
    assert ki.main(["bundle", str(d), "-o", str(tmp_path / "b"), "--modules", str(mods)],
                   log=lambda *x, **k: lines.append(str(x))) == 1
    assert "never a keyed one" in "".join(lines)


def test_load(bundle, keyfile, monkeypatch):
    radio = Radio()
    r, log = run(["--key-file", str(keyfile), "load", str(bundle), "--verify"], radio, monkeypatch)
    assert r == 0, log
    for slot, flags, name in ((9, 3, ""), (10, 3, ""), (13, 5, ""), (0, 1, "SURF"), (7, 1, "GROUNDWAVE")):
        i = slot_info(radio, slot)
        assert i["r"] == "0" and int(i["flags"]) == flags, (slot, i)
        assert bytes.fromhex(i["name"]).decode() == name
    assert slot_info(radio, 1) == {"r": "-1"}
    # the pages first, then the deck, then the modules (EXPLORE can start as soon as possible)
    order = [ln.split()[1] for ln in log.splitlines() if ln.strip().startswith("slot ")]
    assert order == ["9", "10", "11", "12", "13", "0", "7"]
    assert "K6: K6 LIB put" in log                           # the radio's lines between frames
    tail = radio.shutdown()
    assert "RULE" not in tail and "erases=" in tail


def test_wrong_key_writes_nothing(bundle, tmp_path, monkeypatch):
    k = tmp_path / "other.key"
    k.write_text("ff" * 16 + "\n")
    radio = Radio()
    r, log = run(["--key-file", str(k), "load", str(bundle)], radio, monkeypatch)
    assert r == 1 and "another key" in log
    for slot in (0, 7, 9, 13):
        assert slot_info(radio, slot) == {"r": "-1"}
    radio.shutdown()


def test_pages_for_another_image_are_skipped(bundle, keyfile, monkeypatch):
    radio = Radio(build_id=bytes(8))
    r, log = run(["--key-file", str(keyfile), "load", str(bundle)], radio, monkeypatch)
    assert r == 0 and "skipping EXPLORE's pages" in log
    assert slot_info(radio, 9) == {"r": "-1"}
    assert slot_info(radio, 0)["r"] == "0" and slot_info(radio, 13)["r"] == "0"
    radio.shutdown()


def test_lost_and_damaged_answers_are_sent_again(bundle, keyfile, monkeypatch):
    monkeypatch.setattr(ki, "TIMEOUT", 0.3)
    radio = Radio(drop=(5, 40), mangle=(9, 77))
    r, log = run(["--key-file", str(keyfile), "load", str(bundle), "--only", "0,13", "--verify"], radio, monkeypatch)
    assert r == 0, log
    assert slot_info(radio, 0)["r"] == "0" and slot_info(radio, 13)["r"] == "0"
    radio.shutdown()


def test_lost_answer_to_the_last_chunk(bundle, keyfile, monkeypatch):
    """the install happened but its answer was lost: the resent last chunk starts a transfer of
    its own, and the slot read back shows the install is good"""
    monkeypatch.setattr(ki, "TIMEOUT", 0.3)
    surf = os.path.getsize(bundle / "mod-surf.bin") + 24
    chunks = -(-surf // 58)
    radio = Radio(drop=(2 + chunks,))        # info, the key probe (1 chunk), then surf's chunks
    r, log = run(["--key-file", str(keyfile), "load", str(bundle), "--only", "0"], radio, monkeypatch)
    assert r == 0, log
    assert slot_info(radio, 0)["r"] == "0"
    # the last chunk went twice, then the slot was read back (16 + 3,000 + 8 B: 48 reads), then end
    assert radio.answers == 2 + chunks + 1 + 48 + 1
    radio.shutdown()


def test_flash_creates_default_key_and_uses_it(bundle, tmp_path, monkeypatch):
    radio = Radio()
    image = (bundle / "aura-voice-explore.bin").read_bytes()
    try:
        result, log = run(["flash", str(bundle / "aura-voice-explore.bin")], radio, monkeypatch)
        assert result == 0, log
        key_path = tmp_path / ".config/flux/k6-mod.key"
        key = k6mod.read_key(key_path)
        expected = k6mod.key_image(image, key)
        assert radio.flashed[:len(expected)] == expected
        assert key_path.stat().st_mode & 0o777 == 0o600
    finally:
        radio.shutdown()


def test_flash_over_the_cable(bundle, keyfile, monkeypatch):
    radio = Radio()
    img = (bundle / "aura-voice-explore.bin").read_bytes()
    r, log = run(["--key-file", str(keyfile), "flash", str(bundle / "aura-voice-explore.bin")], radio, monkeypatch)
    assert r == 0, log
    assert radio.restarts == 1
    want = k6mod.key_image(img, KEY)
    assert radio.flashed[:len(want)] == want and not any(radio.flashed[len(want):])
    assert k6mod.PLACEHOLDER not in radio.flashed and KEY in radio.flashed
    radio.shutdown()


@pytest.mark.parametrize("start", ["stock", "fresh"])
def test_install_on_a_new_radio(start, out_dir, keyfile, tmp_path, monkeypatch):
    """stock firmware (or blowfang with --fresh): the two codebook installers and the core image,
    each caught as the owner switches the radio on, the backup at the first boot's FORMAT FLASH?,
    then the library"""
    d, mods = out_dir
    (d / "aura-pager-cbi.bin").write_bytes(b"CBI-IMAGE" + k6mod.PLACEHOLDER + bytes(2000))
    (d / "aura-pager-vlri.bin").write_bytes(b"VLRI-IMAGE" + k6mod.PLACEHOLDER + bytes(2000))
    b = tmp_path / "bundle"
    assert ki.main(["bundle", str(d), "-o", str(b), "--modules", str(mods)], log=lambda *x, **k: None) == 0
    monkeypatch.setattr(ki, "CHIP_BYTES", 0x10000)
    monkeypatch.setattr(ki.time, "sleep", lambda t: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    radio = Radio()
    if start == "stock":
        radio.state = "stock"
    asked = []

    def ask(q):
        asked.append(q)
        if "switch it ON" in q:
            radio.state, radio.inb = "boot", b""
        if "press MENU" in q:
            radio.formatted = True
            radio.state = "lean"
            radio.out += b"K6 CB r=2 mode=2 crc=d5856ecd ms=566.4\r\n"
            radio.spi[0x100000] = b"K6CB".ljust(64, b"\0")
        return "YES" if "YES" in q else ""

    monkeypatch.setattr(ki, "open_port", lambda a: radio)
    lines = []
    extra = ["--fresh"] if start == "fresh" else []
    r = ki.main(["--key-file", str(keyfile), "install", str(b)] + extra,
                log=lambda *x, **k: lines.append(" ".join(map(str, x))), ask=ask)
    log = "\n".join(lines)
    assert r == 0, log
    assert [f[:4] for f in radio.flashes] == [b"CBI-", b"VLRI", b"\0\0\0\0"]
    assert all(KEY in f and k6mod.PLACEHOLDER not in f for f in radio.flashes)
    assert sum("switch it ON" in q for q in asked) == 3 and any("press MENU" in q for q in asked)
    backups = os.listdir(tmp_path / "flux-backups")
    assert len(backups) == 1 and os.path.getsize(tmp_path / "flux-backups" / backups[0]) == 0x10000
    assert slot_info(radio, 9)["r"] == "0" and slot_info(radio, 7)["r"] == "0"
    assert radio.restarts == 1                               # the session's end: into EXPLORE
    radio.shutdown()


@pytest.mark.parametrize("why", ["have one", "--no-backup"])
def test_update_needs_no_hands(why, bundle, keyfile, tmp_path, monkeypatch):
    monkeypatch.setattr(ki.time, "sleep", lambda t: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    if why == "have one":
        (tmp_path / "blowfang-backups").mkdir()
        (tmp_path / "blowfang-backups" / "k6-spiflash-20261114T0000Z.bin").write_bytes(b"")
    radio = Radio()
    radio.spi[0x100000] = b"K6CB".ljust(64, b"\0")
    radio.spi[0x108000] = b"K6VL".ljust(64, b"\0")
    asked = []
    monkeypatch.setattr(ki, "open_port", lambda a: radio)
    lines = []
    extra = ["--no-backup"] if why == "--no-backup" else []
    r = ki.main(["--key-file", str(keyfile), "install", str(bundle)] + extra,
                log=lambda *x, **k: lines.append(" ".join(map(str, x))), ask=lambda q: asked.append(q) or "")
    log = "\n".join(lines)
    assert r == 0, log
    assert ("No backup (--no-backup)" in log) == (why == "--no-backup")
    assert ("An existing SPI backup was found" in log) == (why == "have one")
    assert (tmp_path / "blowfang-backups").exists() == (why == "have one")
    assert not asked and len(radio.flashes) == 1 and radio.restarts == 2
    assert slot_info(radio, 10)["r"] == "0"
    radio.shutdown()


def test_backup(keyfile, tmp_path, monkeypatch):
    monkeypatch.setattr(ki, "CHIP_BYTES", 0x20000)
    radio = Radio()
    radio.kl_cmd("flip 0f010")                               # something to find in the calibration sector
    out = tmp_path / "backup.bin"
    r, log = run(["backup", "-o", str(out)], radio, monkeypatch)
    assert r == 0, log
    data = out.read_bytes()
    assert len(data) == 0x20000 and data[0xF010] == 0xFE and data.count(0xFF) == len(data) - 1
    radio.shutdown()


def test_flash_pack_uses_verified_image_and_bootloader(tmp_path, monkeypatch):
    import k6_pack_install as packs

    radio = Radio()
    radio.state = 'boot'
    image = b'pack firmware' + k6mod.PLACEHOLDER + bytes(1500)
    monkeypatch.setattr(packs, 'provision_artifacts',
                        lambda *_: ({'packs': ['base']}, image, BUILD_ID.hex(), KEY))
    monkeypatch.setattr(ki, 'open_port', lambda _: radio)
    try:
        packs.provision_pack(tmp_path, {}, 'test-port', action='flash', mode='update',
                             log=lambda *args, **kwargs: None)
        expected = k6mod.key_image(image, KEY)
        assert radio.flashed[:len(expected)] == expected
        assert not any(radio.flashed[len(expected):])
    finally:
        radio.shutdown()


def test_fast_read_fixed_rate_backend(monkeypatch):
    from k6_install import Cable, CpsLink
    class FixedRatePort:
        pass
    class BaudOnlyPort:
        baudrate = 115200
    expected = bytes(8192)
    def baseline(self, addr, n, window=8, progress=None):
        assert (addr, n) == (0x110000, 8192)
        return expected
    def forbidden(*args, **kwargs):
        raise AssertionError("must not probe fast transport on fixed-rate backend")
    monkeypatch.setattr(CpsLink, "read", baseline)
    monkeypatch.setattr(Cable, "calls", forbidden)
    for port in (FixedRatePort(), BaudOnlyPort()):
        assert Cable(port).read(0x110000, 8192) == expected
