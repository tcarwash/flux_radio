#!/usr/bin/env python3
"""Flux K6 KISS TNC client + single-client localhost TCP bridge.

The radio must already be running the installed FLUX TNC foreground module.
No live firmware install, implicit module launch, automatic TX or flash writes.

Wire protocol: normal KISS port 0 data and SET_HARDWARE 0x06 extension:
b'FX', version 1, opcode. See docs/k6-kiss-multimode.md.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import selectors
import socket
import sys
import time

try:
    import serial
except ImportError:
    serial = None

FEND = 0xC0
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD
HARDWARE = 0x06
PREFIX = b"FX\x01"
LIST = 0x01
GET = 0x02
SET = 0x03
CAPS = 0x04
STATUS = 0x05
OK = 0
MODE_PACKET_TX = 0x01
MODE_PACKET_RX = 0x02
MODE_VOICE = 0x04
MAX_FRAME = 1024
MAX_BRIDGE_QUEUE = 16384
MODE_LABELS = {
    1: "APRS-1200",
    0x100: "SOTTO-1200",
}


class ProtocolError(ValueError):
    pass


class KISSDecoder:
    """Incremental KISS framing with bounded memory and loss recovery."""

    def __init__(self, max_frame=MAX_FRAME):
        if not 2 <= max_frame <= 65535:
            raise ValueError("invalid KISS frame bound")
        self.max_frame = max_frame
        self.frame = bytearray()
        self.started = False
        self.escaping = False
        self.bad = False

    def feed(self, data):
        frames = []
        for byte in data:
            if byte == FEND:
                if self.started and self.frame and not self.bad and not self.escaping:
                    frames.append(bytes(self.frame))
                self.started = True
                self.frame.clear()
                self.escaping = False
                self.bad = False
                continue
            if not self.started or self.bad:
                continue
            if self.escaping:
                self.escaping = False
                if byte == TFEND:
                    byte = FEND
                elif byte == TFESC:
                    byte = FESC
                else:
                    self.bad = True
                    continue
            elif byte == FESC:
                self.escaping = True
                continue
            if len(self.frame) >= self.max_frame:
                self.bad = True
                self.frame.clear()
                continue
            self.frame.append(byte)
        return frames


def kiss_frame(command, payload=b""):
    if not 0 <= command <= 255 or len(payload) > MAX_FRAME - 1:
        raise ProtocolError("invalid KISS frame size/type")
    escaped = bytearray([FEND])
    for b in bytes([command]) + payload:
        if b == FEND:
            escaped.extend((FESC, TFEND))
        elif b == FESC:
            escaped.extend((FESC, TFESC))
        else:
            escaped.append(b)
    escaped.append(FEND)
    return bytes(escaped)


def control(op, payload=b""):
    if not 1 <= op <= 5:
        raise ProtocolError("unsupported Flux control operation")
    return kiss_frame(HARDWARE, PREFIX + bytes([op]) + payload)


def decode_control(frame):
    if not frame or frame[0] != HARDWARE or len(frame) < 6 or frame[1:4] != PREFIX:
        return None
    op = frame[4]
    if not op & 0x80:
        return None
    return {"op": op & 0x7F, "status": frame[5], "payload": frame[6:]}


def parse_mode_reply(reply):
    payload = reply["payload"]
    if len(payload) < 6:
        raise ProtocolError("truncated mode descriptor")
    mode_id = int.from_bytes(payload[0:2], "little")
    flags = payload[2]
    mtu = int.from_bytes(payload[3:5], "little")
    n = payload[5]
    if n > 12 or len(payload) != 6 + n:
        raise ProtocolError("invalid mode label")
    try:
        label = payload[6:].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ProtocolError("non-ASCII mode label") from exc
    if mode_id == 0xffff:
        if flags or mtu or n:
            raise ProtocolError("malformed mode-list sentinel")
        return None
    if not mode_id or not label or any(ord(c) < 32 or ord(c) > 126 for c in label):
        raise ProtocolError("invalid modem identity")
    return {"id": mode_id, "name": label, "flags": flags, "max_payload": mtu,
            "packet_tx": bool(flags & MODE_PACKET_TX),
            "packet_rx": bool(flags & MODE_PACKET_RX),
            "voice": bool(flags & MODE_VOICE)}


@contextmanager
def radio(port, baud):
    if serial is None:
        raise RuntimeError("pyserial is required: pip install pyserial")
    from k6_port_lock import serial_lock
    with serial_lock(port):
        with serial.Serial(port, baudrate=baud, timeout=0.1, write_timeout=2,
                           exclusive=True) as s:
            yield s


def request(s, op, payload=b"", *, timeout=3, multi=False):
    s.write(control(op, payload))
    decoder = KISSDecoder()
    until = time.monotonic() + timeout
    found = []
    while time.monotonic() < until:
        for frame in decoder.feed(s.read(256)):
            reply = decode_control(frame)
            if reply is None or reply["op"] != op:
                continue
            if reply["status"] != OK:
                raise ProtocolError(f"radio refused Flux command {op}: error {reply['status']}")
            found.append(reply)
            if not multi:
                return found
            if parse_mode_reply(reply) is None:
                return found
    raise TimeoutError(f"no complete Flux response to command {op}")


def list_modes(s):
    replies = request(s, LIST, multi=True)
    modes = [parse_mode_reply(r) for r in replies]
    return [mode for mode in modes if mode is not None]


def get_mode(s):
    replies = request(s, GET)
    value = replies[0]["payload"]
    if len(value) != 2:
        raise ProtocolError("invalid GET mode response")
    return int.from_bytes(value, "little")


def set_mode(s, mode):
    if not 1 <= mode <= 65534:
        raise ProtocolError("invalid mode ID")
    replies = request(s, SET, mode.to_bytes(2, "little"))
    returned = replies[0]["payload"]
    if len(returned) != 2 or int.from_bytes(returned, "little") != mode:
        raise ProtocolError("SET mode acknowledgement mismatch")
    return mode


def serve(s, *, host="127.0.0.1", tcp_port=8001, stop_after=None):
    """Bounded one-client KISS TCP to cable bridge. No queue, no redial, no broadcast."""
    if not 0 < tcp_port < 65536:
        raise ValueError("invalid TCP port")
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, tcp_port))
    server.listen(1)
    server.settimeout(0.5)
    print(f"Flux KISS listening on {host}:{tcp_port} (one client; Ctrl+C to stop)",
          file=sys.stderr, flush=True)
    clients = 0
    try:
        while stop_after is None or clients < stop_after:
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            clients += 1
            print(f"KISS client {addr[0]} connected", file=sys.stderr, flush=True)
            with conn:
                conn.setblocking(False)
                sel = selectors.DefaultSelector()
                try:
                    sel.register(conn, selectors.EVENT_READ)
                    sel.register(s, selectors.EVENT_READ)
                    to_host = bytearray()
                    to_radio = bytearray()
                    radio_decoder = KISSDecoder()
                    client_decoder = KISSDecoder()
                    while True:
                        for key, event in sel.select(timeout=0.2):
                            if key.fileobj is conn:
                                try:
                                    data = conn.recv(4096)
                                except BlockingIOError:
                                    continue
                                if not data:
                                    break
                                for frame in client_decoder.feed(data):
                                    packet = kiss_frame(frame[0], frame[1:])
                                    if len(to_radio) + len(packet) > MAX_BRIDGE_QUEUE:
                                        raise ProtocolError("client sent data faster than radio")
                                    to_radio.extend(packet)
                            else:
                                data = s.read(1024)
                                for frame in radio_decoder.feed(data):
                                    packet = kiss_frame(frame[0], frame[1:])
                                    if len(to_host) + len(packet) > MAX_BRIDGE_QUEUE:
                                        raise ProtocolError("radio output exceeded TCP backpressure limit")
                                    to_host.extend(packet)
                        else:
                            if to_radio:
                                n = s.write(to_radio[:512])
                                if n:
                                    del to_radio[:n]
                            if to_host:
                                try:
                                    n = conn.send(to_host)
                                except BlockingIOError:
                                    n = 0
                                if n:
                                    del to_host[:n]
                            continue
                        break
                finally:
                    sel.close()
            print("KISS client disconnected", file=sys.stderr, flush=True)
    finally:
        server.close()


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0",
                        help="programming-cable device with FLUX TNC module already running")
    parser.add_argument("--baud", type=int, default=115200)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("modes", help="list modes truly advertised by the radio")
    p.add_argument("--json", action="store_true")
    sub.add_parser("get", help="show current selected mode")
    p = sub.add_parser("set", help="select a mode ID or exact name (does NOT transmit)")
    p.add_argument("mode")
    p = sub.add_parser("serve", help="bridge serial KISS to one KISS TCP client")
    p.add_argument("--listen", default="127.0.0.1")
    p.add_argument("--tcp-port", type=int, default=8001)
    p.add_argument("--allow-lan", action="store_true",
                   help="required to bind beyond loopback; no built-in authentication")
    args = parser.parse_args(argv)
    if args.baud != 115200:
        parser.error("current radio KISS UART uses 115200 baud")
    if args.command == "serve" and args.listen not in ("127.0.0.1", "::1", "localhost") and not args.allow_lan:
        parser.error("non-loopback KISS TCP listener needs --allow-lan (no authentication)")
    try:
        with radio(args.port, args.baud) as s:
            if args.command == "modes":
                modes = list_modes(s)
                if args.json:
                    print(json.dumps(modes, indent=2))
                else:
                    for mode in modes:
                        caps = ",".join(name for key, name in
                                        (("packet_tx","TX"),("packet_rx","RX"),("voice","voice"))
                                        if mode[key]) or "control-only"
                        print(f"{mode['id']:5d}  {mode['name']:<12} {caps:<15} MTU {mode['max_payload']}")
                return 0
            if args.command == "get":
                print(get_mode(s))
                return 0
            if args.command == "set":
                try:
                    wanted = int(args.mode, 0)
                except ValueError:
                    matches = [m for m in list_modes(s) if m["name"].lower() == args.mode.lower()]
                    if len(matches) != 1:
                        raise ProtocolError(f"mode not advertised by radio: {args.mode}")
                    wanted = matches[0]["id"]
                set_mode(s, wanted)
                print(f"Selected mode {wanted}")
                return 0
            if args.command == "serve":
                serve(s, host=args.listen, tcp_port=args.tcp_port)
                return 0
    except (RuntimeError, ProtocolError, OSError, TimeoutError) as exc:
        print(f"Flux KISS: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
