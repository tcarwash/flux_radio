"""Milestone 1: cable-only CRC32 vs authenticated OTA split (docs/k6-feature-refactor-roadmap.md).

Covers the stabilized split end to end against independently compiled loaders, not
fixtures: the C loader/tag/proof objects are rebuilt from this checkout in both modes
(cable-only -DKM_CABLE_ONLY, signed -DKM_KEY, unkeyed placeholder), the RF vs cable
ingress twins are relinked from the same host objects with different loader flags, and
the pack/standalone installer selection is driven through the real resolve/gate,
receipt, and radio-harness code paths. No serial port, no radio writes, no flashing.

Needs make -C c/k6 km_host kl_host (the signed reference binaries and host objects).
SPDX-License-Identifier: MIT"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from host_link import gc_sections

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from modem import k6mod  # noqa: E402
import k6_install as ki  # noqa: E402

TEST_KEY = bytes(range(16))
WRONG_KEY = bytes(reversed(range(16)))
C6 = ROOT / "c" / "k6"
HOST = C6 / "host"
SIGNED_KL = C6 / "kl_host"
K6FLAGS = ["-std=c99", "-DSOTTO_V36", "-DSOTTO_PITCH_THIRD", "-DFX_NARROW_MUL",
           "-DFX_CHECK", "-DFX_CS512_CONST", "-DFX_YIELD_HOOK",
           "-Wall", "-Wextra", "-Wno-unused-parameter"]

pytestmark = pytest.mark.skipif(
    not (SIGNED_KL.is_file() and (C6 / "km_host").is_file()),
    reason="needs make -C c/k6 km_host kl_host")


@pytest.fixture(autouse=True)
def isolated_key_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("BLOWFANG_KEY", raising=False)
    monkeypatch.delenv("FLUX_KEY", raising=False)


def cc():
    compiler = shutil.which("cc")
    assert compiler, "a host C compiler is required"
    return compiler


# ---- independently compiled loader objects ----------------------------------

PROBE_SRC = r'''
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "k6_mod.c"
static int hex(const char *s, uint8_t *out, unsigned cap) {
  unsigned n = 0;
  while (s[0] && s[1] && n < cap) {
    unsigned v;
    if (sscanf(s, "%2x", &v) != 1) break;
    out[n++] = (uint8_t)v;
    s += 2;
  }
  return (int)n;
}
int main(int argc, char **argv) {
  static uint8_t img[16416];
  int n;
  unsigned code, i;
  int t, p;
  static const uint8_t rec[16] = {'K', '6', 'C', 'P', 2, 9, 23, 0, 1, 2, 3, 4, 5, 6, 7, 8};
  static const uint8_t nonce[16] = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15};
  uint8_t tag[8] = {0};
  if (argc != 2) return 7;
  n = hex(argv[1], img, sizeof img);
  if (n < KM_HDR + KM_TAG) { printf("short\n"); return 7; }
  code = (unsigned)img[4] | ((unsigned)img[5] << 8);
  t = km_tag_check(img, code);
  p = km_capability_proof(rec, nonce, tag);
  printf("tag=%d proof=%d ", t, p);
  for (i = 0; i < 8; i++) printf("%02x", tag[i]);
  printf("\n");
  return 0;
}
'''


@pytest.fixture(scope="module")
def loaders(tmp_path_factory):
    """Fresh C builds of this checkout's loader: cable-only, signed, and unkeyed."""
    work = tmp_path_factory.mktemp("ota_modes")
    src = work / "probe.c"
    src.write_text(PROBE_SRC)
    out = {}

    def build(name, extra):
        exe = work / name
        subprocess.run([cc(), "-O1", "-I" + str(C6), "-I" + str(C6.parent),
                        "-ffunction-sections", "-fdata-sections",
                        gc_sections(), *extra, str(src), "-o", str(exe)],
                       check=True, cwd=str(ROOT))
        return exe

    key_csv = ",".join(str(b) for b in TEST_KEY)
    out["ota"] = build("probe_ota", ["-DKM_KEY=" + key_csv])
    out["cable"] = build("probe_cable", ["-DKM_CABLE_ONLY"])
    out["unkeyed"] = build("probe_unkeyed", [])
    # Raw objects for the placeholder audit (actual compiled artifacts, no fixtures).
    objs = {}
    for name, extra in (("ota_unkeyed", []), ("cable", ["-DKM_CABLE_ONLY"]),
                        ("keyed", ["-DKM_KEY=" + key_csv])):
        obj = work / f"k6_mod_{name}.o"
        subprocess.run([cc(), "-O1", "-std=c99", "-I" + str(C6), "-I" + str(C6.parent),
                        *K6FLAGS, *extra, "-c", str(C6 / "k6_mod.c"), "-o", str(obj)],
                       check=True, cwd=str(ROOT))
        objs[name] = obj.read_bytes()
    out["objects"] = objs
    return out


def probe(exe, image):
    result = subprocess.run([str(exe), image.hex()], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    toks = result.stdout.split()
    return int(toks[0].split("=")[1]), int(toks[1].split("=")[1]), toks[2]


def tiny_code():
    return b"\x70\x47"  # bx lr: returns wherever it is run from


def signed_image(serial, lib=None, **kw):
    return k6mod.image(tiny_code(), 0, serial, TEST_KEY, level=1, lib=lib, **kw)


def crc_image(serial, lib=None, **kw):
    return k6mod.image(tiny_code(), 0, serial, None, level=1, lib=lib, **kw)


def test_signed_unsigned_cross_mode_refusal(loaders):
    signed = signed_image(11)
    unsigned = crc_image(12)
    wrong = k6mod.image(tiny_code(), 0, 13, WRONG_KEY, level=1)
    t, _, _ = probe(loaders["ota"], signed)
    assert t == 0  # the tag checks under the owner key
    t, _, _ = probe(loaders["ota"], unsigned)
    assert t == -4  # signed loader refuses the unsigned CRC32 envelope
    t, _, _ = probe(loaders["ota"], wrong)
    assert t == -4  # signed loader refuses another key
    t, _, _ = probe(loaders["cable"], unsigned)
    assert t == 0
    t, _, _ = probe(loaders["cable"], signed)
    assert t == -4  # cable-only loader refuses the signed envelope
    t, _, _ = probe(loaders["cable"], wrong)
    assert t == -4
    t, _, _ = probe(loaders["unkeyed"], signed)
    assert t == -5  # placeholder build refuses every module


def test_proof_refusal_in_base_and_valid_proof_in_ota(loaders):
    record = bytes([ord("K"), ord("6"), ord("C"), ord("P"), 2, 9, 23, 0,
                    1, 2, 3, 4, 5, 6, 7, 8])
    nonce = bytes(range(16))
    expected = k6mod.siphash24(TEST_KEY, b"K6CAPAU1" + record + nonce).to_bytes(8, "little").hex()
    _, proof, tag_hex = probe(loaders["ota"], signed_image(21))
    assert proof == 0
    assert tag_hex == expected  # C and Python agree on the fixed-domain proof
    _, proof, tag_hex = probe(loaders["cable"], crc_image(22))
    assert proof == -5  # Base answers every proof challenge with KM_NO_KEY
    assert tag_hex == "00" * 8
    _, proof, _ = probe(loaders["unkeyed"], signed_image(23))
    assert proof == -5


def test_placeholders_match_selected_ota_mode(loaders):
    """Exactly one key placeholder on the signed (OTA-capable) object, zero cable-only."""
    assert loaders["objects"]["ota_unkeyed"].count(k6mod.PLACEHOLDER) == 1
    assert loaders["objects"]["cable"].count(k6mod.PLACEHOLDER) == 0
    assert loaders["objects"]["keyed"].count(k6mod.PLACEHOLDER) == 0


# ---- RF vs cable ingress twins (same sources, different loader flags) --------

HOST_OBJS = ["kl_host.o", "k6_lib.o", "cwid.o", "xdmr.o", "xlisten.o",
             "sotto_pager.o", "sotto_fx_dec.o", "sp_cb.o", "sp_vlr.o",
             "fx_math.o", "fx_tables.o"]


@pytest.fixture(scope="module")
def twins(tmp_path_factory):
    missing = [o for o in HOST_OBJS if not (HOST / o).is_file()]
    if missing or not SIGNED_KL.is_file():
        pytest.skip("needs make -C c/k6 km_host kl_host")
    work = tmp_path_factory.mktemp("ota_twins")
    cable_mod = work / "k6_mod_cable.o"
    subprocess.run([cc(), "-O2", "-g", *K6FLAGS, "-I" + str(C6), "-I" + str(C6.parent),
                    "-DKM_CABLE_ONLY", "-c", str(C6 / "k6_mod.c"), "-o", str(cable_mod)],
                   check=True, cwd=str(ROOT))
    cable_host = work / "kl_host_cable.o"
    subprocess.run([cc(), "-O2", "-g", *K6FLAGS, "-I" + str(C6), "-I" + str(C6.parent),
                    "-Dkm_packet=km_cable_packet",
                    "-c", str(C6 / "kl_host.c"), "-o", str(cable_host)],
                   check=True, cwd=str(ROOT))
    base = [str(HOST / o) for o in HOST_OBJS if o not in ("kl_host.o",)]
    # kl_host.o carries the pkt line driver; k6_mod_test.o is the signed loader.
    rf = work / "kl_cable_rf"
    subprocess.run([cc(), str(HOST / "kl_host.o"), str(cable_mod), *base,
                    "-o", str(rf)], check=True)
    cable = work / "kl_cable"
    subprocess.run([cc(), str(cable_host), str(cable_mod), *base,
                    "-o", str(cable)], check=True)
    return SimpleNamespace(rf=rf, cable=cable)


class Lib:
    """One kl_host session: the SPI library behind the cable packet path."""

    def __init__(self, exe):
        self.proc = subprocess.Popen([str(exe)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, bufsize=1)

    def cmd(self, line):
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return self.proc.stdout.readline().strip()

    def pkt(self, image, xfer):
        last = None
        for chunk in k6mod.packets(image, xfer):
            last = self.cmd("pkt " + chunk.hex())
        return last

    @staticmethod
    def fields(line):
        return dict(kv.split("=") for kv in line.split())

    def close(self):
        out, _ = self.proc.communicate("")
        return out


def slot_dump(lib, slot):
    return lib.cmd(f"dump {slot * 0x4000 + 0x110000:x}")


def test_cable_only_valid_crc_accepted(twins):
    lib = Lib(twins.cable)
    try:
        img = crc_image(31)
        info_before = lib.cmd("info 5")
        assert Lib.fields(info_before)["r"] == "-1"  # KL_EMPTY
        got = Lib.fields(lib.pkt(img, 7))
        assert got["r"] == "3" and got["armed"] == "1"  # KM_READY, armed to run
    finally:
        tail = lib.close()
    assert "RULE" not in tail


def test_cable_only_corrupt_envelope_refused(twins):
    lib = Lib(twins.cable)
    try:
        img = bytearray(crc_image(32))
        img[20] ^= 0x01  # damage the code; the CRC32 tag no longer checks
        got = Lib.fields(lib.pkt(bytes(img), 8))
        assert got["r"] == "-4"  # KM_BAD_TAG
        bad = bytearray(k6mod.packets(crc_image(33), 9)[0])
        bad[10] ^= 0x40  # damage the whitened packet itself
        assert Lib.fields(lib.cmd("pkt " + bytes(bad).hex()))["r"] == "-1"  # KM_BAD_CRC
        assert Lib.fields(lib.cmd("info 5"))["r"] == "-1"
    finally:
        tail = lib.close()
    assert "RULE" not in tail


def test_rf_rejected_without_state_mutation(twins):
    lib = Lib(twins.rf)  # cable-only build: km_packet is the RF entry point
    try:
        before_info = lib.cmd("info 5")
        before_dump = slot_dump(lib, 5)
        for xfer in (17, 18):
            got = Lib.fields(lib.pkt(crc_image(34), xfer))
            assert got["r"] == "0"  # KM_NOT_MINE: RF never feeds the cable loader
            assert got["armed"] == "0"
        assert lib.cmd("info 5") == before_info
        assert slot_dump(lib, 5) == before_dump
    finally:
        tail = lib.close()
    assert "RULE" not in tail


def test_signed_ota_accepts_replays_rejected():
    lib = Lib(SIGNED_KL)
    try:
        assert Lib.fields(lib.pkt(signed_image(41), 7))["r"] == "3"
        assert Lib.fields(lib.pkt(signed_image(41), 8))["r"] == "-6"  # KM_OLD replay
        assert Lib.fields(lib.pkt(crc_image(42), 9))["r"] == "-4"  # unsigned refused
        assert Lib.fields(lib.pkt(
            k6mod.image(tiny_code(), 0, 43, WRONG_KEY, level=1), 10))["r"] == "-4"
    finally:
        lib.close()


def test_stored_library_integrity_and_occupied_migration():
    lib = Lib(SIGNED_KL)
    try:
        img = signed_image(51, lib=5)
        got = Lib.fields(lib.pkt(img, 11))
        assert got["install"] == str(len(img))
        assert Lib.fields(lib.cmd("load 5"))["r"] == str(len(tiny_code()))
        head = slot_dump(lib, 5)
        assert head[:len(img) * 2] == img.hex()  # stored image is bit-exact
        assert head[len(img) * 2:] == "ff" * (64 - len(img))  # erased flash behind it
        neighbour = slot_dump(lib, 6)
        lib.cmd(f"flip {0x110000 + 5 * 0x4000 + 20:x}")  # wear one stored byte
        assert Lib.fields(lib.cmd("load 5"))["r"] == "-3"  # KL_BAD_TAG
        assert slot_dump(lib, 6) == neighbour  # nothing else moved
        assert Lib.fields(lib.cmd("info 5"))["r"] == "0"  # header still parses
    finally:
        lib.close()


def test_theme_envelope_never_armed_as_module():
    lib = Lib(SIGNED_KL)
    try:
        blob = b"K6T\x06" + b"\x00" * 12
        img = k6mod.image(blob, 0, 61, TEST_KEY, lib=7, theme=True)
        got = Lib.fields(lib.pkt(img, 12))
        assert got["r"] == "3" and got["armed"] == "0"
        assert got["install"] == "-2"  # kl_install refuses theme decks (KL_BAD_HDR)
        assert Lib.fields(lib.cmd("load 7"))["r"] in ("-1", "-2")
    finally:
        lib.close()


# ---- standalone installer selection -----------------------------------------

def fake_flash(reads):
    class Flash:
        def read(self, address, count):
            for base, data in reads:
                if base <= address < base + len(data):
                    part = data[address - base:address - base + count]
                    return bytes(part).ljust(count, b"\xff")
            return b"\xff" * count
    return Flash()


def auto_entry(tmp_path, name, code=b"\x70\x47"):
    path = tmp_path / f"{name}.bin"
    path.write_bytes(code)
    return {"kind": "module", "id": f"org.example.{name}", "name": name.upper(),
            "path": str(path), "level": 1}


def test_cable_key_selection_and_old_signed_firmware_recap():
    cable_only = object.__new__(ki.Cable)
    mask = 1 | 16  # loader + cable-only; never paired with the OTA bit
    cable_only.call = lambda command: b"K6CP" + bytes([2, mask, 23, 0]) + bytes(range(1, 9))
    assert ki.cable_key(cable_only, TEST_KEY) is None  # no key leaves the machine
    signed = object.__new__(ki.Cable)
    signed.call = lambda command: b"K6CP" + bytes([2, 1 | 8, 23, 0]) + bytes(range(1, 9))
    assert ki.cable_key(signed, TEST_KEY) == TEST_KEY
    legacy = object.__new__(ki.Cable)
    legacy.call = lambda command: b"BLWF" + bytes(12)
    with pytest.raises(ki.InstallError, match="malformed"):
        legacy.capabilities()


def test_module_api_ceiling_follows_firmware_level():
    class Caps:
        def __init__(self, ceiling):
            self.ceiling = ceiling

        def capabilities(self):
            return {"module_api_max": self.ceiling}

    ki.check_module_api(Caps(23), [{"kind": "module", "level": 23}], log=lambda *a, **k: None)
    with pytest.raises(ki.InstallError, match="supports level 22"):
        ki.check_module_api(Caps(22), [{"kind": "module", "level": 23}],
                            log=lambda *a, **k: None)


def test_slot_migration_never_silently_erases(tmp_path):
    entries = [auto_entry(tmp_path, f"m{n}") for n in range(3)]
    planned = ki.allocate_slots(fake_flash([]), entries, {"slots": 32, "arena": 11776}, TEST_KEY)
    assert [e["slot"] for e in planned] == [0, 1, 2]
    # Slot 0 holds unrelated data: the planner steps over it instead of erasing it.
    reads = [(ki.LIB_BASE, b"\x00" * 64)]
    planned = ki.allocate_slots(fake_flash(reads), [auto_entry(tmp_path, "solo")],
                                {"slots": 32, "arena": 11776}, TEST_KEY)
    assert planned[0]["slot"] == 1
    # A full library fails before anything is written.
    with pytest.raises(ki.InstallError, match="no free module slots"):
        ki.allocate_slots(fake_flash([(ki.LIB_BASE - 1, b"\x00" * (32 * ki.LIB_SLOT + 1))]),
                          [auto_entry(tmp_path, "full")],
                          {"slots": 32, "arena": 11776}, TEST_KEY)


def test_auto_update_keeps_identity_and_refuses_foreign_erase_range(tmp_path):
    from modem import k6mod as m

    marker = ki.auto_marker("org.example.solo")
    code = m.NAME_MAGIC + b"SOLO".ljust(m.NAME_LEN, b"\0") + bytes(32)
    old = m.image(code + marker, 0, 71, TEST_KEY, 1, lib=0)
    blob = old + b"\xff" * 8192  # erased flash around the installed image
    reads = [(ki.LIB_BASE, blob)]
    path = tmp_path / "solo.bin"
    path.write_bytes(code)
    entry = {"kind": "module", "id": "org.example.solo", "name": "SOLO",
             "path": str(path), "level": 1}
    planned = ki.allocate_slots(fake_flash(reads), [entry],
                                {"slots": 32, "arena": 11776}, TEST_KEY)
    assert planned[0]["slot"] == 0 and planned[0]["_update"]
    with pytest.raises(ki.InstallError, match="unrelated data"):
        ki.allocate_slots(fake_flash([(ki.LIB_BASE, old + b"\x00" * 8192)]),
                          [dict(entry)], {"slots": 32, "arena": 11776}, TEST_KEY)


# ---- pack capability selection ----------------------------------------------

def spec():
    return json.loads((ROOT / "config" / "k6-feature-packs.json").read_text())


def policy():
    from k6_pack_policy import POLICY

    return json.loads(POLICY.read_text())


def test_pack_gates_select_exact_build_modes():
    from k6_feature_packs import resolve
    from k6_pack_policy import gate_environment, validate

    validate(spec(), policy())
    assert gate_environment(resolve(spec(), ["base"]), policy()) == {
        "K6_SCAN_SERVICE": "0", "K6_FSK_RX_SERVICE": "0", "K6_SOTTO_SERVICE": "0",
        "K6_DESIGNER_SERVICE": "0", "K6_OTA_SERVICE": "0"}
    assert gate_environment(resolve(spec(), ["ota"]), policy())["K6_OTA_SERVICE"] == "1"
    assert gate_environment(resolve(spec(), ["designer"]), policy())["K6_DESIGNER_SERVICE"] == "1"
    scan_data = gate_environment(resolve(spec(), ["scan", "data"]), policy())
    assert scan_data["K6_SCAN_SERVICE"] == "1" and scan_data["K6_FSK_RX_SERVICE"] == "1"
    assert scan_data["K6_OTA_SERVICE"] == "0"  # OTA never leaks into unrelated packs
    sotto = resolve(spec(), ["sotto"])
    assert sotto["packs"] == ["base", "sotto"]
    assert "sotto-decoder" in sotto["capabilities"]
    assert gate_environment(sotto, policy())["K6_SOTTO_SERVICE"] == "1"


def stage_pack(tmp_path, packs, firmware_bytes, level=6):
    from k6_feature_packs import resolve
    from k6_pack_build import assemble
    from k6_build_identity import NOTE
    from k6_capability_receipt import write as write_receipt
    from k6_pack_policy import POLICY, gate_environment

    image = tmp_path / "fw.bin"
    # One aligned GNU build-ID note, like every real linked firmware image carries.
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(NOTE + bytes(range(1, 9)) + bytes(firmware_bytes))

    def build(name, source, folder):
        (folder / "module.bin").write_bytes(name.encode())
        (folder / "manifest.json").write_text(json.dumps(
            {"library": [{"id": f"org.blowfang.k6.{name}", "level": level,
                          "file": "module.bin"}]}))

    root = tmp_path / "pack"
    plan = resolve(spec(), packs)
    lock = assemble(plan, root, image, module_builder=build)
    gates = gate_environment(plan, policy())
    write_receipt(root / "firmware.receipt.json", root / "firmware.bin", plan, gates)
    return root, plan


def test_pack_firmware_placeholder_follows_ota_mode(tmp_path):
    from k6_pack_install import PackError, provision_artifacts

    ota_fw = b"\x00" * 64 + k6mod.PLACEHOLDER + b"\xff" * 64
    root, _ = stage_pack(tmp_path, ["ota"], ota_fw)
    key_file = tmp_path / "k6-mod.key"
    key_file.write_text(TEST_KEY.hex())
    _, _, _, key = provision_artifacts(root, spec(), str(key_file))
    assert key == TEST_KEY
    base_fw = b"\x00" * 128  # cable-only build carries no key placeholder
    root, _ = stage_pack(tmp_path / "base", ["base"], base_fw)
    _, _, _, key = provision_artifacts(root, spec(), None)
    assert key is None
    root, _ = stage_pack(tmp_path / "mismatch", ["ota"], base_fw)
    with pytest.raises(PackError, match="placeholder"):
        provision_artifacts(root, spec(), str(key_file))
    root, _ = stage_pack(tmp_path / "mismatch2", ["base"], ota_fw)
    with pytest.raises(PackError, match="placeholder"):
        provision_artifacts(root, spec(), None)


def test_old_signed_firmware_and_failed_proof_never_fall_back(tmp_path, monkeypatch):
    from k6_pack_install import PackError, provision_pack

    ota_fw = b"\x00" * 64 + k6mod.PLACEHOLDER + b"\xff" * 64
    root, _ = stage_pack(tmp_path, ["ota"], ota_fw)
    key_file = tmp_path / "k6-mod.key"
    key_file.write_text(TEST_KEY.hex())
    monkeypatch.setattr(ki, "open_port", lambda args: SimpleNamespace(close=lambda: None))
    flashed = []

    class DeadCable:
        def __init__(self, mode):
            self.mode = mode

        def capabilities(self):
            if self.mode == "legacy":
                raise ki.InstallError("no CAPS on legacy firmware")
            return ki.Cable.parse_capabilities(
                b"K6CP" + bytes([2, 1 | 8, 23, 0]) + bytes(range(1, 9)))

        def authenticated_capabilities(self, key):
            raise ki.InstallError("capability proof did not match")

        def end(self):
            pass

    monkeypatch.setattr(ki, "flash", lambda *a, **k: flashed.append(a))
    for mode in ("legacy", "proof-fails"):
        monkeypatch.setattr(ki, "open_cable",
                            lambda *a, mode=mode, **k: DeadCable(mode))
        with pytest.raises(PackError):
            provision_pack(root, spec(), "/dev/fake", str(key_file),
                           action="install", log=lambda *a, **k: None)
        with pytest.raises(PackError):
            provision_pack(root, spec(), "/dev/fake", str(key_file),
                           action="flash", mode="update", log=lambda *a, **k: None)
    assert flashed == []  # failed proof authenticates nothing and flashes nothing


def test_pack_report_matches_radio_and_never_self_attests(tmp_path):
    from k6_pack_install import compatibility_report, verify_pack

    ota_fw = b"\x00" * 64 + k6mod.PLACEHOLDER + b"\xff" * 64
    root, _ = stage_pack(tmp_path, ["ota"], ota_fw)
    lock = verify_pack(root, spec(), require_attested=False)
    with pytest.raises(Exception, match="not implemented"):
        verify_pack(root, spec())
    reported = {"module_loader": True, "module_api_max": 23, "ota": True,
                "build_id": lock["firmware"]["sha256"][:16]}
    report = compatibility_report(lock, reported, expected_build_id=None)
    assert report["capabilities_match"] and not report["install_ready"]
    assert report["authenticated"] is False  # an unattested report proves nothing
    legacy = {"module_loader": True, "module_api_max": 22}
    report = compatibility_report(lock, legacy)
    assert not report["capabilities_match"] and "authenticated-ota" in report["missing_capabilities"]
    assert report["unsupported_modules"] == []  # the staged module fits the old ceiling
    narrow = {"module_loader": True, "module_api_max": 5, "ota": True}
    assert compatibility_report(lock, narrow)["unsupported_modules"] == ["vfo"]


# ---- gates match linked builds (including Designer); API/RX/TX preserved -----

def test_capability_gates_match_linked_builds():
    build = (ROOT / "firmware" / "k6-aura" / "build-fd6818b.sh").read_text()
    assert "K6_OTA_SERVICE=${K6_OTA_SERVICE:-0}" in build  # Base defaults to cable-only
    assert "--cfg k6_no_ota" in build
    assert "--cfg k6_designer" in build  # Designer pack selects the linked renderer
    assert "k6_no_fsk_rx" in build and "k6_no_scan" in build
    assert "K6_OTA_SERVICE" in (ROOT / "tools" / "k6_build.sh").read_text()
    assert "-DKM_CABLE_ONLY" in (ROOT / "tools" / "k6_build.sh").read_text()
    query = (ROOT / "firmware" / "k6-aura" / "apply-capability-query.py").read_text()
    assert "(8 if ota == \"1\" else 16)" in query  # OTA and cable-only bits never pair
    ota = (ROOT / "firmware" / "k6-aura" / "apply-ota-service.py").read_text()
    assert "km_cable_packet" in ota and "k6_no_ota" in ota  # RF dispatch stays out of Base
    designer_install = (ROOT / "firmware" / "k6-aura" / "apply-theme-install.py").read_text()
    assert "d[4] = if cfg!(k6_designer) {3} else {2};" in designer_install
    gates = policy()["services"]
    build_envs = set(re.findall(r"K6_[A-Z_]+_SERVICE", build))
    for name, gate in gates.items():
        assert gate["environment"] in build_envs, name
    assert "--cfg k6_no_sotto" in build
    sotto_patch = (ROOT / "firmware" / "k6-aura" / "apply-sotto-service.py").read_text()
    assert "fn sp_packet(" not in sotto_patch and "fn sp_play_pwm(" not in sotto_patch
    assert "not(k6_no_sotto)" in sotto_patch and "poll_library_queue" in sotto_patch


def test_capability_record_format_and_api_ceiling():
    record = b"K6CP" + bytes([2, 1 | 8 | 2 | 4, 23, 0]) + bytes(range(1, 9))
    caps = ki.Cable.parse_capabilities(record)
    assert caps["module_api_max"] == 23  # firmware ceiling, not the old pack cap
    assert caps["ota"] and not caps["cable_only"]
    with pytest.raises(ki.InstallError, match="invalid"):
        ki.Cable.parse_capabilities(b"K6CP" + bytes([2, 1 | 8 | 16, 23, 0]) + bytes(8))
    with pytest.raises(ki.InstallError, match="malformed"):
        ki.Cable.parse_capabilities(b"K6CP" + bytes([3, 1, 23, 0]) + bytes(8))


def test_api_rx_tx_protections_and_modules_preserved():
    header = (C6 / "k6_mod.h").read_text()
    assert "#define KM_ABI 1" in header and "#define KM_LEVEL 23" in header
    assert "0x30 0x33 0x36 0x37" in header  # TX/registry writes stay refused for modules
    assert "tx_on" in header and "rx_hz" in header
    lib_header = (C6 / "k6_lib.h").read_text()
    assert "#define KL_BASE 0x110000u" in lib_header
    assert "#define KL_SLOT_BYTES 0x4000u" in lib_header and "#define KL_SLOTS 32" in lib_header
    for name in ("vfo", "surf", "explore_scan", "cardlink"):
        source = C6 / "mod" / f"{name}.c"
        assert source.is_file(), name
        assert 1 <= k6mod.source_level(str(source)) <= 23
    assert k6mod.source_level(str(C6 / "mod" / "aprsgpssync.c")) == 23  # packs accept it now
