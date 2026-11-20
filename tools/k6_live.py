#!/usr/bin/env python3
"""Non-rebooting Flux live control. Requires a K6_LIVE_SERIAL=1 host image."""
import argparse
import json
import secrets
import struct
import sys
import time
from types import SimpleNamespace

from k6_install import CpsLink, InstallError, crc16_xmodem, open_port
from k6_port_lock import serial_lock

MAGIC = b"FLXLV1"
STATUS, READ, TUNE = 0x60, 0x61, 0x62
RUN, RUN_EVENT = 0x66, 0x67
MODES = ("FM", "AM", "USB", "CW", "CWF")
ERRORS = {1: "Radio busy; return to standby, release PTT or disable the conflicting mode.",
          2: "Unsupported command, address or frequency.", 3: "SPI read failed."}


class LiveUnavailable(InstallError):
    """No live protocol response. Never implicitly open the rebooting loader."""


def request_bytes(cmd, seq, arg=0):
    body = MAGIC + struct.pack("<BBI", cmd, seq, arg)
    return body + struct.pack(">H", crc16_xmodem(body))


def decode_status(d):
    if len(d) != 26 or d[:5] != b"FLIV\x01":
        raise InstallError("Unsupported live status format")
    master, watching, flags, a, b, cv, rssi, ma, mb, wa, wb = struct.unpack("<BBBIIHiBBBB", d[5:])
    if master > 1 or watching > 1 or ma >= len(MODES) or mb >= len(MODES):
        raise InstallError("Invalid live status response")
    return dict(version=1, master=master, watching=watching, transmitting=bool(flags & 1),
                squelch_open=bool(flags & 2), standby_idle=bool(flags & 4),
                sleeping=bool(flags & 8), battery_volts=cv / 100, rssi_dbm=rssi,
                vfos=[dict(hz=hz, mode=MODES[mod], wide=bool(wide), memory=bool(flags & bit))
                      for hz, mod, wide, bit in ((a, ma, wa, 16), (b, mb, wb, 32))])


class LiveLink(CpsLink):
    def __init__(self, port, timeout=1.0, *, initial_seq=None):
        super().__init__(port, log=lambda *_: None, timeout=timeout)
        self.seq = secrets.randbelow(256) if initial_seq is None else initial_seq

    def request(self, cmd, arg=0):
        self.seq = (self.seq + 1) % 256
        self.port.write(request_bytes(cmd, self.seq, arg))
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            frame = self._frame(deadline)
            if frame is None:
                break
            c, seq, d = frame
            if (c, seq) != (cmd, self.seq):
                continue
            if not d:
                raise InstallError("Empty live reply")
            if d[0]:
                raise InstallError(ERRORS.get(d[0], "Unknown live radio error"))
            return d[1:]
        raise LiveUnavailable("No live response. Use a live-enabled Flux image in standby; no reset was requested.")

    def status(self):
        return decode_status(self.request(STATUS))

    def tune(self, hz):
        if not (136_000_000 <= hz <= 174_000_000 or 400_000_000 <= hz <= 480_000_000) or hz % 10:
            raise ValueError("Expected 136–174 or 400–480 MHz at 10 Hz resolution.")
        if self.request(TUNE, hz):
            raise InstallError("Unexpected tune reply")
        return self.status()

    def run_module(self, slot, *, timeout=30.0, allow_higher_api=False, on_report=None):
        """Queue exactly once; collect framed reports and completion without more writes."""
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < 32:
            raise ValueError("Module slot must be an integer from 0 to 31")
        if not 0 < timeout <= 3600:
            raise ValueError("Module wait timeout must be in (0, 3600] seconds")
        try:
            if self.request(RUN, slot | (0x100 if allow_higher_api else 0)):
                raise InstallError("Unexpected launch acknowledgment")
        except LiveUnavailable as exc:
            raise LiveUnavailable("Launch acknowledgment lost; execution unknown. Do not retry blindly.") from exc
        seq = self.seq
        deadline = time.monotonic() + timeout
        reports = []
        while time.monotonic() < deadline:
            frame = self._frame(deadline)
            if frame is None:
                break
            cmd, received_seq, data = frame
            if (cmd, received_seq) != (RUN_EVENT, seq):
                continue
            if not data:
                raise InstallError("Empty module event; execution state unknown")
            if data[0] == 0 and len(data) <= 61:
                line = data[1:].decode("utf-8", "replace")
                reports.append(line)
                if on_report is not None:
                    on_report(line)
            elif data[0] == 1 and len(data) == 11:
                got_slot, load, returned, ret = struct.unpack("<BiBi", data[1:])
                if got_slot != slot or returned not in (0, 1) or (returned and load <= 0):
                    raise InstallError("Invalid module completion; execution state unknown")
                return dict(slot=slot, load=load, returned=bool(returned),
                            result=ret if returned else None, reports=reports)
            else:
                raise InstallError("Invalid module event; execution state unknown")
        raise LiveUnavailable("Module completion not received; it may still be running. Do not retry blindly.")

    def read(self, addr, n, window=1):
        if addr < 0 or addr % 64 or n < 0 or not (addr + n <= 0xb040 or (addr >= 0xf000 and addr + n <= 0x10000)):
            raise ValueError("Live reads require 64-byte alignment within memory/settings SPI.")
        blocks = bytearray()
        for offset in range(addr, addr + n, 64):
            block = self.request(READ, offset)
            if len(block) != 64:
                raise InstallError("Short live SPI read")
            blocks.extend(block)
        return bytes(blocks[:n])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    tune = sub.add_parser("tune")
    tune.add_argument("hz", type=int)
    run = sub.add_parser("run", help="launch an installed foreground module; wait for reports and completion")
    run.add_argument("slot", type=int)
    run.add_argument("--timeout", type=float, default=30)
    run.add_argument("--allow-higher-api", action="store_true",
                     help="explicitly allow modules above level 2 (may transmit or write hardware)")
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=0.5)
    a = p.parse_args()
    if a.cmd == "watch" and a.interval < 0.2:
        p.error("Minimum watch interval is 0.2 s")
    try:
        with serial_lock(a.port), open_port(SimpleNamespace(port=a.port)) as port:
            link = LiveLink(port)
            if a.cmd == "watch":
                while True:
                    print(json.dumps(link.status()), flush=True)
                    time.sleep(a.interval)
            elif a.cmd == "run":
                result = link.run_module(a.slot, timeout=a.timeout,
                                         allow_higher_api=a.allow_higher_api,
                                         on_report=lambda line: print(line, file=sys.stderr, flush=True))
                print(json.dumps(result, indent=2))
                if not result["returned"]:
                    p.exit(1, "Module did not execute to completion\n")
            elif a.cmd == "tune":
                print(json.dumps(link.tune(a.hz), indent=2))
            else:
                print(json.dumps(link.status(), indent=2))
    except KeyboardInterrupt:
        pass
    except (InstallError, OSError, ValueError) as exc:
        p.exit(1, f"{exc}\n")


if __name__ == "__main__":
    main()
