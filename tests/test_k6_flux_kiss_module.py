"""Flux mode-aware KISS TNC module (c/k6/mod/fluxtnc.c, flux_kiss_mux.h).

Host-unit tests: the C KISS parser, Flux SET_HARDWARE encode/parse
helpers, the DATA TX gate (malformed/oversized frames and unselected modes
must never key), the APRS Bell 202 bitstream via ap_tx_encode, and the real
Thumb relocatable module build (modem.k6mod.build: clang -fropi, two
addresses, byte-identical).

OFFLINE: no radio, serial, SDR, flash or transmit is touched here.
SPDX-License-Identifier: MIT"""
import ctypes as C
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD = os.path.join(ROOT, "c", "k6", "mod")
MUX = os.path.join(MOD, "flux_kiss_mux.h")
TNC = os.path.join(MOD, "fluxtnc.c")

import sys
sys.path.insert(0, ROOT)
from modem import aprs, k6mod  # noqa: E402

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
NEED, FRAME, DROPPED = 0, 1, -1

SHIM = r'''
#include <stdint.h>
#include "flux_kiss_mux.h"
#include "aprs_tx.h"
static fluxk_rx_t g_rx;
static unsigned g_type;
static uint8_t g_pay[80];
static unsigned g_n;
void rx_reset(void) { fluxk_rx_reset(&g_rx); }
int rx_feed(unsigned byte) {
  return fluxk_rx_feed(&g_rx, (uint8_t)byte, &g_type, g_pay, &g_n);
}
unsigned rx_type(void) { return g_type; }
unsigned rx_len(void) { return g_n; }
uint8_t rx_byte(unsigned i) { return i < (unsigned)sizeof g_pay ? g_pay[i] : 0; }
int hz_ok(uint32_t hz) { return fluxk_hz_ok(hz); }
int data_tx_ok(unsigned cur, unsigned n, int afsk) {
  return fluxk_data_tx_ok((uint16_t)cur, n, afsk);
}
unsigned fx_parse(const uint8_t *p, unsigned n, unsigned *op, unsigned *alen) {
  return fluxk_fx_parse(p, n, op, alen);
}
unsigned sethw_validate(unsigned op, unsigned alen) {
  return fluxk_sethw_validate(op, alen);
}
unsigned resp_hdr(uint8_t *o, unsigned op, unsigned st) {
  return fluxk_resp_hdr(o, op, st);
}
unsigned list_body(uint8_t *o, unsigned id, unsigned fl, unsigned maxp,
                   const char *name, unsigned nl) {
  return fluxk_list_body(o, (uint16_t)id, (uint8_t)fl, (uint16_t)maxp, name, nl);
}
unsigned kiss_encode(uint8_t *out, unsigned cap, unsigned type,
                     const uint8_t *p, unsigned n) {
  return fluxk_kiss_encode(out, cap, type, p, n);
}
unsigned n_backends(void) { return fluxk_n_backends(); }
int backend(unsigned idx, unsigned *id, unsigned *fl, unsigned *maxp,
            unsigned *nl, char *name) {
  const fluxk_backend_t *b;
  unsigned i;
  if (idx >= fluxk_n_backends())
    return 0;
  b = &fluxk_backends[idx];
  *id = b->mode_id;
  *fl = b->flags;
  *maxp = b->max_payload;
  *nl = b->name_len;
  for (i = 0; i < 12; i++)
    name[i] = b->name[i];
  return 1;
}
int aprs_encode(const uint8_t *f, unsigned n, uint8_t *bits, unsigned *nbits) {
  ap_tx_t t;
  unsigned nb, i;
  int r = ap_tx_encode(&t, f, n);
  nb = (unsigned)((t.bits + 7) / 8);
  if (nb > 128)
    nb = 128;
  for (i = 0; i < nb; i++)
    bits[i] = t.data[i];
  *nbits = t.bits;
  return r;
}
'''


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("host cc required")
    d = tmp_path_factory.mktemp("fluxtnc")
    c = d / "shim.c"
    so = d / "shim.so"
    c.write_text(SHIM)
    subprocess.run(["cc", "-O2", "-shared", "-fPIC", "-I" + MOD,
                    str(c), "-o", str(so)], check=True)
    l = C.CDLL(str(so))
    l.rx_feed.argtypes = [C.c_uint]
    l.rx_feed.restype = C.c_int
    l.rx_type.restype = C.c_uint
    l.rx_len.restype = C.c_uint
    l.rx_byte.argtypes = [C.c_uint]
    l.rx_byte.restype = C.c_uint8
    l.hz_ok.argtypes = [C.c_uint32]
    l.hz_ok.restype = C.c_int
    l.data_tx_ok.argtypes = [C.c_uint, C.c_uint, C.c_int]
    l.data_tx_ok.restype = C.c_int
    l.fx_parse.argtypes = [C.c_char_p, C.c_uint,
                           C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
    l.fx_parse.restype = C.c_uint
    l.sethw_validate.argtypes = [C.c_uint, C.c_uint]
    l.sethw_validate.restype = C.c_uint
    l.resp_hdr.argtypes = [C.c_char_p, C.c_uint, C.c_uint]
    l.resp_hdr.restype = C.c_uint
    l.list_body.argtypes = [C.c_char_p, C.c_uint, C.c_uint, C.c_uint,
                            C.c_char_p, C.c_uint]
    l.list_body.restype = C.c_uint
    l.kiss_encode.argtypes = [C.c_char_p, C.c_uint, C.c_uint,
                              C.c_char_p, C.c_uint]
    l.kiss_encode.restype = C.c_uint
    l.n_backends.restype = C.c_uint
    l.backend.argtypes = [C.c_uint, C.POINTER(C.c_uint), C.POINTER(C.c_uint),
                          C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.c_char_p]
    l.backend.restype = C.c_int
    l.aprs_encode.argtypes = [C.c_char_p, C.c_uint, C.c_char_p,
                              C.POINTER(C.c_uint)]
    l.aprs_encode.restype = C.c_int
    return l


def kiss_frame(cmd, payload):
    out = bytearray([FEND, cmd])
    for b in payload:
        if b == FEND:
            out += bytes([FESC, TFEND])
        elif b == FESC:
            out += bytes([FESC, TFESC])
        else:
            out.append(b)
    out.append(FEND)
    return bytes(out)


def feed(lib, raw):
    """Feed bytes; return [(rc, type, payload)] for FRAME/DROPPED events."""
    events = []
    for b in raw:
        rc = lib.rx_feed(b)
        if rc == FRAME:
            n = lib.rx_len()
            events.append((rc, lib.rx_type(),
                           bytes(lib.rx_byte(i) for i in range(n))))
        elif rc == DROPPED:
            events.append((rc, None, None))
    return events


def frames_only(lib, raw):
    return [(t, p) for rc, t, p in feed(lib, raw) if rc == FRAME]


# ---- KISS parser ----

def test_kiss_data_roundtrip_with_escapes(lib):
    lib.rx_reset()
    payload = bytes([0x82, 0xA0, 0xC0, 0xDB, 0x03, 0xF0]) + b"hi"
    got = frames_only(lib, kiss_frame(0x00, payload))
    assert got == [(0x00, payload)]


def test_kiss_encode_escapes_and_parses(lib):
    lib.rx_reset()
    data = b"FX\x01\x81\x00" + bytes([FEND, FESC]) + b"APRS1200"
    out = C.create_string_buffer(128)
    n = lib.kiss_encode(out, 128, 0x06, data, len(data))
    raw = bytes(out[:n])
    assert raw[0] == FEND and raw[-1] == FEND
    assert bytes([FESC, TFEND]) in raw and bytes([FESC, TFESC]) in raw
    assert frames_only(lib, raw) == [(0x06, data)]


def test_kiss_invalid_escape_drops_and_resyncs(lib):
    lib.rx_reset()
    bad = bytes([FEND, 0x00]) + b"A" + bytes([FESC, 0x54]) + b"B" + bytes([FEND])
    ev = feed(lib, bad)
    assert ev == [(DROPPED, None, None)]
    good = kiss_frame(0x00, b"after-resync-12345")
    assert frames_only(lib, good) == [(0x00, b"after-resync-12345")]


def test_kiss_escape_cut_by_fend_drops(lib):
    lib.rx_reset()
    ev = feed(lib, bytes([FEND, 0x00, FESC, FEND]))
    assert ev == [(DROPPED, None, None)]
    assert frames_only(lib, kiss_frame(0x00, b"ok-123456789012345")) == [
        (0x00, b"ok-123456789012345")]


def test_kiss_oversized_frame_drops_whole(lib):
    lib.rx_reset()
    # 76 payload bytes: over MAX_PAYLOAD 75 but under the 80-byte ring.
    ev = feed(lib, bytes([FEND, 0x00]) + bytes([0x41] * 76) + bytes([FEND]))
    assert ev == [(DROPPED, None, None)]  # dropped, never truncated to TX
    # 90 bytes: overflow trips mid-frame, the closing FEND only resyncs.
    ev = feed(lib, bytes([FEND, 0x00]) + bytes([0x42] * 90) + bytes([FEND]))
    assert (DROPPED, None, None) in ev
    assert not [e for e in ev if e[0] == FRAME]
    assert frames_only(lib, kiss_frame(0x06, b"FX\x01\x04")) == [
        (0x06, b"FX\x01\x04")]


def test_kiss_empty_frames_and_noise_ignored(lib):
    lib.rx_reset()
    raw = bytes([FEND, FEND, 0x99, FEND]) + kiss_frame(0x00, b"x" * 17)
    assert frames_only(lib, raw) == [(0x00, b"x" * 17)]


# ---- SET_HARDWARE wire format ----

def fx(op, args=b""):
    return b"FX" + bytes([1, op]) + args


def test_fx_parse_and_validate(lib):
    op, alen = C.c_uint(), C.c_uint()
    assert lib.fx_parse(fx(1), 4, C.byref(op), C.byref(alen)) == 0
    assert (op.value, alen.value) == (1, 0)
    assert lib.fx_parse(fx(3, b"\x01\x00"), 6, C.byref(op),
                        C.byref(alen)) == 0
    assert (op.value, alen.value) == (3, 2)
    assert lib.fx_parse(b"BAD!", 4, C.byref(op), C.byref(alen)) == 1
    assert lib.fx_parse(b"FX\x02\x01", 4, C.byref(op), C.byref(alen)) == 1
    assert lib.fx_parse(fx(9), 4, C.byref(op), C.byref(alen)) == 2
    assert lib.fx_parse(fx(1), 3, C.byref(op), C.byref(alen)) == 1
    assert lib.sethw_validate(1, 0) == 0
    assert lib.sethw_validate(1, 1) == 1
    assert lib.sethw_validate(3, 2) == 0
    assert lib.sethw_validate(3, 1) == 1
    assert lib.sethw_validate(4, 0) == 0
    assert lib.sethw_validate(5, 0) == 0
    assert lib.sethw_validate(9, 0) == 2


def test_list_bodies_and_response_header(lib):
    out = C.create_string_buffer(32)
    n = lib.list_body(out, 1, 0x01, 75, b"APRS1200", 8)
    assert n == 14
    assert bytes(out[:n]) == (b"\x01\x00" b"\x01" b"\x4b\x00" b"\x08"
                              b"APRS1200")
    n = lib.list_body(out, 0xFFFF, 0, 0, b"", 0)
    assert n == 6
    assert bytes(out[:n]) == b"\xff\xff\x00\x00\x00\x00"
    n = lib.resp_hdr(out, 1, 0)
    assert n == 5
    assert bytes(out[:n]) == b"FX\x01\x81\x00"
    n = lib.resp_hdr(out, 3, 2)
    assert bytes(out[:n]) == b"FX\x01\x83\x02"


# ---- DATA TX gate: nothing malformed, unselected or unsupported may key ----

def test_data_tx_gate(lib):
    assert lib.data_tx_ok(1, 17, 1) == 1
    assert lib.data_tx_ok(1, 75, 1) == 1
    assert lib.data_tx_ok(1, 16, 1) == 0   # too short: never key
    assert lib.data_tx_ok(1, 76, 1) == 0   # too long: never key
    assert lib.data_tx_ok(0, 17, 1) == 0   # no mode selected: never key
    assert lib.data_tx_ok(2, 17, 1) == 0   # unknown mode: never key
    assert lib.data_tx_ok(1, 17, 0) == 0   # no AFSK path: never key


def test_hz_guard_band_edges(lib):
    assert lib.hz_ok(143999999) == 0
    assert lib.hz_ok(144000000) == 1
    assert lib.hz_ok(144390000) == 1
    assert lib.hz_ok(148000000) == 1
    assert lib.hz_ok(148000001) == 0
    assert lib.hz_ok(419999999) == 0
    assert lib.hz_ok(420000000) == 1
    assert lib.hz_ok(450000000) == 1
    assert lib.hz_ok(450000001) == 0


# ---- Registry: only the native mode, no invented providers ----

def test_registry_lists_only_native_aprs1200(lib):
    assert lib.n_backends() == 1
    mid, fl, maxp, nl = (C.c_uint() for _ in range(4))
    name = C.create_string_buffer(12)
    assert lib.backend(0, C.byref(mid), C.byref(fl), C.byref(maxp),
                       C.byref(nl), name) == 1
    assert mid.value == 1
    assert fl.value == 0x01  # TX only: no RX bit without a native receiver
    assert not fl.value & 0x02
    assert not fl.value & 0x04  # voice Sotto is never packet data
    assert maxp.value == 75
    assert nl.value == 9
    assert name.raw[:9] == b"APRS-1200"
    assert lib.backend(1, C.byref(mid), C.byref(fl), C.byref(maxp),
                       C.byref(nl), name) == 0


# ---- APRS bitstream: what ap_tx_encode feeds afsk_tx_send ----

def encode(lib, frame):
    bits = C.create_string_buffer(128)
    nbits = C.c_uint()
    r = lib.aprs_encode(frame, len(frame), bits, C.byref(nbits))
    return r, nbits.value, bytes(bits[: (nbits.value + 7) // 8])


def test_aprs_encode_accepts_native_range(lib):
    frame = aprs.frame("N0CALL-7", "APRS", "!4536.00N/12222.20W>hi")
    body = frame[:-2]  # WITHOUT FCS, exactly what the module feeds the encoder
    assert 17 <= len(body) <= 75
    r, nbits, raw = encode(lib, body)
    assert r != 0
    assert 0 < nbits <= 1024  # level-13 afsk_tx_send call limit
    # Independent oracle: modem/aprs.py's own HDLC+NRZI reference over the
    # same frame WITH FCS. C packs MSB-first from a tone-0 start, the
    # reference marks from tone 1, so the streams are exact complements.
    ref = aprs.hdlc_bits([frame], pre_flags=32, tail_flags=3).tolist()
    assert nbits == len(ref)
    got = [(raw[i >> 3] >> (7 - (i & 7))) & 1 for i in range(nbits)]
    assert got == [1 - b for b in ref]


def test_aprs_encode_rejects_out_of_range(lib):
    for bad in (b"x" * 16, b"x" * 76):
        r, _, _ = encode(lib, bad)
        assert r == 0


# ---- Real Thumb relocatable module build ----

need_clang = pytest.mark.skipif(
    not (shutil.which("clang") and shutil.which("ld.lld")
         and shutil.which("llvm-objcopy")),
    reason="needs clang, ld.lld and llvm-objcopy")


def test_kiss_type_only_frame_is_no_frame(lib):
    lib.rx_reset()
    assert feed(lib, bytes([FEND, 0x00, FEND])) == []
    assert frames_only(lib, kiss_frame(0x00, b"y" * 17)) == [(0x00, b"y" * 17)]


@need_clang
def test_flux_tnc_builds_position_independent():
    code, entry = k6mod.build(TNC)
    assert entry % 2 == 0
    assert 200 < len(code) < 3072  # room to spare in the module arena
    assert k6mod.source_level(TNC) == 14
    assert k6mod.module_name(code) == "FLUX TNC"
    img = k6mod.image(code, entry, 4242, bytes(range(16)), level=14)
    assert len(k6mod.packets(img, 7, 128)) <= 26


def test_shared_kiss_boundary_between_frames(lib):
    # A single FEND can terminate one frame and start the next.
    # Do not require a second FEND and lose frames from standard KISS hosts.
    lib.rx_reset()
    encoded1 = kiss_frame(0, b"A" * 18)
    encoded2 = kiss_frame(0, b"B" * 18)
    events = feed(lib, encoded1 + encoded2[1:])
    ok = [event for event in events if event[0] == FRAME]
    assert len(ok) == 2
    assert ok[0][2] == b"A" * 18
    assert ok[1][2] == b"B" * 18


def test_installed_mode_registry_is_safe_opt_in():
    # Older K6 builds reuse the API26+ tail differently; a default TNC
    # must never call that pointer merely because a radio reports API29.
    text = open(TNC).read()
    assert "FLUXTNC_ENABLE_HOST_MODE_REGISTRY" in text
    assert "level == 26" in text
    assert "FLUXTNC_CAN_DISCOVER(api)" in text
    assert "mode_info" in text


def test_stop_command_has_zero_arguments_and_no_tx_intent(lib):
    op,alen=C.c_uint(),C.c_uint()
    raw=b"FX\\x01\\x06".replace(b"\\x01",bytes([1])).replace(b"\\x06",bytes([6]))
    assert lib.fx_parse(raw,len(raw),C.byref(op),C.byref(alen)) == 0
    assert op.value == 6 and alen.value == 0
    assert lib.sethw_validate(6,0) == 0
    assert lib.sethw_validate(6,2) != 0
    text=open(TNC).read()
    assert "if (s->stop_requested)" in text
    assert "break; /* never consume a queued DATA frame after STOP */" in text
