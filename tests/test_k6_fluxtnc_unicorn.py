"""Cortex-M0 Unicorn OFFLINE execution tests for the Data-pack FLUX TNC module.

What this proves (and nothing else): the real compiled Thumb module
``c/k6/mod/fluxtnc.c`` -- built by ``modem.k6mod.build`` (clang -fropi,
linked twice, byte-identical: position-independent) -- runs on an emulated
Cortex-M0 against a scripted mock ``k6_api_t`` (``c/k6/k6_mod.h``) and:

* answers LIST / GET / SET / CAPS / STATUS over KISS type 0x06,
* reassembles serial RX arriving in tiny chunks, with C0/DB escapes and
  shared FEND boundaries,
* never keys on wrong/oversize/malformed frames, busy RSSI, a bad TX
  frequency, an unselected/voice-only mode, a refused ``tx_on``, power-off
  or a key exit (PA balanced, KISS ownership released),
* encodes one valid AX.25 payload with ``ap_tx_encode`` (FCS + bitstuff)
  into exactly one guarded AFSK TX inside the 3000 ms ``tx_on`` window,
* gates the optional API26 installed-provider discovery (legacy API14 and
  the default build never touch ``mode_info``),
* fits the module arena / scratch budget and the AFSK callback cap.

OFFLINE: no radio, serial port, SDR, flashing, GUI, network or shared
hardware is touched. The mock owns every API call; any unexpected call
(tune, flash, LCD, ...) is a hard test failure.

Run with the Python that has ``unicorn`` (here: system pytest with the
cached unicorn on PYTHONPATH)::

  PYTHONPATH=/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/.venv-cm0/lib/python3.13/site-packages \
    python3 -m pytest tests/test_k6_fluxtnc_unicorn.py -q

Needs clang, ld.lld, llvm-objcopy and unicorn. Pure-host parser coverage
lives in tests/test_k6_flux_kiss_module.py; this file only executes the
compiled module. SPDX-License-Identifier: MIT"""

import os
import struct
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from modem import aprs, k6mod  # noqa: E402

TNC = os.path.join(ROOT, "c", "k6", "mod", "fluxtnc.c")

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
CMD_DATA, CMD_SETHW = 0x00, 0x06
OP_LIST, OP_GET, OP_SET, OP_CAPS, OP_STATUS = 1, 2, 3, 4, 5
ST_OK, ST_MALFORMED, ST_UNKNOWN, ST_UNSUPPORTED = 0, 1, 2, 3
ST_BUSY, ST_TOO_LONG = 4, 6
MODE_APRS1200 = 1
VOICE_TEST, VOICE_RFTEST = 0x0100, 0x0101

# api->fd_read(0x67) raw values from KM_RSSI_DBM(reg) = ((reg&0x1FF)>>1)-174.
RAW_IDLE = (-125 + 174) * 2    # quiet B-chip floor
RAW_BUSY = (-60 + 174) * 2     # carrier present
RAW_EDGE_QUIET = (-82 + 174) * 2  # exactly the busy threshold: still idle
RAW_EDGE_BUSY = (-81 + 174) * 2   # 1 dB above: busy

need_clang = pytest.mark.skipif(
    not (os.path.exists("/usr/bin/clang") and os.path.exists("/usr/bin/ld.lld")),
    reason="needs clang and ld.lld")


# ---- KISS / Flux wire helpers (test-side framing only) ----

def kiss(cmd, payload):
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


def fx_req(op, args=b""):
    return b"FX\x01" + bytes([op]) + args


def decode_responses(raw):
    """Split captured serial_write bytes into [(type, payload)]."""
    frames = []
    for chunk in raw.split(bytes([FEND])):
        if not chunk:
            continue
        t, pay = chunk[0], chunk[1:]
        dec, i = bytearray(), 0
        while i < len(pay):
            if pay[i] == FESC and i + 1 < len(pay):
                dec.append(FEND if pay[i + 1] == TFEND else FESC)
                i += 2
            else:
                dec.append(pay[i])
                i += 1
        frames.append((t, bytes(dec)))
    return frames


def fx_resp(payload):
    assert payload[:2] == b"FX" and payload[2] == 1, payload
    return {"op": payload[3] & 0x7F, "echo": bool(payload[3] & 0x80),
            "status": payload[4], "body": payload[5:]}


def pack_provider(mode_id, flags, mtu, name):
    assert len(name) <= 12
    return struct.pack("<HBB12sH", mode_id, flags, len(name),
                       name.ljust(12, b"\0"), mtu)


# ---- the emulated radio: scripted serial RX, captured TX, guarded stubs ----

# k6_api_t slot order (c/k6/k6_mod.h): header u16 abi, u16 level, then these.
API_NAMES = ["say", "ticks", "fd_read", "fd_write", "delay_us", "tune",
             "rx_hz", "ms", "key_down", "scratch", "tx_on", "tx_off", "tx_pa",
             "key_poll", "cal_read", "cal_write", "fl_read", "fl_prog",
             "fl_erase", "lcd_text", "lcd_bar", "lcd_wf", "lcd_flush", "led",
             "clock_set", "clock_get", "lcd_glyphs", "fsk_rx_start",
             "fsk_rx_ready", "fsk_rx_read", "fsk_rx_stop", "fsk_tx_send",
             "card_store", "afsk_tx_send", "kiss_mode", "serial_read",
             "serial_write", "settings_read", "vfo_b_tune", "cw_init",
             "cw_feed", "cw_flush", "cw_call", "tone_decode", "tone_start",
             "tone_step", "tone_stop", "lcd_explore_header",
             "explore_xs_defaults", "explore_xs_init", "explore_xs_step",
             "explore_xs_close", "explore_xs_resume", "explore_ex_init",
             "explore_ex_view", "explore_ex_view_n", "explore_ex_tone",
             "explore_ex_cw", "lcd_explore_frame", "action_chirp",
             "aprs_position_set", "fsk_configured", "cancel_poll", "mode_info"]
FORBIDDEN = set(API_NAMES) - {"say", "ticks", "fd_read", "delay_us", "rx_hz",
                               "ms", "key_down", "key_poll", "scratch",
                               "tx_on", "tx_off", "afsk_tx_send", "kiss_mode",
                               "serial_read", "serial_write", "mode_info"}


class Emu:
    """Run built module bytes on a Cortex-M0 with a fully scripted API."""

    def __init__(self, code, entry, level=14, rx_chunks=(), hz=144390000,
                 rssi=(RAW_IDLE,), tx_refuse=False, afsk_rc=0, afsk_none=False,
                 kiss_refuse=False, power_off=False, providers=None,
                 providers_rc=0, room=4096, grace=5, trickle=None):
        u = pytest.importorskip("unicorn")
        from unicorn import arm_const as A
        self.A = A
        self.mu = u.Uc(u.UC_ARCH_ARM, u.UC_MODE_THUMB | u.UC_MODE_MCLASS)
        self.mu.ctl_set_cpu_model(A.UC_CPU_ARM_CORTEX_M0)
        ram = 0x20000000
        self.mu.mem_map(ram, 1 << 20)
        self.base = ram + 0x1000
        self.mu.mem_write(self.base, code)
        api, stubs = ram + 0x8000, ram + 0x9000
        self.api, self.stubs = api, stubs
        for i in range(len(API_NAMES)):
            if API_NAMES[i] == "afsk_tx_send" and afsk_none:
                continue  # NULL: non-B image, control-plane only
            self.mu.mem_write(stubs + 16 * i, b"\x70\x47")  # bx lr
        self.mu.mem_write(api, struct.pack("<HH", 1, level) + b"".join(
            (0 if (n == "afsk_tx_send" and afsk_none) else stubs + 16 * i | 1
             ).to_bytes(4, "little") for i, n in enumerate(API_NAMES)))
        self.scratch = ram + 0x40000
        self.mu.mem_write(self.scratch - 256, b"\xa5" * 8192)
        self.room = room
        self.sp0 = ram + 0xF000
        self.mu.mem_write(self.sp0 - 2048, b"\xa5" * 2048)
        stop = ram + 0xFF00
        self.mu.mem_write(stop, b"\x00\xbf" * 8)
        self.stop = stop
        # scripted inputs
        self.active = bytearray(b"".join(rx_chunks)) if trickle is None else bytearray()
        self.pending = bytearray() if trickle is None else bytearray(b"".join(rx_chunks))
        self.trickle = trickle
        self.hz, self.rssi = hz, list(rssi)
        self.tx_refuse, self.afsk_rc = tx_refuse, afsk_rc
        self.kiss_refuse, self.power = kiss_refuse, 0x8000 if power_off else 0
        self.providers = providers or []
        self.providers_rc = providers_rc
        # observations
        self.said, self.kiss_calls, self.writes = [], [], bytearray()
        self.tx_on_calls, self.tx_offs, self.afsks = [], 0, []
        self.fd_reads, self.mode_calls, self.keyed = [], [], False
        self.empty_polls, self.grace, self.loops = 0, grace, 0
        self.vus = 0
        self.mu.mmio_map(0x48000000, 0x1000, self._mmio_r, None,
                         self._mmio_w, None)
        self.mu.hook_add(u.UC_HOOK_CODE, self._on_stub,
                         begin=stubs, end=stubs + 16 * len(API_NAMES))
        self.mu.hook_add(u.UC_HOOK_CODE, self._on_stop,
                         begin=stop, end=stop)
        self.mu.reg_write(A.UC_ARM_REG_R0, api)
        self.mu.reg_write(A.UC_ARM_REG_SP, self.sp0)
        self.mu.reg_write(A.UC_ARM_REG_LR, stop | 1)
        self.pc = self.base + entry | 1

    # -- peripheral + API behaviour --
    def _mmio_r(self, uc, off, size, _):
        if 0x48000000 + off == 0x48000010:
            return self.power & ((1 << (8 * size)) - 1)
        return 0

    def _mmio_w(self, uc, off, size, val, _):
        raise AssertionError(f"unexpected MMIO write {0x48000000 + off:#x}")

    def _cstr(self, p):
        return bytes(self.mu.mem_read(p, 96)).split(b"\0", 1)[0].decode()

    def _on_stop(self, uc, addr, size, _):
        uc.emu_stop()

    def _ret(self, uc, v):
        uc.reg_write(self.A.UC_ARM_REG_R0, v & 0xFFFFFFFF)

    def _on_stub(self, uc, addr, size, _):
        i = (addr - self.stubs) // 16
        name = API_NAMES[i]
        R = self.A
        r0 = uc.reg_read(R.UC_ARM_REG_R0)
        r1 = uc.reg_read(R.UC_ARM_REG_R1)
        r2 = uc.reg_read(R.UC_ARM_REG_R2)
        if name in FORBIDDEN:
            raise AssertionError(f"module called forbidden {name}")
        if name == "say":
            self.said.append(self._cstr(r0))
        elif name == "ticks":
            self._ret(uc, (self.vus // 100) & 0xFFFF)
        elif name == "fd_read":
            self.fd_reads.append(r0 & 0xFF)
            assert (r0 & 0xFF) == 0x67, "only REG_67 RSSI reads expected"
            self._ret(uc, self.rssi.pop(0) if len(self.rssi) > 1 else self.rssi[0])
        elif name == "delay_us":
            self.vus += r0
            if r0 == 5000 and self.trickle is not None and self.pending:
                n = min(self.trickle, len(self.pending))
                self.active += self.pending[:n]
                del self.pending[:n]
        elif name == "rx_hz":
            self._ret(uc, self.hz)
        elif name == "ms":
            self._ret(uc, (self.vus // 1000) & 0xFFFFFFFF)
        elif name in ("key_down", "key_poll"):
            self.loops += 1
            if self.active or self.pending or self.empty_polls < self.grace:
                if not self.active and not self.pending:
                    self.empty_polls += 1
                self._ret(uc, 0)
            else:
                self._ret(uc, 1)
        elif name == "scratch":
            uc.mem_write(r0, struct.pack("<I", self.room))
            self._ret(uc, self.scratch)
        elif name == "tx_on":
            if self.tx_refuse or self.keyed:
                self.tx_on_calls.append((r0, 0))
                self._ret(uc, 0xFFFFFFFF if self.tx_refuse else 0xFFFFFFFE)
            else:
                self.tx_on_calls.append((r0, 1))
                self.keyed = True
                self._ret(uc, 0)
        elif name == "tx_off":
            self.tx_offs += 1
            self.keyed = False
        elif name == "afsk_tx_send":
            nbytes = (r1 + 7) // 8
            self.afsks.append((bytes(uc.mem_read(r0, nbytes)), r1))
            self._ret(uc, self.afsk_rc & 0xFFFFFFFF)
        elif name == "kiss_mode":
            self.kiss_calls.append(r0)
            self._ret(uc, 1 if self.kiss_refuse else 0)
        elif name == "serial_read":
            self._ret(uc, self.active.pop(0) if self.active else 0xFFFFFFFF)
        elif name == "serial_write":
            self.writes += bytes(uc.mem_read(r0, r1 & 0xFFFF))
            self._ret(uc, 0)
        elif name == "mode_info":
            self.mode_calls.append(r0)
            if self.providers_rc:
                self._ret(uc, 0xFFFFFFFF)
            elif r0 < len(self.providers):
                uc.mem_write(r1, self.providers[r0])
                self._ret(uc, 1)
            else:
                self._ret(uc, 0)
        else:  # pragma: no cover - guarded by FORBIDDEN above
            raise AssertionError(f"unhandled {name}")

    def run(self, cap=20_000_000):
        self.mu.emu_start(self.pc, self.stop, count=cap)
        r = self.mu.reg_read(self.A.UC_ARM_REG_R0)
        return r - (1 << 32) if r & 0x80000000 else r

    def responses(self):
        return [(t, fx_resp(p)) for t, p in decode_responses(bytes(self.writes))
                if t == CMD_SETHW and len(p) >= 5]

    def stack_used(self):
        paint = bytes(self.mu.mem_read(self.sp0 - 2048, 2048))
        return next((2048 - i for i, b in enumerate(paint) if b != 0xA5), 0)

    def scratch_used(self):
        # module zeroes sizeof(fluxtnc_t) at scratch base; painted A5 above
        seg = bytes(self.mu.mem_read(self.scratch, 4096))
        used = 0
        for i, b in enumerate(seg):
            if b != 0xA5:
                used = i + 1
        return used

    def check_cleanup(self):
        assert self.kiss_calls and self.kiss_calls[0] == 1, self.kiss_calls
        assert self.kiss_calls[-1] == 0, self.kiss_calls  # KISS released
        assert self.tx_offs == sum(ok for _, ok in self.tx_on_calls), \
            (self.tx_on_calls, self.tx_offs)  # every key is unkeyed
        assert not self.keyed, "PA left keyed"


# ---- builds ----

@pytest.fixture(scope="module")
def build_plain():
    pytest.importorskip("unicorn")
    return k6mod.build(TNC)


@pytest.fixture(scope="module")
def build_registry():
    pytest.importorskip("unicorn")
    with tempfile.TemporaryDirectory() as d:
        w = os.path.join(d, "w.c")
        with open(w, "w") as f:
            f.write("#define FLUXTNC_ENABLE_HOST_MODE_REGISTRY 1\n"
                    f'#include "{TNC}"\n')
        return k6mod.build(w)


@need_clang
def test_build_pic_and_size(build_plain):
    code, entry = build_plain
    assert entry % 2 == 0
    assert 200 < len(code) < 3072  # room in the module arena
    assert k6mod.source_level(TNC) == 14
    assert k6mod.module_name(code) == "FLUX TNC"
    img = k6mod.image(code, entry, 4242, bytes(range(16)), level=14)
    assert len(k6mod.packets(img, 7, 128)) <= 26


def set_req(mode):
    return kiss(CMD_SETHW, fx_req(OP_SET, struct.pack("<H", mode)))


def data_req(payload):
    return kiss(CMD_DATA, payload)


@need_clang
def test_list_get_set_caps_status_normal(build_plain):
    code, entry = build_plain
    rx = [set_req(1), kiss(CMD_SETHW, fx_req(OP_GET)),
          kiss(CMD_SETHW, fx_req(OP_LIST)), kiss(CMD_SETHW, fx_req(OP_CAPS)),
          kiss(CMD_SETHW, fx_req(OP_STATUS))]
    e = Emu(code, entry, rx_chunks=rx)
    assert e.run() == -2  # key exit
    e.check_cleanup()
    assert e.said[0] == "flux tnc: KISS TNC up"
    assert e.tx_on_calls == [] and e.afsks == []
    rs = e.responses()
    assert [(r["op"], r["status"]) for _, r in rs] == [
        (OP_SET, ST_OK), (OP_GET, ST_OK), (OP_LIST, ST_OK),
        (OP_LIST, ST_OK), (OP_CAPS, ST_OK), (OP_STATUS, ST_OK)]
    assert rs[0][1]["body"] == b"\x01\x00" and rs[1][1]["body"] == b"\x01\x00"
    assert rs[2][1]["body"] == (b"\x01\x00\x01\x4b\x00\x09APRS-1200")
    assert rs[3][1]["body"] == b"\xff\xff\x00\x00\x00\x00"  # terminator
    assert rs[4][1]["body"] == b"\x00\x00\x4b\x00"  # no RX, 75 TX
    assert rs[5][1]["body"] == b"\x00\x00\x00\x00"  # tx_ok/fail zero


@need_clang
def test_small_chunks_escapes_shared_fend(build_plain):
    code, entry = build_plain
    payload = bytes([0x82, 0xA0, FEND, FESC, 0x03, 0xF0]) + b"hi" * 6
    assert 17 <= len(payload) <= 75
    a = kiss(CMD_DATA, payload)
    b = kiss(CMD_SETHW, fx_req(OP_GET))
    # shared FEND boundary, then 2-byte trickle (UART arrival, not bulk)
    e = Emu(code, entry, rx_chunks=[a + b[1:], set_req(1)],
            trickle=2)
    assert e.run() == -2
    e.check_cleanup()
    # GET ran before any SET: mode still none
    rs = e.responses()
    gets = [r for _, r in rs if r["op"] == OP_GET]
    sets = [r for _, r in rs if r["op"] == OP_SET]
    assert len(gets) == 1 and gets[0]["body"] == b"\x00\x00"
    assert len(sets) == 1 and sets[0]["status"] == ST_OK
    assert [r["status"] for _, r in rs if r["op"] == 0] == [ST_UNSUPPORTED]


@need_clang
def test_wrong_and_oversize_frames_never_key(build_plain):
    code, entry = build_plain
    good = data_req(b"y" * 17)
    bad = [kiss(0x10, b"y" * 17),        # wrong port
           kiss(0x01, b"y" * 17),        # wrong command
           kiss(CMD_DATA, b"s" * 16),    # too short
           kiss(CMD_DATA, b"L" * 76),    # oversize: dropped whole
           bytes([FEND, CMD_DATA]) + b"A" + bytes([FESC, 0x54]) + b"B" + bytes([FEND]),
           bytes([FEND, CMD_DATA, FESC, FEND]),  # cut escape
           bytes([FEND, FEND, 0x99, FEND]),      # noise + empty
           bytes([FEND, CMD_DATA, FEND]),        # type-only
           kiss(CMD_SETHW, fx_req(9)),   # unknown op -> UNKNOWN, no TX
           good]  # trailing valid frame, but no mode selected: refused
    e = Emu(code, entry, rx_chunks=bad)
    assert e.run() == -2
    e.check_cleanup()
    assert e.tx_on_calls == [] and e.afsks == [] and e.fd_reads == []
    rs = e.responses()
    kinds = [(r["op"], r["status"]) for _, r in rs]
    assert (9, ST_UNKNOWN) in kinds  # the parsed-but-unknown op answers
    refusals = [r for _, r in rs if r["op"] == 0]
    assert [r["status"] for r in refusals] == [ST_MALFORMED, ST_UNSUPPORTED]
    assert all(r["body"] == b"\x00\x00" for r in refusals)  # no mode selected


@need_clang
def test_valid_ax25_one_guarded_afsk_tx(build_plain):
    code, entry = build_plain
    frame = aprs.frame("N0CALL-7", "APRS", "!4536.00N/12222.20W>hi")
    body = frame[:-2]
    assert 17 <= len(body) <= 75
    e = Emu(code, entry, rx_chunks=[set_req(1), data_req(body),
                                    kiss(CMD_SETHW, fx_req(OP_STATUS))])
    assert e.run() == -2
    e.check_cleanup()
    assert [ms for ms, ok in e.tx_on_calls] == [3000]  # 3 s txdog window
    assert len(e.afsks) == 1
    raw, bits = e.afsks[0]
    assert 0 < bits <= 1024  # level-13 callback cap; 1200 baud => <1 s air
    ref = aprs.hdlc_bits([frame], pre_flags=32, tail_flags=3).tolist()
    assert bits == len(ref)
    got = [(raw[i >> 3] >> (7 - (i & 7))) & 1 for i in range(bits)]
    assert got == [1 - b for b in ref]  # C packs tone-0 start, ref tone 1
    assert e.fd_reads == [0x67] * 2  # two separated RSSI samples per packet
    st = [r for _, r in e.responses() if r["op"] == OP_STATUS][0]
    assert st["body"] == b"\x01\x00\x00\x00"  # tx_ok=1, tx_fail=0


@need_clang
@pytest.mark.parametrize("rssi,hz,tx_refuse,afsk_rc,why", [
    ((RAW_BUSY,), 144390000, False, 0, "busy"),
    ((RAW_EDGE_BUSY,), 144390000, False, 0, "edge-busy"),
    ((RAW_IDLE,), 100000000, False, 0, "freq"),
    ((RAW_IDLE,), 144390000, True, 0, "refused"),
    ((RAW_IDLE,), 144390000, False, 7, "afsk-fail"),
])
def test_guarded_tx_failure_modes(build_plain, rssi, hz, tx_refuse, afsk_rc, why):
    code, entry = build_plain
    frame = aprs.frame("N0CALL-7", "APRS", ">quiet please")
    body = frame[:-2]
    assert 17 <= len(body) <= 75
    e = Emu(code, entry, rx_chunks=[set_req(1), data_req(body),
                                    kiss(CMD_SETHW, fx_req(OP_STATUS))],
            rssi=rssi * 4, hz=hz, tx_refuse=tx_refuse, afsk_rc=afsk_rc)
    assert e.run() == -2
    e.check_cleanup()
    if why == "busy" or why == "edge-busy":
        assert e.tx_on_calls == []  # carrier check first: never keyed
    elif why == "freq":
        assert e.tx_on_calls == []
    elif why == "refused":
        assert e.tx_on_calls == [(3000, 0)] and e.afsks == []
        assert e.tx_offs == 0  # never keyed: nothing to undo
    else:
        assert e.tx_on_calls == [(3000, 1)] and len(e.afsks) == 1
    st = [r for _, r in e.responses() if r["op"] == OP_STATUS][0]
    ok, fail = struct.unpack("<HH", st["body"])
    assert (ok, fail) == (0, 1)
    assert [r for _, r in e.responses() if r["op"] == 0] == []  # silent


@need_clang
def test_voice_mode_selected_but_never_transmits(build_plain):
    code, entry = build_plain
    frame = aprs.frame("N0CALL-7", "APRS", ">no voice data")
    e = Emu(code, entry, rx_chunks=[set_req(VOICE_TEST), data_req(frame[:-2]),
                                    kiss(CMD_SETHW, fx_req(OP_GET))])
    assert e.run() == -2
    e.check_cleanup()
    assert e.tx_on_calls == [] and e.afsks == []
    assert e.mode_calls == []  # legacy: discovery never consulted
    rs = e.responses()
    assert rs[0] == (CMD_SETHW, {"op": OP_SET, "echo": True,
                                 "status": ST_UNKNOWN, "body": b""})
    assert rs[2][1]["body"] == b"\x00\x00"  # selection refused, still none
    assert rs[1][1]["status"] == ST_UNSUPPORTED  # explicit DATA refusal


@need_clang
def test_no_afsk_image_is_control_plane_only(build_plain):
    code, entry = build_plain
    frame = aprs.frame("N0CALL-7", "APRS", ">hi")
    e = Emu(code, entry, rx_chunks=[set_req(1), data_req(frame[:-2]),
                                    kiss(CMD_SETHW, fx_req(OP_LIST))],
            afsk_none=True)
    assert e.run() == -2
    e.check_cleanup()
    assert e.tx_on_calls == [] and e.afsks == []
    rs = e.responses()
    assert rs[0][1]["status"] == ST_UNSUPPORTED  # SET needs the TX path
    lists = [r for _, r in rs if r["op"] == OP_LIST]
    assert [r["body"] for r in lists] == [b"\xff\xff\x00\x00\x00\x00"]


@need_clang
def test_power_off_and_kiss_refusal(build_plain):
    code, entry = build_plain
    e = Emu(code, entry, power_off=True)
    assert e.run() == -2
    assert e.kiss_calls == [1, 0]  # KISS ownership still released
    assert e.tx_on_calls == []
    assert e.said == ["flux tnc: KISS TNC up", "flux tnc: down"]
    e2 = Emu(code, entry, kiss_refuse=True)
    assert e2.run() == -4
    assert e2.kiss_calls == [1]  # refused: never took ownership


@need_clang
def test_level_guard_and_scratch_budget(build_plain):
    code, entry = build_plain
    assert Emu(code, entry, level=13).run() == -3
    e = Emu(code, entry, room=4096)
    assert e.run() == -2
    used = e.scratch_used()
    assert 0 < used <= 4096
    print(f"fluxtnc scratch struct={used} code={len(code)} "
          f"stack={e.stack_used()}")
    assert e.stack_used() <= 1024
    assert Emu(code, entry, room=used - 1).run() == -8  # too small: -8


@need_clang
@pytest.mark.parametrize("registry,level,n_voice", [
    (False, 14, 0), (False, 26, 0), (True, 14, 0), (True, 26, 2)])
def test_api26_discovery_gated(build_plain, build_registry, registry, level,
                               n_voice):
    code, entry = build_registry if registry else build_plain
    provs = [pack_provider(VOICE_TEST, 0x04, 0, b"SOTTO TEST"),
             pack_provider(VOICE_TEST, 0x04, 0, b"SOTTO TEST"),  # dup
             pack_provider(VOICE_RFTEST, 0x04, 0, b"SOTTO RFTEST")]
    e = Emu(code, entry, level=level, providers=provs,
            rx_chunks=[kiss(CMD_SETHW, fx_req(OP_LIST))])
    assert e.run() == -2
    e.check_cleanup()
    lists = [r for _, r in e.responses() if r["op"] == OP_LIST]
    assert len(lists) == 1 + n_voice + 1  # native + voices + terminator
    assert lists[-1]["body"] == b"\xff\xff\x00\x00\x00\x00"
    if n_voice:
        assert lists[1]["body"][5:6] == b"\x0a"  # SOTTO TEST name_len 10
        # selecting a discovered voice id works, but DATA still refuses
        e2 = Emu(code, entry, level=level, providers=provs,
                 rx_chunks=[set_req(VOICE_TEST),
                            data_req(b"v" * 20),
                            kiss(CMD_SETHW, fx_req(OP_GET))])
        assert e2.run() == -2
        e2.check_cleanup()
        assert e2.tx_on_calls == []
        rs = e2.responses()
        assert rs[0][1] == {"op": OP_SET, "echo": True, "status": ST_OK,
                            "body": b"\x00\x01"}
        assert rs[2][1]["body"] == b"\x00\x01"
        assert rs[1][1]["status"] == ST_UNSUPPORTED
    else:
        assert e.mode_calls == []  # untouched: no accidental slot call


@need_clang
def test_api26_malformed_or_busy_provider_fails_closed(build_registry):
    code, entry = build_registry
    bad = [pack_provider(VOICE_TEST, 0x01, 0, b"SOTTO TEST")]  # TX, not voice
    e = Emu(code, entry, level=26, providers=bad,
            rx_chunks=[kiss(CMD_SETHW, fx_req(OP_LIST))])
    assert e.run() == -2
    assert any(r["status"] == ST_UNSUPPORTED for _, r in e.responses())
    e2 = Emu(code, entry, level=26, providers_rc=1,
             rx_chunks=[kiss(CMD_SETHW, fx_req(OP_LIST)),
                        set_req(VOICE_TEST)])
    assert e2.run() == -2
    e2.check_cleanup()
    assert e2.tx_on_calls == []
    rs = e2.responses()
    assert len([r for _, r in rs if r["status"] == ST_BUSY]) == 2
    assert any(r["op"] == OP_LIST and r["status"] == ST_BUSY for _, r in rs)
    assert any(r["op"] == OP_SET and r["status"] == ST_BUSY for _, r in rs)


