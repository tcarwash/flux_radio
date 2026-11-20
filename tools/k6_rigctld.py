#!/usr/bin/env python3
"""Loopback-only Hamlib NET rigctl bridge for Flux (RX/status and CAT tuning)."""
import argparse
import math
import socketserver
import threading
from types import SimpleNamespace

from k6_install import InstallError, open_port
from k6_live import LiveLink
from k6_port_lock import serial_lock

# Hamlib dump_state v1. RX ranges only; no advertised TX capabilities.
# Mode bits AM=1 CW=2 USB=4 FM=32. CWF is represented as CW (FM carrier with tone).
# Only expose the negotiated live tune ranges, not the entire RFIC range.
DUMP_STATE = """1
2
0
136000000 174000000 0x27 -1 -1 0x3 0
400000000 480000000 0x27 -1 -1 0x3 0
0 0 0 0 0 0 0
0 0 0 0 0 0 0
0x27 10
0 0
0x20 15000
0x20 8000
0x1 8000
0x1 4000
0x4 2400
0x2 500
0 0
0
0
0
0
0
0
0x0
0x0
0x40000000
0x0
0x0
0x0
vfo_ops=0x0
ptt_type=0x0
dcd_type=0x1
targetable_vfo=0x0
has_set_vfo=0
has_get_vfo=1
has_set_freq=1
has_get_freq=1
has_set_conf=0
has_get_conf=0
has_power2mW=0
has_mW2power=0
has_get_ant=0
has_set_ant=0
timeout=0
rig_model=2
rigctld_version=Flux live bridge v0
done
"""

ALIASES = {
    r"\get_freq": "f", r"\set_freq": "F", r"\get_mode": "m", r"\set_mode": "M",
    r"\get_vfo": "v", r"\set_vfo": "V", r"\get_ptt": "t", r"\set_ptt": "T",
    r"\get_level": "l", r"\get_info": "_", r"\get_dcd": "dcd",
}

class Rig:
    def __init__(self, link, features=("status", "tune")):
        self.link = link
        self.features = set(features)
        self.lock = threading.Lock()

    @staticmethod
    def mode(vfo):
        mode = vfo["mode"]
        return ("CW" if mode == "CWF" else mode,
                {"FM": 15000 if vfo["wide"] else 8000,
                 "AM": 8000 if vfo["wide"] else 4000,
                 "USB": 2400, "CW": 500, "CWF": 500}[mode])

    def execute(self, line):
        parts = line.split()
        if not parts:
            return "RPRT -1\n"
        op = ALIASES.get(parts[0], parts[0])
        args = parts[1:]
        try:
            with self.lock:
                return self._execute(op, args)
        except (InstallError, OSError):
            return "RPRT -5\n"
        except (ValueError, KeyError, IndexError, OverflowError):
            return "RPRT -1\n"

    def _execute(self, op, args):
        if op == r"\dump_state" and not args:
            return (DUMP_STATE
                    .replace("has_set_vfo=0", "has_set_vfo=" + str(int("vfo" in self.features)))
                    .replace("has_set_freq=1", "has_set_freq=" + str(int("tune" in self.features))))
        if op == r"\chk_vfo" and not args:
            return "0\n"
        # NET backend checks CAT lock before changing mode. This bridge does
        # not implement a CAT lock; this is not a read of the keypad lock.
        if op == r"\get_lock_mode" and not args:
            return "0\nRPRT 0\n"
        if op == r"\get_powerstat" and not args:
            self.link.status()
            return "1\n"
        if op == "_":
            return "Flux live serial bridge (RX/CAT; remote TX disabled)\n"
        if op == "l" and args == ["?"]:
            return "STRENGTH\n"
        if (op == "l" and args == ["STRENGTH"]) or (op == "dcd" and not args):
            s = self.link.status()
            # RSSI/DCD belong to the currently watched receiver, not the
            # inactive VFO. Do not present a cached sample during TX/sleep,
            # scans, or when dual watch is sampling the other VFO.
            if (not s["standby_idle"] or s["transmitting"] or s["sleeping"]
                    or s["watching"] != s["master"]):
                return "RPRT -9\n"
            if op == "l":
                # Match the firmware's S-meter: VHF/UHF S9 = -93 dBm.
                # The underlying raw-to-dBm conversion is not yet calibrated.
                return f"{s['rssi_dbm'] + 93}\n"
            return f"{int(s['squelch_open'])}\n"
        if op == "M" and len(args) == 2 and "mode" in self.features:
            mode, width = args[0], int(args[1])
            widths = {"FM": (8000,15000), "AM": (4000,8000), "USB": (2400,), "CW": (500,)}
            if mode not in widths or (width not in widths[mode] and width not in (0,-1)):
                return "RPRT -1\n"
            wide = len(widths[mode]) == 2 and width in (0, -1, widths[mode][-1])
            s = self.link.set_mode(mode, wide)
            actual = self.mode(s["vfos"][s["master"]])
            if actual[0] != mode or (width > 0 and actual[1] != width):
                return "RPRT -5\n"
            return "RPRT 0\n"
        if op == "V" and len(args) == 1 and "vfo" in self.features:
            if args[0] not in ("VFOA", "VFOB"):
                return "RPRT -1\n"
            index = int(args[0] == "VFOB")
            return "RPRT 0\n" if self.link.select_vfo(index)["master"] == index else "RPRT -5\n"
        if op == "F" and len(args) == 1 and "tune" in self.features:
            value = float(args[0])
            if not math.isfinite(value) or value % 10 or not (
                136e6 <= value <= 174e6 or 400e6 <= value <= 480e6):
                return "RPRT -1\n"
            s = self.link.tune(int(value))
            return "RPRT 0\n" if s["vfos"][s["master"]]["hz"] == int(value) else "RPRT -5\n"
        if op in ("f", "m", "v", "t", r"\get_vfo_info") and (
            not args or (op == r"\get_vfo_info" and len(args) == 1)):
            s = self.link.status()
            idx = s["master"]
            if args and args[0] in ("VFOA", "VFOB"):
                idx = int(args[0] == "VFOB")
            elif args and args[0] not in ("currVFO", "VFO_CURR"):
                return "RPRT -1\n"
            v = s["vfos"][idx]
            mode, width = self.mode(v)
            return {
                "f": f"{v['hz']}\n", "m": f"{mode}\n{width}\n",
                "v": "VFOA\n" if idx == 0 else "VFOB\n",
                "t": f"{int(s['transmitting'])}\n",
                r"\get_vfo_info": f"{v['hz']}\n{mode}\n{width}\n0\n0\n",
            }[op]
        # Never simulate a successful change that the firmware cannot perform.
        # In particular never key or unkey a local operator's transmitter here.
        return "RPRT -11\n"

class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(60)
        try:
            while True:
                raw = self.rfile.readline(1025)
                if not raw or len(raw) > 1024 or not raw.endswith(b"\n"):
                    return
                line = raw.decode("ascii").strip()
                if line in ("q", "Q", r"\quit"):
                    return
                self.wfile.write(self.server.rig.execute(line).encode("ascii"))
                self.wfile.flush()
        except (OSError, UnicodeError):
            return

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def __init__(self, address, rig):
        self.rig = rig
        super().__init__(address, Handler)

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True, help="K6 serial device")
    p.add_argument("--listen-port", type=int, default=4532)
    a = p.parse_args()
    try:
        with serial_lock(a.port), open_port(SimpleNamespace(port=a.port)) as port:
            link = LiveLink(port)
            link.status()  # fail up front rather than serving a nonexistent radio
            # Old live firmware explicitly rejects CAPS. Never fall back on silence,
            # malformed responses or arbitrary I/O failures.
            try:
                features = link.capabilities()["features"]
            except InstallError as exc:
                if str(exc) != "Unsupported command, address or frequency.":
                    raise
                features = ("status", "tune")
            with Server(("127.0.0.1", a.listen_port), Rig(link, features)) as server:
                print(f"Flux NET rigctl on 127.0.0.1:{server.server_address[1]} (TX disabled)", flush=True)
                server.serve_forever()
    except KeyboardInterrupt:
        pass
    except (InstallError, OSError) as exc:
        p.exit(1, f"{exc}\n")

if __name__ == "__main__":
    main()
