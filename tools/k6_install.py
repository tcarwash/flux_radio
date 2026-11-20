#!/usr/bin/env python3
"""Install Flux-compatible firmware and modules on a Baofeng UV-K6 / K61 over the programming cable alone: no second radio.
The instruction book is docs/manual/install.md; this is the tool it walks you through.

  python3 tools/k6_install.py install BUNDLE        everything, step by step (a new radio, or an update)
  python3 tools/k6_install.py backup -o FILE        the radio's whole 2 MB SPI flash (calibration included)
  python3 tools/k6_install.py flash IMAGE           key an image with your key and flash it
  python3 tools/k6_install.py load BUNDLE           EXPLORE's pages, the help deck and the modules
  python3 tools/k6_install.py status                what the radio runs and what its library holds
  python3 tools/k6_install.py key                   make your module key (once)
  python3 tools/k6_install.py bundle OUT -o DIR     a release folder from the Flux build output or CI

How the pieces get there:

- The core image goes in through the stock bootloader (MCU flash, 1 KB packages). The bootloader
  listens on the cable for about 200 ms after every reset: this tool catches that window when you
  switch the radio on, or, on a radio already running Flux module-host firmware, right after the cable session's end
  command restarts it (no hands).
- Everything else (EXPLORE's code pages, the help card deck, the modules) goes into the radio's
  SPI module library through the image's cable session (cps::run_cable_session in the upstream
  firmware patch): the same signed chunks the radio takes over the air, checked by the same loader, written
  by the same library code (slots in 0x110000-0x18FFFF only). The session opens on a handshake
  while the radio is in EXPLORE or the arena, ignores the PTT line (the cable's PC->radio wire),
  never transmits, and ends with a restart.
- Your key: every image is keyed with it on this machine (the release images carry a placeholder)
  and every library image is signed with it as it is sent. It never leaves this machine; nobody
  else's key is needed or shipped. Keep the key file: an image keyed with it takes only modules
  signed with it.

Needs Python 3.8+, pyserial and numpy (pip install pyserial numpy). SPDX-License-Identifier: MIT
"""
import argparse
import binascii
import datetime
import hashlib
import hmac
import secrets
import json
import os
import shutil
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
from modem import k6mod  # noqa: E402

# ---- the cable session's and Aura's CPS frames: A6 cmd addr(2) len(2) data CRC-16/XMODEM(2) ----

CPS_HEADER = 0xA6
ACK = 0x06
CABLE_HANDSHAKE = b"PROGRAMBF-K6BLWF"     # blowfang's cable session (cps::run_cable_session)
AURA_HANDSHAKE = b"PROGRAMBF-K6AURA"      # Aura's CPS session: its first-boot dump, or full Aura
CMD_INFO, CMD_CHUNK, CMD_READ, CMD_END, CMD_ERROR = 0x56, 0x4D, 0x44, 0x45, 0xEE
CMD_CAPS = 0x49  # optional Blowfang read-only capability query
CMD_SERIAL = 0x4A  # optional replay-watermark query
CMD_READ_LOGO, CMD_WRITE_LOGO = 0x4C, 0x6C  # Aura boot bitmap, 64-byte chunks
CHUNK_EARLY_ACK = b"\x7fCHK"
ERR_BAD_CRC = 0x01
READ_BLOCK = 64
CHIP_BYTES = 2 << 20

# the SPI library (c/k6/k6_lib.h) and the codebooks (docs/k6-spi-codebook.md)
LIB_BASE, LIB_SLOT, LIB_SLOTS = 0x110000, 0x4000, 32
CODEBOOKS = ((0x100000, b"K6CB", "sotto's codebook (v3.7, sotto 1200)", "cbi"),
             (0x108000, b"K6VL", "sotto 600 / 400's codebooks", "vlri"))
PAGE_SLOTS = {"table": 9, "sweep": 10, "listen": 11, "edit": 12}
CARD_SLOTS = (13, 14, 15)
BUILD_ID = 8          # an EXPLORE page ends with its image's build ID (tools/k6_pages.py)
CHUNK = 64            # the cable sends L packets: 64 bytes, 58 of image each

# km_packet's results (c/k6/k6_mod.h) and the library's (k6_lib.h)
KM = {1: "chunk", 2: "repeat", 3: "checked", -1: "bad CRC", -2: "bad chunk", -3: "bad header",
      -4: "wrong key", -5: "no key in the image", -6: "old serial"}
KL = {-1: "slot empty", -2: "bad header", -3: "bad tag", -4: "flash refused", -5: "read-back differs",
      -6: "no such slot", -7: "not a module"}

TIMEOUT = 3.0         # s for an answer before a frame goes again
DEFAULT_KEYS = ("~/.config/flux/k6-mod.key", "~/.config/blowfang/k6-mod.key", "~/.config/sotto/k6-mod.key")


class InstallError(Exception):
    pass


def crc16_xmodem(data):
    return binascii.crc_hqx(bytes(data), 0)


def cps_frame(cmd, addr=0, data=b""):
    body = struct.pack(">BHH", cmd, addr, len(data)) + bytes(data)
    return bytes([CPS_HEADER]) + body + struct.pack(">H", crc16_xmodem(body))


class CpsLink:
    """One CPS session on the cable: the cable session (blowfang) or Aura's. Frames go one at a
    time or a few in flight (window); the radio's report lines between frames (plain text: never
    an 0xA6 byte) go to log."""

    def __init__(self, port, log=print, timeout=None):
        self.port, self.log, self.timeout = port, log, timeout or TIMEOUT
        self.buf = b""
        self.text = b""
        self.rx_recent = bytearray()
        self.bad_response_crcs = 0
        self.km_diag_stage = None
        self.km_diag_by_chunk = {}
        self.km_diag_chunk = None
        self.lines = []                                      # the radio's report lines so far

    def _fill(self):
        b = self.port.read(256)
        if b:
            self.rx_recent.extend(b)
            if len(self.rx_recent) > 256:
                del self.rx_recent[:-256]
            self.buf += b
        return bool(b)

    def _text(self, b):
        self.text += b
        while b"\n" in self.text:
            line, self.text = self.text.split(b"\n", 1)
            line = line.decode("ascii", "replace").strip()
            if line:
                self.lines.append(line)
                self.log("  K6: " + line[:120])

    def handshake(self, key, wait=20.0, gap=0.5, fresh=True):
        """Send key until the radio answers ACK (0x06), at most `wait` seconds, one try every
        `gap` s (each 16 bytes take 1.4 ms: PTT's debouncer never sees them). True on ACK.
        fresh: drop what the radio sent before (False keeps its report lines coming)."""
        deadline = time.monotonic() + wait
        if fresh:
            self.port.reset_input_buffer()
            self.buf = b""
        while time.monotonic() < deadline:
            self.port.write(key)
            t = time.monotonic() + gap
            while time.monotonic() < t:
                self._fill()
                i = self.buf.find(bytes([ACK]))
                if i >= 0:
                    self._text(self.buf[:i])
                    self.buf = self.buf[i + 1:]
                    return True
                if b"\n" in self.buf:
                    j = self.buf.rindex(b"\n") + 1
                    self._text(self.buf[:j])
                    self.buf = self.buf[j:]
        return False

    def _frame(self, deadline):
        """The next frame from the radio: (cmd, addr, data), or None at the deadline"""
        while True:
            i = self.buf.find(bytes([CPS_HEADER]))
            if i < 0:
                self._text(self.buf)
                self.buf = b""
            else:
                if i:
                    self._text(self.buf[:i])
                    self.buf = self.buf[i:]
                if len(self.buf) >= 6:
                    n = struct.unpack(">H", self.buf[4:6])[0]
                    if len(self.buf) >= 8 + n:
                        f, self.buf = self.buf[:8 + n], self.buf[8 + n:]
                        if crc16_xmodem(f[1:6 + n]) == struct.unpack(">H", f[6 + n:8 + n])[0]:
                            return f[1], struct.unpack(">H", f[2:4])[0], f[6:6 + n]
                        self.bad_response_crcs += 1
                        self.buf = f[1:] + self.buf          # not a frame after all: resync
                        continue
            if time.monotonic() > deadline:
                return None
            self._fill()

    def calls(self, reqs, window=1, retries=4):
        """reqs: [(cmd, addr, data)] -> their answers' data, in order. Up to `window` frames in
        flight; an answer that doesn't come, or a CRC error, sends that frame again."""
        self.rx_recent.clear()
        self.bad_response_crcs = 0
        out = [None] * len(reqs)
        todo = list(range(len(reqs)))
        tries = {}
        crc_errors = {}
        while todo:
            batch, todo = todo[:window], todo[window:]
            self.km_diag_chunk = (reqs[batch[0]][1]
                                  if len(batch) == 1 and reqs[batch[0]][0] == CMD_CHUNK else None)
            for i in batch:
                self.port.write(cps_frame(*reqs[i]))
            pending = list(batch)
            deadline = time.monotonic() + self.timeout
            while pending:
                f = self._frame(deadline)
                if f is None:
                    break
                cmd, addr, data = f
                if cmd == CMD_INFO and data == b"KM":
                    self.km_diag_stage = addr
                    if self.km_diag_chunk is not None:
                        self.km_diag_by_chunk[self.km_diag_chunk] = addr
                    if any(reqs[j][0] == CMD_CHUNK and reqs[j][1] == 0 for j in pending):
                        if addr & 0x80:
                            self.log(f"  K6: C reached KM_CHUNK return branch (expected r={addr & 0x7F})")
                        else:
                            self.log(f"  K6: module loader reached diagnostic stage {addr}")
                    continue
                if cmd == CMD_INFO and addr == 0xFFFF and data[:2] == b"KR":
                    r, have, count, last = struct.unpack("bBBb", data[2:6])
                    self.log(f"  K6: firmware return probe r={r} have={have} count={count} last={last}")
                    continue
                hit = next((i for i in pending if reqs[i][1] == addr), None)
                if hit is None:
                    continue                                 # a late answer to an earlier try
                if cmd == CMD_ERROR:
                    if data[:1] != bytes([ERR_BAD_CRC]):
                        raise InstallError(f"the radio refused command {reqs[hit][0]:#04x} at {addr}: error {data.hex()}")
                    crc_errors[hit] = crc_errors.get(hit, 0) + 1
                    continue                                 # sent again below
                if cmd == reqs[hit][0] and data == CHUNK_EARLY_ACK:
                    if reqs[hit][0] == CMD_CHUNK and reqs[hit][1] == 0:
                        self.log("  K6: radio accepted the first cable frame; waiting for module-loader result")
                    continue
                if cmd != reqs[hit][0]:
                    continue
                out[hit] = data
                pending.remove(hit)
            again = [i for i in batch if out[i] is None]
            for i in again:
                tries[i] = tries.get(i, 0) + 1
                if tries[i] > retries:
                    if crc_errors.get(i, 0):
                        raise InstallError(f"the radio rejected command {reqs[i][0]:#04x} at {reqs[i][1]} "
                                           f"{crc_errors[i]} time(s): cable-frame CRC error; check the serial "
                                           "link and radio UART framing")
                    if self.bad_response_crcs:
                        raise InstallError(f"received {self.bad_response_crcs} radio frame(s) with bad CRC; "
                                           f"last serial bytes: {bytes(self.rx_recent).hex(' ')}")
                    if self.rx_recent:
                        raise InstallError(f"radio sent no valid reply to command {reqs[i][0]:#04x}; "
                                           f"last serial bytes: {bytes(self.rx_recent).hex(' ')}")
                    raise InstallError(f"no answer from the radio to command {reqs[i][0]:#04x} at {reqs[i][1]}")
            todo = again + todo
            if again:
                time.sleep(0.05)
                self.port.reset_input_buffer()
                self.buf = b""
        return out

    def call(self, cmd, addr=0, data=b""):
        return self.calls([(cmd, addr, data)])[0]

    def read(self, addr, n, window=8, progress=None):
        """n bytes of the SPI flash from addr (64-byte aligned)"""
        if addr % READ_BLOCK:
            raise ValueError("reads start on a 64-byte block")
        blocks = range(addr // READ_BLOCK, (addr + n + READ_BLOCK - 1) // READ_BLOCK)
        out = bytearray()
        step = 256
        for s in range(0, len(blocks), step):
            part = self.calls([(CMD_READ, b, b"") for b in blocks[s:s + step]], window=window)
            for d in part:
                if len(d) != READ_BLOCK:
                    raise InstallError(f"a read came back {len(d)} bytes, not {READ_BLOCK}")
                out += d
            if progress:
                progress(len(out), len(blocks) * READ_BLOCK)
        return bytes(out[:n])

    def end(self):
        """End the session: the cable session restarts the radio; Aura's first-boot one returns
        to the FORMAT FLASH? screen"""
        try:
            self.call(CMD_END)
        except InstallError:
            pass                                             # it restarts all the same after 5 s idle


class Cable(CpsLink):
    """blowfang's cable session"""

    def invalidate_library_directory(self):
        self.__dict__.pop('_library_directory', None)

    def handshake(self, *args, **kwargs):
        self.invalidate_library_directory()
        self.__dict__.pop('_info', None)
        return super().handshake(*args, **kwargs)

    def info(self):
        if hasattr(self,"_info"):return self._info
        d = self.call(CMD_INFO)
        if d[:4] != b"BLWF":
            raise InstallError(f"the radio did not identify as a Flux-compatible module host (INFO: {d.hex()})")
        self._info=dict(protocol=d[4], slots=d[5], arena=struct.unpack("<H", d[6:8])[0],
                    build_id=d[8:16] if any(d[8:16]) else None)
        return self._info

    def capabilities(self):
        """Query optional versioned firmware capabilities; never infer them from INFO."""
        return self.parse_capabilities(self.call(CMD_CAPS))

    def serial_watermark(self):
        """Read the current boot's replay floor when firmware advertises the query."""
        d = self.call(CMD_SERIAL)
        if len(d) != 4:
            raise InstallError('malformed replay-watermark reply')
        return struct.unpack('<I', d)[0]

    @staticmethod
    def parse_capabilities(data):
        if len(data) != 16 or data[:4] != b'K6CP' or data[4] not in (1, 2) or data[7] != 0:
            raise InstallError('unsupported or malformed firmware capability record')
        if (data[4] == 1 and any(data[8:])) or (data[4] == 2 and not any(data[8:])):
            raise InstallError('invalid firmware build identity field')
        mask = data[5]
        if mask & ~255 or not mask & 1 or mask & 24 == 24:
            raise InstallError('invalid firmware capability flags')
        return {'format': data[4], 'build_id': data[8:].hex() if data[4] == 2 else None, 'module_loader': True, 'scanner': bool(mask & 2),
                'fsk_rx': bool(mask & 4), 'module_api_max': data[6],
                'ota': bool(mask & 8), 'cable_only': bool(mask & 16), 'designer': bool(mask & 32),
                'sotto': bool(mask & 64),
                'authenticated': False, 'build_identity_verified': False}

    def authenticated_capabilities(self, key):
        """Fresh, domain-separated proof of key possession and reported capability record."""
        if len(key) != 16 or key == k6mod.PLACEHOLDER:
            raise InstallError('invalid module key for capability challenge')
        nonce = secrets.token_bytes(16)
        reply = self.call(CMD_CAPS, data=nonce)
        if len(reply) != 25:
            raise InstallError('malformed or unsupported capability proof')
        record, status, tag = reply[:16], reply[16], reply[17:]
        if status != 0:
            raise InstallError('radio has no provisioned key or refused capability proof')
        caps = self.parse_capabilities(record)
        if caps.get('cable_only') or caps['format'] != 2:
            raise InstallError('capability proof requires protocol 2')
        expected = k6mod.siphash24(key, b'K6CAPAU1' + record + nonce).to_bytes(8, 'little')
        if not hmac.compare_digest(tag, expected):
            raise InstallError('capability proof did not match the expected module key')
        return caps  # caller separately records successful authentication

    def send_image(self, img, xfer, window=1):
        """One signed image as L chunks; the last chunk alone (its install erases and programs
        SPI, and the radio's 320-byte receive ring must not overflow meanwhile). Returns
        (km_packet's last result, the install's result)."""
        self.invalidate_library_directory()
        self.km_diag_stage = None
        self.km_diag_by_chunk = {}
        pk = k6mod.packets(img, xfer, CHUNK)
        reqs = [(CMD_CHUNK, i, p) for i, p in enumerate(pk)]
        ans = self.calls(reqs[:-1], window=window) if len(reqs) > 1 else []
        for i, d in enumerate(ans):
            r = struct.unpack("b", d[:1])[0]
            if r == 0:
                stage = self.km_diag_by_chunk.get(i)
                detail = (f"; C reached KM_CHUNK return branch (expected r={stage & 0x7F})" if stage is not None and stage & 0x80
                          else f"; loader stopped at diagnostic stage {stage}" if stage is not None else "")
                raise InstallError(f"module packet {i}/{len(pk) - 1} was not recognized{detail}; "
                                   f"radio reports {d[1]}/{d[2]} chunks; reply bytes {d.hex(' ')}")
            if r < 0:
                raise InstallError(f"chunk {i}: {KM.get(r, r)}")
        d = self.calls(reqs[-1:])[0]
        r, have, count = struct.unpack("b", d[:1])[0], d[1], d[2]
        put = struct.unpack("<i", d[4:8])[0]
        if r == 0:
            i = len(pk) - 1
            stage = self.km_diag_by_chunk.get(i)
            detail = (f"; C reached KM_CHUNK return branch (expected r={stage & 0x7F})" if stage is not None and stage & 0x80
                      else f"; loader stopped at diagnostic stage {stage}" if stage is not None else "")
            raise InstallError(f"module packet {i}/{len(pk) - 1} was not recognized{detail}; "
                               f"radio reports {have}/{count} chunks; reply bytes {d.hex(' ')}")
        return r, have, count, put

    def start_downlink(self, cmd, addr, payload, rate):
        """Start a bounded read-only response stream; all commands sent at baseline."""
        if self.port.baudrate != 115200 or not 115200 <= rate <= 3000000:
            raise InstallError('downlink must start at baseline with a valid baud rate')
        if cmd not in (0x64, 0x70, 0x74):
            raise InstallError('not a read-only streaming command')
        self.bad_response_crcs = 0
        request = cps_frame(cmd, addr, payload)
        if rate == 115200:
            self.port.write(request)
            return 115200
        self.port.write(cps_frame(0x62, 0, struct.pack('<I', rate)) + request +
                        cps_frame(0x62, 0, struct.pack('<I', 115200)))
        self.port.flush()
        actual = self._downlink_ack(rate)
        self.port.baudrate = rate
        return actual

    def _downlink_ack(self, rate):
        f = self._frame(time.monotonic() + 2)
        actual = 96000000 // round(96000000 / rate)
        if f != (0x62, 0, struct.pack('<I', actual)):
            raise InstallError('downlink baud acknowledgement mismatch')
        return actual

    def finish_downlink(self, rate):
        if rate != 115200:
            self._downlink_ack(115200)
            self.port.baudrate = 115200
            time.sleep(.08)
        if self.bad_response_crcs:
            raise InstallError('downlink CRC error')

    def _fast_read(self, addr, blocks, rate):
        if not 1 <= blocks <= 1024 or addr % 64 or addr < 0 or addr + blocks*256 > CHIP_BYTES:
            raise ValueError('invalid SPI burst bounds')
        start = addr // 64
        self.start_downlink(0x64, start, struct.pack('<H', blocks), rate)
        data = bytearray()
        for i in range(blocks):
            f = self._frame(time.monotonic() + 2)
            if f is None or f[:2] != (0x64, start + 4*i) or len(f[2]) != 256:
                raise InstallError('fast read frame missing or out of sequence')
            data.extend(f[2])
        self.finish_downlink(rate)
        return bytes(data)

    def _recover_fast_read(self):
        # Never send commands at an uncertain rate. A bounded burst ends, then
        # the cable idle watchdog resets UART to baseline.
        self.port.baudrate = 115200
        time.sleep(16)
        if not self.handshake(CABLE_HANDSHAKE, wait=5):
            raise InstallError('fast read recovery failed; reconnect radio')
        self.__dict__.pop('_info', None)

    def read(self, addr, n, window=8, progress=None):
        # Virtual/fixed-rate transports may implement byte I/O but not baud switching.
        if getattr(self.port, "baudrate", None) != 115200 or not callable(getattr(self.port, "flush", None)):
            return super().read(addr,n,window,progress)
        if addr % 64 or n < 4096 or addr < 0 or addr+n > CHIP_BYTES or getattr(self, '_fast_rate', None) == 0:
            return super().read(addr,n,window,progress)
        if not hasattr(self, '_fast_rate'):
            # q exists in the transport firmware. Do not queue baud commands
            # on old firmware that has never advertised transport support.
            try:
                stats = self.calls([(0x71,0,b'')], retries=0)[0]
                if len(stats) != 16: raise InstallError('no UART statistics')
            except InstallError:
                self._fast_rate=0
                return super().read(addr,n,window,progress)
            reference=super().read(addr,4096,window)
            self._fast_rate=0
            for rate in (500000,192000):
                try:
                    if self._fast_read(addr,16,rate)!=reference:
                        raise InstallError('fast read probe differs from baseline')
                except (InstallError,OSError,ValueError):
                    self._recover_fast_read()
                else:
                    self._fast_rate=rate
                    self.log(f'  negotiated SPI read speed: {rate} baud')
                    break
        if not self._fast_rate:
            return super().read(addr,n,window,progress)
        out=bytearray()
        try:
            while n-len(out)>=256:
                blocks=min((1024 if n >= 512*1024 else 256) if self._fast_rate >= 500000 else 64,(n-len(out))//256)
                out.extend(self._fast_read(addr+len(out),blocks,self._fast_rate))
                if progress:progress(len(out),n)
        except (InstallError,OSError,ValueError):
            self._fast_rate=0
            self._recover_fast_read()
            self.log('  fast read failed; retrying at 115200 baud')
        if len(out)<n:out.extend(super().read(addr+len(out),n-len(out),window))
        if progress:progress(n,n)
        return bytes(out)

    def library_directory(self):
        """Read the validated directory once per session, invalidated before mutations."""
        if hasattr(self, '_library_directory'):
            return self._library_directory.copy()
        if self.info()['protocol'] < 5:
            return None
        from k6_user_store_migrate import locations
        # Only the two directory sectors are needed for metadata validation.
        raw=self.read(LIB_BASE,8192)+bytes(512*1024-8192)
        try:
            self._library_directory = locations(raw)
            return self._library_directory.copy()
        except ValueError as exc:raise InstallError(str(exc)) from exc

    def library_read(self,slot,n):
        directory=self.library_directory()
        if directory is None:return self.read(LIB_BASE+slot*LIB_SLOT,n)
        if slot not in directory:return b'\xff'*n
        a,length=directory[slot]
        data=self.read(LIB_BASE+a,min(n,length))
        return data+b'\xff'*(n-len(data))

    def slot_head(self, slot):
        """(header, name) of what a library slot holds, or None if it's erased"""
        h = self.library_read(slot,64)
        if h[:3] != b"K6M":
            return None
        code = h[16:]
        return h[:16], k6mod.module_name(code)

    def theme_at(self, slot):
        """Read a validated, typed theme; never interpret a module as a theme."""
        from k6_theme_package import unpack
        if slot not in AUTO_SLOTS:
            return None
        address = LIB_BASE + slot * LIB_SLOT
        header = self.library_read(slot,12)
        if header[:4] not in (b"K6T\x06", b"K6T\x07", b"K6T\x08", b"K6T\x09"):
            return None
        size = 12 + int.from_bytes(header[4:6], "little")
        if not 12 <= size <= 8192:
            return None
        blob = self.library_read(slot,size)
        try:
            _, name = unpack(blob, include_name=True)
        except ValueError:
            return None
        return name or f"Theme {slot}", blob

    def remove_theme(self, slot, key, serial):
        if self.info()["protocol"] < 4:
            raise InstallError("Theme removal requires Designer firmware with cable protocol 4")
        if self.theme_at(slot) is None:
            raise InstallError(f"Slot {slot} does not contain a valid removable theme")
        img = k6mod.image(b"K6THDEL", 0, serial, key, lib=slot, theme=True, remove=True)
        r, _, _, result = self.send_image(img, serial & 255)
        if r != 3 or result != 0:
            raise InstallError(f"Theme removal refused: {r}, {result}")
        if self.library_read(slot,LIB_SLOT) != b"\xff" * LIB_SLOT:
            raise InstallError("Theme erase failed full-slot verification")

    def remove_slot(self, slot, key, serial):
        """Erase one module slot using an authenticated, replay-protected request image."""
        img = k6mod.image(b"K6ERASE", 0, serial, key, lib=slot, remove=True)
        transfer_error = None
        try:
            r, have, count, result = self.send_image(img, serial & 0xFF)
            if r != 3 or result:
                detail = KM.get(r, r) if r != 3 else KL.get(result, result)
                transfer_error = InstallError(f"radio erase reply: {detail}; accepted {have}/{count} chunks")
        except InstallError as exc:
            transfer_error = exc
        erased = self.library_read(slot,LIB_SLOT) == b"\xff" * LIB_SLOT
        if not erased:
            if transfer_error:
                raise transfer_error
            raise InstallError(f"slot {slot} failed full-slot erase verification")


# ---- the stock bootloader: catch its window, then flash the MCU (AA cmd arg len data CRC EF) ----

BOOT_HELLO, BOOT_DATA, BOOT_COUNT, BOOT_END = 0x01, 0x03, 0x04, 0x45
BOOT_PACKAGE = 1024
BOOT_ERRORS = {0xE1: "bad handshake", 0xE2: "CRC mismatch", 0xE3: "bad address",
               0xE4: "flash write failed", 0xE5: "bad command"}


def boot_frame(cmd, arg=0, data=b""):
    body = bytes([cmd, arg, len(data) >> 8, len(data) & 0xFF]) + bytes(data)
    c = crc16_xmodem(body)
    return bytes([0xAA]) + body + bytes([c >> 8, c & 0xFF, 0xEF])


def boot_answer(port, timeout, trace=None):
    """The bootloader's answer to a frame: (cmd, arg), or None"""
    deadline, buf, seen = time.monotonic() + timeout, b"", bytearray()
    while time.monotonic() < deadline:
        chunk = port.read(64)
        if chunk:
            seen.extend(chunk)
            buf += chunk
        i = buf.find(b"\xaa")
        while i >= 0 and len(buf) - i >= 5:
            n = buf[i + 3] << 8 | buf[i + 4]
            if len(buf) - i < 8 + n:
                break
            f = buf[i:i + 8 + n]
            if f[-1] == 0xEF and crc16_xmodem(f[1:5 + n]) == (f[5 + n] << 8 | f[6 + n]):
                return f[1], f[2]
            i = buf.find(b"\xaa", i + 1)
    if trace:
        trace("  bootloader RX timeout: " + (bytes(seen[:96]).hex(" ") if seen else "no bytes"))
    return None


def boot_call(port, cmd, arg, data, retries=5, timeout=2.0, quiet_ok=False, trace=None):
    for _ in range(retries):
        port.reset_input_buffer()
        port.write(boot_frame(cmd, arg, data))
        a = boot_answer(port, timeout, trace)
        if a is None:
            continue
        if a[1] == ACK:
            return
        if a[0] == 0xE2:
            continue
        raise InstallError(f"the bootloader refused command {cmd:#04x}: {BOOT_ERRORS.get(a[0], hex(a[0]))}")
    if quiet_ok:
        return
    raise InstallError(f"no answer from the bootloader to command {cmd:#04x}")


def boot_flash(port, image, log=print, trace=None):
    """Flash an image into the MCU: the radio must be in UPDATE mode (catch_window)"""
    n = -(-len(image) // BOOT_PACKAGE)
    if n > 256:
        raise InstallError(f"{len(image)} bytes is more than the bootloader takes")
    boot_call(port, BOOT_HELLO, 0, b"BOOTLOADER", trace=trace)
    boot_call(port, BOOT_COUNT, 0, bytes([n & 0xFF]), trace=trace)
    for seq in range(n):
        boot_call(port, BOOT_DATA, seq & 0xFF, image[seq * BOOT_PACKAGE:(seq + 1) * BOOT_PACKAGE].ljust(BOOT_PACKAGE, b"\0"), trace=trace)
        if seq % 8 == 7 or seq == n - 1:
            log(f"\r  flashing {seq + 1}/{n} KB", end="")
    log("")
    boot_call(port, BOOT_END, 0, b"", retries=2, quiet_ok=True, trace=trace)   # it may restart before it answers


def catch_window(port, window, trace=None):
    """Send the bootloader's UPDATE handshake until it answers (tools/k6_update.py)"""
    import k6_update
    return k6_update.catch_window(port, window, trace=trace)


# ---- keys, signing, bundles ----

def find_key(path=None, create=False, log=print, create_explicit=False):
    selected = path or os.environ.get("FLUX_KEY") or os.environ.get("BLOWFANG_KEY")
    if selected:
        p = os.path.expanduser(selected)
    else:
        p = next((os.path.expanduser(p) for p in DEFAULT_KEYS
                  if os.path.exists(os.path.expanduser(p))), os.path.expanduser(DEFAULT_KEYS[0]))
    if not os.path.exists(p):
        if not create or (selected and not create_explicit):
            raise InstallError(f"no module key at {p}: run `{sys.argv[0]} key`")
        os.makedirs(os.path.dirname(os.path.abspath(p)), mode=0o700, exist_ok=True)
        k6mod.new_key(p)
        log(f"made your module key {p} (keep it private and backed up)")
    try:
        state = k6mod.ensure_default_key(p)
    except (OSError, ValueError) as exc:
        raise InstallError(f"cannot use module key {p}: {exc}") from exc
    if state == "created":
        log(f"copied the module key to {k6mod.default_key_path()}")
    elif state == "different":
        log(f"WARNING: default key {k6mod.default_key_path()} is different; it was not replaced. "
            "Use the same --key-file for future operations on this radio.")
    return p


def keyed_image(data, key):
    """The image with your key over the placeholder. An image without the placeholder takes no
    modules (or was built keyed already): it is used as it is, and the caller says so."""
    n = data.count(k6mod.PLACEHOLDER)
    if n == 1:
        if key is None: raise InstallError("signed firmware needs a module key")
        return k6mod.key_image(data, key), True
    if n == 0:
        return data, False
    raise InstallError(f"the key placeholder is in the image {n} times")


class Serials:
    """Module serials: each newer than the last (the radio refuses an old one since power-on)"""

    def __init__(self, start=None):
        self.n = int(time.time()) if start is None else start

    def next(self):
        self.n += 1
        return self.n

    def advance_after(self, serial):
        """Keep generated serials above the radio's boot-local replay watermark."""
        self.n = max(self.n, int(serial))


def probe_image(serial, key):
    """A two-byte module (bx lr) that is never installed or run: the cable session only checks
    its tag, so the answer says whether the radio's key is yours"""
    return k6mod.image(b"\x70\x47", 0, serial, key)


AUTO_ID_MAGIC = b"K6AUTO1\0"
AUTO_SLOTS = tuple(range(9)) + tuple(range(16, 32))


def auto_marker(module_id):
    if not isinstance(module_id, str) or not module_id or len(module_id.encode('utf-8')) > 256:
        raise InstallError("automatic modules require a nonempty stable id (at most 256 UTF-8 bytes)")
    return AUTO_ID_MAGIC + hashlib.sha256(module_id.encode('utf-8')).digest()[:16]


def sign(entry, serial, key):
    """A bundle's library entry, signed for its slot"""
    code = entry.get("code")
    if code is None:
        code = open(entry["path"], "rb").read()
    if entry.get("_auto"):
        code += auto_marker(entry["id"])
    kind = entry["kind"]
    return k6mod.image(code, k6mod.bin_entry(code) if kind == "module" else 0, serial, key,
                       entry.get("level", 1), lib=entry["slot"], page=kind == "page", cards=kind == "cards")


def sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def load_bundle(d):
    m = json.load(open(os.path.join(d, "manifest.json")))
    if m.get("format") != 1:
        raise InstallError(f"{d}: manifest format {m.get('format')}, this tool reads 1")
    for name, want in m["sha256"].items():
        if sha256(os.path.join(d, name)) != want:
            raise InstallError(f"{d}/{name} doesn't match the manifest (damaged download?)")
    for e in m["library"]:
        e["path"] = os.path.join(d, e["file"])
    return m


def read_modules_list(path):
    out = []
    for ln in open(path):
        ln = ln.split("#")[0].split()
        if len(ln) == 2:
            out.append((int(ln[0]), ln[1]))
    return out


def cmd_module_bundle(a, log=print):
    """Package one module without binding it to a radio slot."""
    auto_marker(a.id)
    if a.source.endswith(".c"):
        code, entry = k6mod.build(a.source)
        level = k6mod.source_level(a.source) if a.level is None else a.level
        if entry != k6mod.bin_entry(code):
            raise InstallError("module entry cannot be recovered from its binary")
    else:
        if a.level is None:
            raise InstallError("--level is required for a prebuilt binary")
        code = open(a.source, "rb").read()
        level = a.level
    if not 1 <= level <= 255 or not code or len(code) + 48 > LIB_SLOT:
        raise InstallError("invalid API level or module too large for a slot")
    os.makedirs(a.output, exist_ok=True)
    filename = "module.bin"
    with open(os.path.join(a.output, filename), "wb") as f:
        f.write(code)
    manifest = {"format": 1, "build": "slot-independent module", "build_id": "0000000000000000",
                "library": [{"id": a.id, "kind": "module", "file": filename,
                             "name": k6mod.module_name(code) or a.id, "level": level}],
                "sha256": {filename: hashlib.sha256(code).hexdigest()}}
    with open(os.path.join(a.output, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    log(f"{a.output}: packaged {a.id} (automatic slot)")
    return 0


def cmd_bundle(a, log=print):
    """build.sh's (or a CI artifact's) out/ -> a release folder anyone can install from"""
    src, dst = a.out, a.output
    image = os.path.join(src, "aura-voice-explore.bin")
    data = open(image, "rb").read()
    if data.count(k6mod.PLACEHOLDER) != 1:
        raise InstallError(f"{image} has no key placeholder: it was built with a key "
                           "(K6_MOD_KEY_FILE). Bundle an unkeyed build (CI's), never a keyed one")
    files, lib = ["aura-voice-explore.bin"], []
    ids = set()
    for page, slot in sorted(PAGE_SLOTS.items(), key=lambda x: x[1]):
        f = f"page-{page}.bin"
        b = open(os.path.join(src, f), "rb").read()
        ids.add(b[-BUILD_ID:])
        files.append(f)
        lib.append(dict(slot=slot, kind="page", file=f, name=page.upper()))
    if len(ids) != 1:
        raise InstallError("the pages were linked against different images")
    build_id = ids.pop()
    if data.count(build_id) < 1:
        raise InstallError("the pages weren't linked against this image (its build ID isn't in it)")
    files.append("cards-core.bin")
    lib.append(dict(slot=CARD_SLOTS[0], kind="cards", file="cards-core.bin", name="HELP"))
    for slot, name in read_modules_list(a.modules):
        f = f"mod-{name}.bin"
        code = open(os.path.join(src, f), "rb").read()
        files.append(f)
        lib.append(dict(slot=slot, kind="module", file=f, name=k6mod.module_name(code) or name,
                        level=k6mod.source_level(os.path.join(ROOT, "c", "k6", "mod", name + ".c"))))
    extra = {}
    for key, f in (("cbi", "aura-pager-cbi.bin"), ("vlri", "aura-pager-vlri.bin")):
        if os.path.exists(os.path.join(src, f)):
            extra[key] = f
            files.append(f)
    os.makedirs(dst, exist_ok=True)
    for f in files:
        shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))
    build = open(os.path.join(src, "build-id.txt")).read().strip() if os.path.exists(os.path.join(src, "build-id.txt")) else ""
    with open(os.path.join(dst, "NOTICE.txt"), "w") as f:
        f.write(NOTICE.format(build=build or "(unknown)"))
    m = dict(format=1, build=build, image="aura-voice-explore.bin", build_id=build_id.hex(),
             codebook_images=extra, library=lib, sha256={f: sha256(os.path.join(dst, f)) for f in files})
    with open(os.path.join(dst, "manifest.json"), "w") as f:
        json.dump(m, f, indent=1)
    missing = [k for k in ("cbi", "vlri") if k not in extra]
    log(f"{dst}: {len(files)} files, image build ID {build_id.hex()}, {len(lib)} library entries"
        + (f"; no {' or '.join(missing)} image (a new radio needs them for the codebooks)" if missing else ""))
    return 0


NOTICE = """Flux firmware for the Baofeng UV-K6 / K61, release {build}

The .bin images are builds of Aura (https://github.com/sophiel-meow/bf-k61-firmware-aura, GPL-3.0)
with blowfang's patch (firmware/k6-aura/aura-pager.patch, GPL-3.0) and sotto's C library (MIT).
Their complete source is https://github.com/baitisj/blowfang (with its sotto submodule) and Aura
at the commit firmware/k6-aura/build.sh names. The modules (mod-*.bin), EXPLORE's code pages
(page-*.bin) and the help deck (cards-core.bin) are blowfang's own code, MIT.

Nothing here is Baofeng's: no stock firmware, no part of it. Flashing replaces the stock
firmware for good; back up your radio's SPI flash first (docs/manual/install.md).

The images carry a key placeholder, not anybody's key: tools/k6_install.py writes your own key
into the image on your machine and signs every library image with it as it sends it.
"""


# ---- the steps ----

def open_port(a):
    import serial
    if a.port:
        name = a.port
    else:
        from serial.tools import list_ports
        usb = [p.device for p in list_ports.comports() if p.vid is not None]
        if len(usb) != 1:
            raise InstallError("say which serial port is the K6 cable: --port /dev/ttyUSB0 (or COM5); "
                               f"USB serial ports here: {', '.join(usb) or 'none'}")
        name = usb[0]
    return serial.Serial(name, 115200, timeout=0.005)


def open_cable(port, log, wait):
    c = Cable(port, log)
    if not c.handshake(CABLE_HANDSHAKE, wait=wait):
        return None
    return c


def backup(link, path, log=print):
    t = time.monotonic()
    data = link.read(0, CHIP_BYTES, progress=lambda n, tot: log(f"\r  backup {n * 100 // tot}%", end="")
                     if n % (64 * 1024) == 0 or n == tot else None)
    log("")
    cal = link.read(0xF000, 0x1000)
    if cal != data[0xF000:0x10000]:
        raise InstallError("the calibration sector read differently twice: the cable is unreliable, try again")
    from k6_user_store_restore import persist
    persist(path,data)
    log(f"wrote {path}: {len(data) >> 10} KB in {time.monotonic() - t:.0f} s, sha256 {hashlib.sha256(data).hexdigest()[:16]}; "
        "keep it: it holds your radio's factory calibration")
    return data


def have_backup():
    d = os.path.expanduser("~/flux-backups")
    if not os.path.isdir(d) and os.path.isdir(os.path.expanduser("~/blowfang-backups")):
        d = os.path.expanduser("~/blowfang-backups")
    return os.path.isdir(d) and any(f.startswith("k6-spiflash-") for f in os.listdir(d))


def default_backup_path():
    d = os.path.expanduser("~/flux-backups")
    if not os.path.isdir(d) and os.path.isdir(os.path.expanduser("~/blowfang-backups")):
        d = os.path.expanduser("~/blowfang-backups")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "k6-spiflash-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%MZ") + ".bin")


def flash(port, image, key, how, log=print, ask=input, debug_boot=False):
    """Key the image and flash it. how: "cable" (a Flux radio: end the cable session, catch
    the window after its restart, no hands), "power" (catch a power-on), or "update" (the radio
    is already showing UPDATE, so start with the bootloader's flash protocol)."""
    data, keyed = keyed_image(image, key)
    trace = log if debug_boot else None
    if not keyed:
        log("  (no key placeholder; flashing image unchanged)")
    if how == "cable":
        c = Cable(port, log)
        c.call(CMD_END)
        time.sleep(0.03)
        ok = catch_window(port, 2.0, trace=trace)
        if not ok:
            raise InstallError("the bootloader didn't answer after the radio restarted; try the flash again")
        log("  the radio is in UPDATE mode")
    elif how == "power":
        ask("Switch the radio OFF, then press Enter here and switch it ON (no keys held) within 10 s... ")
        ok = catch_window(port, 12.0, trace=trace)
        if not ok:
            raise InstallError("the bootloader didn't answer: switch the radio off, press Enter and switch it on "
                               "again (or hold Side Keys 1 and 2 while switching it on)")
        log("  the radio is in UPDATE mode")
    elif how == "update":
        log("  using the bootloader already in UPDATE mode")
    else:
        raise InstallError(f"unknown flash mode: {how}")
    boot_flash(port, data, log, trace=trace)
    log("  flashed; the radio restarts (if its screen stays on UPDATE, switch it off and on)")


def check_key(c, key, serials, log=print):
    r, _, _, diag = c.send_image(probe_image(serials.next(), key), serials.n & 0xFF)
    if r == 3:
        return
    if r == -1:
        calc, received = (diag >> 16) & 0xFFFF, diag & 0xFFFF
        raise InstallError(f"the radio rejected the module packet CRC (calculated {calc:04x}, "
                           f"received {received:04x})")
    if r == -4 and key is None:
        raise InstallError("the radio rejected the CRC32 envelope")
    if r == -4:
        raise InstallError("the radio's image holds another key than yours: flash the image again with "
                           "`flash` (it keys it with yours), or pass the key it was keyed with (--key-file)")
    if r == -5:
        raise InstallError("the radio's image holds no key: flash it with this tool, which keys it")
    raise InstallError(f"the key check came back {KM.get(r, r)}")


def library_read(c,slot,n):
    # Legacy cable test doubles and older clients retain fixed-slot semantics.
    return c.library_read(slot,n) if hasattr(c,'library_read') else c.read(LIB_BASE+slot*LIB_SLOT,n)


def allocate_slots(c, entries, info, key):
    """Plan automatic entries without modifying the manifest or writing flash."""
    entries = [dict(e) for e in entries]
    automatic = [e for e in entries if e.get("slot") is None]
    if not automatic:
        return entries
    markers = []
    for e in automatic:
        if e.get("kind") != "module":
            raise InstallError("only modules may use automatic slots")
        markers.append(auto_marker(e.get("id")))
        if os.path.getsize(e["path"]) + len(markers[-1]) + 24 > min(info["arena"], LIB_SLOT):
            raise InstallError(f"{e.get('name', e['id'])}: image with identity does not fit the radio arena or slot")
    if len(set(markers)) != len(markers):
        raise InstallError("duplicate automatic module id in bundle")
    fixed = [e["slot"] for e in entries if e.get("slot") is not None]
    if any(not isinstance(s, int) or isinstance(s, bool) or not 0 <= s < info["slots"] for s in fixed):
        raise InstallError("invalid fixed slot")
    if len(set(fixed)) != len(fixed):
        raise InstallError("duplicate fixed slot in bundle")
    free, matches = [], {m: [] for m in markers}
    for slot in AUTO_SLOTS:
        if slot >= info["slots"] or slot in fixed:
            continue
        address = LIB_BASE + slot * LIB_SLOT
        head = library_read(c,slot,64)
        if head == b"\xff" * 64:
            free.append(slot)
            continue
        if head[:4] != b"K6M" + bytes([k6mod.ABI]) or head[13] != k6mod.LIB_FLAG:
            continue
        length = struct.unpack_from("<H", head, 4)[0]
        total = 16 + length + 8
        if length < len(AUTO_ID_MAGIC) + 16 or total > LIB_SLOT:
            continue
        image = library_read(c,slot,total)
        if image[-8:] != k6mod.image_tag(key, image[:-8]):
            continue
        marker = image[-8 - 24:-8]
        if marker in matches:
            matches[marker].append((slot, total))
    for e, marker in zip(automatic, markers):
        found = matches[marker]
        if len(found) > 1:
            raise InstallError(f"{e['id']}: installed in multiple slots; resolve manually")
        erase_bytes = ((os.path.getsize(e["path"]) + len(marker) + 24 + 4095) // 4096) * 4096
        if found:
            slot, old_total = found[0]
            area = library_read(c,slot,erase_bytes)
            if any(b != 0xff for b in area[old_total:]):
                raise InstallError(f"{e['id']}: unrelated data in update erase range; resolve manually")
            e["slot"] = slot
            e["_update"] = True
        else:
            while free:
                candidate = free.pop(0)
                if library_read(c,candidate,erase_bytes) == b"\xff" * erase_bytes:
                    e["slot"] = candidate
                    break
            else:
                raise InstallError("no free module slots; no library images were written")
        e["_auto"] = True
    return entries


def check_module_api(c, entries, log=print):
    """API 23+ hosts expose CAPS. Refuse incompatible uploads before allocating slots.
    Preserve legacy cable support for older modules whose hosts predate CAPS."""
    required = max((e.get("level", 1) for e in entries if e["kind"] == "module"), default=1)
    if required <= 22:
        return
    try:
        ceiling = c.capabilities()["module_api_max"]
    except InstallError as exc:
        raise InstallError(f"module requires API level {required}; cannot verify compatible "
                           f"firmware ({exc}). Update the radio firmware first.") from exc
    if ceiling < required:
        raise InstallError(f"module requires API level {required}, but radio supports level {ceiling}; "
                           "update the radio firmware first")
    log(f"  firmware API level {ceiling}; module requires {required}")


def cable_key(c, key=None, key_file=None, log=print):
    try:
        caps = c.capabilities()
    except (InstallError, AttributeError):
        caps = {}
    if caps.get("cable_only"):
        log("  cable-only firmware: CRC32 integrity, no module key")
        return None
    if key is not None:
        return key
    return k6mod.read_key(find_key(key_file, log=log))


def check_required_capabilities(c, required, entries=()):
    if not required:
        return
    caps = c.capabilities()
    missing = [cap for cap in required if not caps.get(cap)]
    if missing:
        raise InstallError("radio firmware lacks required module capabilities: " + ", ".join(missing))
    level = max((e.get("level", 1) for e in entries if e["kind"] == "module"), default=1)
    if caps.get("module_api_max", 0) < level:
        raise InstallError(f"module requires API level {level}; radio supports {caps.get('module_api_max', 0)}")


def load(c, bundle, key, serials, log=print, only=None, verify=False, force=False, skip_key_check=False, required_caps=()):
    check_required_capabilities(c, required_caps, [e for e in bundle["library"] if only is None or e.get("slot") in only])
    info = c.info()
    log(f"  the radio: cable session {info['protocol']}, {info['slots']} slots, "
        f"{info['arena']} B upload arena, "
        f"build ID {info['build_id'].hex() if info['build_id'] else '(none: not the voice + EXPLORE image)'}")
    if info['protocol'] >= 3:
        try:
            watermark = c.serial_watermark()
        except InstallError as exc:
            # Protocol 3 predates the optional read-only watermark command. Older
            # firmware rejects it with ERR_BAD_LEN; all other cable errors matter.
            if 'refused command 0x4a at 0: error 03' not in str(exc):
                raise
        else:
            serials.advance_after(watermark)
            log(f"  radio replay watermark {watermark}; next image serial {serials.n + 1}")
    if skip_key_check:
        log("  skipping the separate key probe; each module is still checked by the radio before install")
    else:
        check_key(c, key, serials, log)
    want = bytes.fromhex(bundle["build_id"])
    entries = [e for e in bundle["library"] if only is None or e.get("slot") in only]
    if info["build_id"] != want and not force:
        pages = [e for e in entries if e["kind"] == "page"]
        if pages:
            log("  skipping EXPLORE's pages: they are for another image than the radio's "
                f"(bundle {want.hex()}); flash the bundle's image first")
            entries = [e for e in entries if e["kind"] != "page"]
    check_module_api(c, entries, log)
    entries = allocate_slots(c, entries, info, key)
    for entry in entries:
        if entry.get("_auto"):
            action = "update" if entry.get("_update") else "install"
            log(f"  automatic {action}: {entry['id']} -> slot {entry['slot']}")
    write_entries(c, entries, key, serials, log, verify)


def write_entries(c, entries, key, serials, log=print, verify=False, skip_current=False):
    """Write a fully allocated plan and verify each automatic module."""
    order = {"page": 0, "cards": 1, "module": 2}
    entries.sort(key=lambda e: (order[e["kind"]], e["slot"]))
    t, total = time.monotonic(), 0
    for e in entries:
        img = sign(e, serials.next(), key)
        slot = LIB_BASE + e["slot"] * LIB_SLOT
        if skip_current and e.get("_update"):
            installed = library_read(c,e["slot"],len(img))
            # Match all signed fields except the upload serial, plus the code.
            if (installed[:8] == img[:8] and installed[12:-8] == img[12:-8]
                    and hmac.compare_digest(installed[-8:], k6mod.image_tag(key, installed[:-8]))):
                log(f"  slot {e['slot']:2d} {e['name']}: already verified")
                continue
        r, have, count, put = c.send_image(img, serials.n & 0xFF)
        if r != 3 or put != len(img):
            # the last chunk's answer may have been lost and the chunk sent again (it then
            # starts a transfer of its own): the slot itself says whether the install happened
            if r == 3 or library_read(c,e["slot"],len(img)) != img:
                if r == 0:
                    why = "radio did not recognize the module packet (restart the cable session)"
                    if c.km_diag_stage is not None:
                        why += f"; module-loader diagnostic stage {c.km_diag_stage}"
                else:
                    why = KM.get(r, r) if r != 3 else KL.get(put, put)
                detail = f"radio accepted {have}/{count} chunks"
                if count > have:
                    detail += f" ({count - have} still missing)"
                raise InstallError(f"slot {e['slot']} ({e['name']}): {why}; {detail}")
        elif (verify or e.get("_auto")) and library_read(c,e["slot"],len(img)) != img:
            raise InstallError(f"slot {e['slot']} ({e['name']}) reads back differently")
        total += len(img)
        log(f"  slot {e['slot']:2d} {e['kind']:6s} {e['name']:12s} {len(img):6d} B")
    log(f"  {len(entries)} library images, {total} B, in {time.monotonic() - t:.0f} s")


def codebooks(c):
    """Which codebook images are missing from the radio's SPI flash"""
    out = []
    for addr, magic, what, img in CODEBOOKS:
        if c.read(addr, 64)[:4] != magic:
            out.append((what, img))
    return out


def wait_line(port, prefix, timeout, log=print):
    """Read report lines until one starts with prefix; the line, or None"""
    deadline, buf = time.monotonic() + timeout, b""
    while time.monotonic() < deadline:
        buf += port.read(256)
        while b"\n" in buf:
            ln, buf = buf.split(b"\n", 1)
            ln = ln.decode("ascii", "replace").strip()
            if ln:
                log("  K6: " + ln[:120])
            if ln.startswith(prefix):
                return ln
    return None


def first_boot(port, backup_path, log=print, ask=input, timeout=90.0):
    """After an image's first flash on a radio Aura never formatted: back up at FORMAT FLASH?,
    then the owner presses MENU. Ends at the image's K6 CB line (its codebook check)."""
    link = CpsLink(port, log)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if link.handshake(AURA_HANDSHAKE, wait=3.0, fresh=False):
            log("  the radio shows FORMAT FLASH?: backing up its SPI flash before anything is erased")
            backup(link, backup_path, log)
            link.end()
            ask("Now press MENU on the radio to format, then press Enter here... ")
            break
        if any(ln.startswith("K6 CB") for ln in link.lines):
            return True                                       # formatted already: no prompt
    return wait_line(port, "K6 CB", 60.0, log) is not None


def cmd_install(a, bundle, port, key, log=print, ask=input):
    serials = Serials()
    log("1. Looking for the radio on the cable (have it on, in EXPLORE or the arena)...")
    c = None if a.fresh else open_cable(port, log, wait=6.0)
    if c:
        log("   It runs Flux-compatible firmware already: an update, no hands needed.")
        need = codebooks(c)
        if a.backup or not (a.no_backup or have_backup()):
            path = a.backup or default_backup_path()
            log(f"2. Backing up the SPI flash to {path} (once per radio is enough)")
            backup(c, path, log)
        elif a.no_backup:
            log("2. No backup (--no-backup)")
        else:
            log("2. An existing SPI backup was found: not backing up again")
        if need:
            raise InstallError("the radio lacks " + " and ".join(w for w, _ in need) +
                               ": run install with --fresh (it flashes the codebook installers)")
        log("3. Flashing the core image...")
        flash(port, open(os.path.join(a.bundle, bundle["image"]), "rb").read(), key, "cable", log, ask)
    else:
        if a.fresh:
            log("   --fresh: installing as on a new radio (codebook installers first, three power-ons).")
        else:
            log("   No Flux cable session: treating it as a new radio (stock, or another firmware).")
        imgs = bundle.get("codebook_images", {})
        for k in ("cbi", "vlri"):
            if k not in imgs:
                raise InstallError(f"the bundle has no {k} image, which a new radio needs for its codebooks")
        if not a.yes:
            log("\nThis replaces the radio's firmware for good: Baofeng's stock firmware can't be read out")
            log("or put back from here. Your SPI flash (factory calibration) is backed up first.")
            if ask("Type YES to go on: ").strip() != "YES":
                log("stopped; nothing was written")
                return 1
        log("2. Flashing the codebook installer (sotto's codebook into SPI flash, once)...")
        flash(port, open(os.path.join(a.bundle, imgs["cbi"]), "rb").read(), key, "power", log, ask)
        if not first_boot(port, a.backup or default_backup_path(), log, ask):
            raise InstallError("no K6 CB line after the first boot: is the radio on, and the cable in?")
        log("3. Flashing the sotto 600 / 400 codebook installer...")
        flash(port, open(os.path.join(a.bundle, imgs["vlri"]), "rb").read(), key, "power", log, ask)
        if wait_line(port, "K6 VLR", 60.0, log) is None:
            raise InstallError("no K6 VLR line: the second codebook install didn't report")
        log("4. Flashing the core image...")
        flash(port, open(os.path.join(a.bundle, bundle["image"]), "rb").read(), key, "power", log, ask)
    log("5. Waiting for the new image (it shows PAGES MISSING until its pages are in)...")
    time.sleep(3.0)
    c = open_cable(port, log, wait=40.0)
    if not c:
        raise InstallError("the new image doesn't answer on the cable: switch the radio off and on, then run "
                           f"`{sys.argv[0]} load {a.bundle}`")
    log("6. Loading EXPLORE's pages, the help deck and the modules over the cable...")
    load(c, bundle, cable_key(c, key, a.key_file, log), serials, log, verify=a.verify)
    c.end()
    log("\nDone. The radio restarts into EXPLORE. Hold MENU on any screen for its help card.")
    return 0


def install_boot_logo(port, image_path, yes=False, log=print, ask=input):
    image = open(image_path, "rb").read(1025)
    if len(image) != 1024 or image in (bytes(1024), b"\xff" * 1024):
        raise InstallError("boot bitmap must contain 1024 bytes of visible page-major artwork")
    link = CpsLink(port, log)
    if not link.handshake(AURA_HANDSHAKE, wait=8.0):
        raise InstallError("no Aura cable session for the boot bitmap; have the radio on its normal screen")
    try:
        old = b"".join(link.call(CMD_READ_LOGO, offset) for offset in range(0, 1024, 64))
        if len(old) != 1024:
            raise InstallError("could not read the existing boot bitmap before replacement")
        if old == image:
            log("Full-screen boot bitmap is already current on the radio.")
            return
        backup_dir = os.path.expanduser("~/flux-backups")
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)
        name = "k6-boot-logo-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(3) + ".bin"
        backup_path = os.path.join(backup_dir, name)
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(old)
        log(f"  backed up the existing boot bitmap to {backup_path}")
        if not yes and ask("Replace the radio's full-screen boot bitmap? Type INSTALL: ").strip() != "INSTALL":
            raise InstallError("boot bitmap installation cancelled")
        for offset in range(0, 1024, 64):
            if link.call(CMD_WRITE_LOGO, offset, image[offset:offset+64]):
                raise InstallError(f"unexpected write reply at boot bitmap offset {offset}")
        readback = b"".join(link.call(CMD_READ_LOGO, offset) for offset in range(0, 1024, 64))
        if readback != image:
            raise InstallError(f"boot bitmap readback differs; restore the backup at {backup_path}")
        log("Full-screen boot bitmap verified on the radio; it appears at the next startup.")
    finally:
        link.end()


def select_theme_install(link, blob, requested_slot=None):
    """Reuse identical design, otherwise use an empty slot with a distinct display name.

    Never erase an earlier revision or reinterpret an occupied module as a theme.
    """
    from k6_theme_package import pack, unpack, display_name
    token, name = unpack(blob, include_name=True)
    existing = {}
    # Some legacy test/transport implementations cannot enumerate typed themes.
    if callable(getattr(link, 'theme_at', None)):
        for slot in AUTO_SLOTS:
            item = link.theme_at(slot)
            if item is not None:
                existing[slot] = item
    if requested_slot is None:
        # Identical design with original name or previously assigned slot suffix.
        for slot, (label, installed) in existing.items():
            if installed == blob or (name and label == revision_theme_name(name, slot)
                                     and installed == pack(token,label)):
                return slot, installed, True
        slot = find_empty_theme_slot(link)
    else:
        slot = requested_slot
        if slot in existing and existing[slot][1] == blob:
            return slot, blob, True
    if library_read(link,slot,LIB_SLOT) != b'\xff'*LIB_SLOT:
        raise InstallError("theme slot is not entirely erased")
    if name and any(label.casefold() == name.casefold() or label.casefold() == revision_theme_name(name, old_slot).casefold()
                    for old_slot,(label,_) in existing.items()):
        label = revision_theme_name(name,slot)
        blob = pack(token,label)
    return slot,blob,False


def revision_theme_name(name, slot):
    from k6_theme_package import display_name
    suffix = f"-{slot:02d}"
    return display_name(name)[:14-len(suffix)].rstrip() + suffix


def find_empty_theme_slot(link):
    """Choose a fully erased non-reserved library slot without overwriting data."""
    for slot in (*range(16, 32), *range(0, 9)):
        address=LIB_BASE+slot*LIB_SLOT
        if library_read(link,slot,LIB_SLOT)==b"\xff"*LIB_SLOT:
            return slot
    raise InstallError("no empty theme slot is available; remove a theme or module slot first")


def report_library(c, log, *, live=False):
    info = c.info()
    log(f"Flux {'live serial, storage protocol' if live else 'cable session'} {info['protocol']}, build ID "
        f"{info['build_id'].hex() if info['build_id'] else '(not the voice + EXPLORE image)'}")
    for what, img in codebooks(c):
        log(f"missing: {what} (the {img} image installs it)")
    for s in range(LIB_SLOTS):
        if info["protocol"] >= 3:
            theme = c.theme_at(s)
            if theme is not None:
                name, blob = theme
                log(f"slot {s:2d} theme  {name:12s} {len(blob):6d} B code, serial 0")
                log(f"theme revision {s} v{blob[3]} sha256 {hashlib.sha256(blob).hexdigest()}")
                continue
        h = c.slot_head(s)
        if h:
            hdr, name = h
            code, serial, flags = struct.unpack_from("<H", hdr, 4)[0], struct.unpack_from("<I", hdr, 8)[0], hdr[13]
            kind = "page" if flags & 2 else "cards" if flags & 4 else "module"
            log(f"slot {s:2d} {kind:6s} {name or '(unnamed)':12s} {code:6d} B code, serial {serial}")


def main(argv=None, log=None, ask=input):
    log = log or (lambda *x, **k: print(*x, **k, flush=True))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="the K6 programming cable's serial port (default: the only USB serial port)")
    ap.add_argument("--key-file", help="your module key (default: $FLUX_KEY or legacy $BLOWFANG_KEY, ~/.config/flux/k6-mod.key, "
                                       "or ~/.config/sotto/k6-mod.key)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help="everything, step by step")
    i.add_argument("bundle")
    i.add_argument("--backup", help="where the SPI flash backup goes (default ~/flux-backups/...)")
    i.add_argument("--no-backup", action="store_true", help="an update: skip the backup (it is skipped "
                                                           "anyway once ~/flux-backups holds one)")
    i.add_argument("--verify", action="store_true", help="read every library slot back after writing it")
    i.add_argument("--yes", action="store_true", help="don't ask before replacing the stock firmware")
    i.add_argument("--fresh", action="store_true", help="install as on a new radio even if Flux modules are already installed "
                                                       "(flashes the codebook installers again)")
    b = sub.add_parser("backup", help="the whole 2 MB SPI flash to a file")
    b.add_argument("-o", "--output")
    b.add_argument("--legacy", action="store_true", help="explicitly use rebooting cable/stock programming protocol")
    f = sub.add_parser("flash", help="key an image with your key and flash it")
    f.add_argument("image")
    fg = f.add_mutually_exclusive_group()
    fg.add_argument("--power", action="store_true", help="catch the bootloader as you switch the radio on, "
                                                         "even if Flux is already installed")
    fg.add_argument("--already-update", action="store_true", help="flash directly when the radio is already "
                                                                   "showing UPDATE")
    f.add_argument("--debug-boot", action="store_true", help="show raw bootloader reply bytes on timeouts")
    ld = sub.add_parser("load", help="the library: EXPLORE's pages, the help deck, the modules")
    ld.add_argument("bundle")
    ld.add_argument("--only", help="these slots only, e.g. 0,13,26")
    ld.add_argument("--verify", action="store_true")
    ld.add_argument("--force", action="store_true", help="pages even if the radio's build ID differs")
    ld.add_argument("--require-capability", action="append", default=[], choices=("module_loader", "scanner", "fsk_rx", "designer", "sotto", "ota"), help="refuse before writing if firmware lacks a requested service")
    ld.add_argument("--skip-key-check", action="store_true", help="skip the standalone probe and try the selected image")
    rm = sub.add_parser("remove-slot", help="erase one module slot from the radio")
    rm.add_argument("slot", type=int, help="module slot number; firmware pages and decks are protected")
    rm.add_argument("--yes", action="store_true", help="confirm erasing this occupied module slot")
    rms = sub.add_parser("remove-slots", help="erase selected module slots or all removable modules")
    rms.add_argument("slots", nargs="?", help="comma-separated module slots, e.g. 0,3,18")
    rms.add_argument("--all-modules", action="store_true", help="select every occupied removable module slot")
    rms.add_argument("--yes", action="store_true", help="confirm the removal operation")
    th = sub.add_parser("install-theme", help="install a named data-only theme into an empty library slot")
    th.add_argument("theme")
    th.add_argument("--slot", default="auto", help="empty slot number, or auto to choose one safely")
    th.add_argument("--yes", action="store_true")
    rt = sub.add_parser("remove-theme", help="erase and unregister one typed Designer theme")
    rt.add_argument("slot", type=int)
    rt.add_argument("--yes", action="store_true")
    splash = sub.add_parser("install-boot-logo", help="install a 1024-byte full-screen boot bitmap, backing up the old one")
    splash.add_argument("image")
    splash.add_argument("--yes", action="store_true")
    status = sub.add_parser("status", help="what the radio runs, its codebooks and its library (live by default)")
    status.add_argument("--legacy", action="store_true", help="explicitly use rebooting cable session")
    sub.add_parser("key", help="make your module key")
    bd = sub.add_parser("bundle", help="a release folder from build.sh's or CI's out/")
    bd.add_argument("out")
    bd.add_argument("-o", "--output", required=True)
    bd.add_argument("--modules", default=os.path.join(HERE, "k6_release_modules.txt"))
    mb = sub.add_parser("module-bundle", help="package one module with an automatic slot")
    mb.add_argument("source", help="module C source or prebuilt binary")
    mb.add_argument("--id", required=True, help="stable globally unique module ID")
    mb.add_argument("--level", type=int, help="module API level (required for a binary)")
    mb.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "key":
            p = find_key(a.key_file, create=True, log=log, create_explicit=True)
            log(f"your key: {p}")
            return 0
        if a.cmd == "bundle":
            return cmd_bundle(a, log)
        if a.cmd == "module-bundle":
            return cmd_module_bundle(a, log)
        key = None
        if a.cmd == "install":
            bundle = load_bundle(a.bundle)
            image = open(os.path.join(a.bundle, bundle["image"]), "rb").read()
            if image.count(k6mod.PLACEHOLDER) == 1 or a.key_file:
                key = k6mod.read_key(find_key(a.key_file, create=True, log=log))
        elif a.cmd == "flash" and open(a.image,"rb").read().count(k6mod.PLACEHOLDER) == 1:
            key = k6mod.read_key(find_key(a.key_file, create=True, log=log))
        bundle = load_bundle(a.bundle) if a.cmd in ("install", "load") else None
        port = open_port(a)
        try:
            if a.cmd == "install-boot-logo":
                install_boot_logo(port, a.image, a.yes, log, ask)
                return 0
            if a.cmd == "install":
                return cmd_install(a, bundle, port, key, log, ask)
            if a.cmd == "status" and not a.legacy:
                from k6_live import LiveLink
                from k6_port_lock import serial_lock
                with serial_lock(a.port or port.port):
                    link = LiveLink(port)
                    link.status()
                    report_library(link, log, live=True)
                return 0
            if a.cmd == "backup":
                path = a.output or default_backup_path()
                if not a.legacy:
                    from k6_live import LiveLink
                    from k6_port_lock import serial_lock
                    with serial_lock(a.port or port.port):
                        link = LiveLink(port)
                        link.status()
                        backup(link, path, log)
                    return 0
                c = open_cable(port, log, wait=4.0)
                link = c or CpsLink(port, log)
                if not c and not link.handshake(AURA_HANDSHAKE, wait=4.0):
                    raise InstallError("the radio answers neither Flux's cable session nor its original programming session: have it on, "
                                       "in EXPLORE or the arena (or at Aura's FORMAT FLASH? screen)")
                backup(link, path, log)
                link.end()
                return 0
            if a.cmd == "flash":
                how = "power"
                if a.already_update:
                    how = "update"
                elif not a.power and open_cable(port, log, wait=3.0):
                    how = "cable"
                flash(port, open(a.image, "rb").read(), key, how, log, ask, a.debug_boot)
                return 0
            c = open_cable(port, log, wait=20.0)
            if not c:
                raise InstallError("no cable session: have the radio on a Flux image, in EXPLORE or the arena")
            if a.cmd in ("load", "install-theme", "remove-theme", "remove-slot", "remove-slots"):
                key = cable_key(c, key_file=a.key_file, log=log)
            if a.cmd == "load":
                only = {int(s) for s in a.only.split(",")} if a.only else None
                try:
                    load(c, bundle, key, Serials(), log, only, a.verify, a.force, a.skip_key_check, a.require_capability)
                except InstallError:
                    # Don't leave the radio in a half-finished cable/module-packet session. The
                    # next attempt starts from a fresh radio state instead of inheriting it.
                    c.end()
                    raise
            elif a.cmd == "install-theme":
                from k6_theme_package import pack,unpack
                theme_token,theme_name=unpack(open(a.theme,"rb").read(8193),include_name=True)
                blob=pack(theme_token,theme_name)
                info=c.info()
                if info["protocol"]<3:
                    raise InstallError("Designer typed theme protocol 3 is required")
                try:
                    requested=None if a.slot=="auto" else int(a.slot)
                except ValueError:
                    raise InstallError("theme slot must be auto or a number") from None
                if requested is not None and requested not in AUTO_SLOTS:
                    raise InstallError("theme slot must be a non-reserved library slot")
                slot,blob,current=select_theme_install(c,blob,requested)
                _,theme_name=unpack(blob,include_name=True)
                if current:
                    log(f"Theme verified in slot {slot}: {theme_name!r} is already current; no duplicate installed.")
                    c.end()
                    return 0
                if not a.yes and ask(f"Install {theme_name or 'theme'} in empty slot {slot}? Type INSTALL: ").strip()!="INSTALL":
                    raise InstallError("theme installation cancelled")
                serials=Serials()
                try:
                    serials.advance_after(c.serial_watermark())
                except InstallError as exc:
                    if 'refused command 0x4a at 0: error 03' not in str(exc):
                        raise
                serial=serials.next()
                img=k6mod.image(blob,0,serial,key,lib=slot,theme=True)
                r,have,count,result=c.send_image(img,serial&255)
                if r!=3 or result!=len(blob) or library_read(c,slot,len(blob))!=blob:
                    raise InstallError(f"theme install/readback failed: {r}, {result}")
                if theme_name:
                    log(f"Theme verified in slot {slot}: {theme_name!r} is registered in DISP > THEME by name.")
                else:
                    log(f"Theme verified in slot {slot}; select DISP > THEME on radio.")
            elif a.cmd == "remove-theme":
                try:
                    if c.info()["protocol"] < 4:
                        raise InstallError("Theme removal requires Designer firmware with cable protocol 4")
                    item = c.theme_at(a.slot)
                    if item is None:
                        raise InstallError("Selected slot is not a valid theme")
                    if not a.yes and ask(f"Erase theme {item[0]}? Type ERASE: ").strip() != "ERASE":
                        raise InstallError("Theme removal cancelled")
                    serials = Serials()
                    serials.advance_after(c.serial_watermark())
                    c.remove_theme(a.slot, key, serials.next())
                    log(f"Theme {item[0]} removed and unregistered from DISP > THEME; slot {a.slot} verified erased")
                finally:
                    c.end()
                return 0
            elif a.cmd == "remove-slot":
                info = c.info()
                if info["protocol"] < 2:
                    raise InstallError("this radio firmware has no safe module-removal protocol; "
                                       "flash an image with cable protocol 2 first")
                if not (0 <= a.slot <= 8 or 16 <= a.slot < LIB_SLOTS):
                    raise InstallError("only module slots 0-8 and 16-31 can be erased")
                head = c.slot_head(a.slot)
                if head is None:
                    raise InstallError(f"slot {a.slot} is already empty")
                flags = head[0][13]
                if not (flags & 1) or flags & (2 | 4):
                    raise InstallError(f"slot {a.slot} is not a removable module; reserved pages and help decks are protected")
                if not a.yes and ask(f"Erase module {head[1] or '(unnamed)'} from slot {a.slot}? Type ERASE to continue: ").strip() != "ERASE":
                    raise InstallError("erase cancelled")
                log(f"  erasing module {head[1] or '(unnamed)'} from slot {a.slot}...")
                c.remove_slot(a.slot, key, Serials().next())
                if c.slot_head(a.slot) is not None:
                    raise InstallError(f"slot {a.slot} still appears occupied after erase")
                log(f"  slot {a.slot} erased; the radio is restarting")
            elif a.cmd == "remove-slots":
                if c.info()["protocol"] < 2:
                    raise InstallError("this radio firmware has no safe module-removal protocol; "
                                       "flash an image with cable protocol 2 first")
                if a.all_modules == bool(a.slots):
                    raise InstallError("choose either comma-separated slots or --all-modules")
                if a.all_modules:
                    requested = list(range(9)) + list(range(16, LIB_SLOTS))
                else:
                    try:
                        requested = [int(part) for part in a.slots.split(",")]
                    except ValueError as exc:
                        raise InstallError("slots must be comma-separated integers") from exc
                    if not requested or len(set(requested)) != len(requested):
                        raise InstallError("select at least one unique module slot")
                removable = []
                for slot in requested:
                    if not (0 <= slot <= 8 or 16 <= slot < LIB_SLOTS):
                        raise InstallError(f"slot {slot} is reserved; only module slots 0-8 and 16-31 can be erased")
                    head = c.slot_head(slot)
                    if head is None:
                        if a.all_modules:
                            continue
                        raise InstallError(f"slot {slot} is already empty")
                    flags = head[0][13]
                    if not (flags & 1) or flags & (2 | 4):
                        if a.all_modules:
                            continue
                        raise InstallError(f"slot {slot} is not a removable module; reserved pages and help decks are protected")
                    removable.append((slot, head[1]))
                if not removable:
                    raise InstallError("no removable modules are installed")
                names = ", ".join(f"{name or '(unnamed)'} (slot {slot})" for slot, name in removable)
                if not a.yes and ask(f"Erase {len(removable)} module(s): {names}? Type ERASE to continue: ").strip() != "ERASE":
                    raise InstallError("erase cancelled")
                for slot, name in removable:
                    log(f"  erasing module {name or '(unnamed)'} from slot {slot}...")
                    c.remove_slot(slot, key, Serials().next())
                    if c.slot_head(slot) is not None:
                        raise InstallError(f"slot {slot} still appears occupied after erase")
                    log(f"  slot {slot} erased")
                log("  radio restarting when the cable session ends")
            else:
                report_library(c, log)
            c.end()
            return 0
        finally:
            port.close()
    except (InstallError, OSError) as e:
        log(f"\nstopped: {e}")
        return 1


if __name__ == "__main__":
    # Share exception classes with the imported live client.
    sys.modules.setdefault("k6_install",sys.modules[__name__])
    sys.exit(main())
