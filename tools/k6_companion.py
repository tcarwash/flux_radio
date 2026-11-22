#!/usr/bin/env python3
"""Browser companion for the live-serial Flux K6.

Only delegates screenshot, status and logical key gestures to k6_live.py.
No flashing, tuning, bootloader entry or PTT is available through this server.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
CLI = HERE / "k6_live.py"
STATIC = HERE / "k6_companion_static"
KEYS = {str(n) for n in range(10)} | {
    "STAR", "POUND", "MENU", "EXIT", "UP", "DOWN",
    "VM", "AB", "BAND", "SIDE1", "SIDE2",
}
UNSUPPORTED_LONG = {str(n) for n in range(1, 10)}


class DeviceError(RuntimeError):
    pass


class Bridge:
    def __init__(self, device: str, python: str = sys.executable):
        self.device = device
        self.python = python
        self.lock = threading.Lock()

    def _execute(self, *args: str, timeout: float = 12) -> str:
        cmd = [self.python, str(CLI), "--port", self.device, *args]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=timeout, cwd=HERE.parent, check=False)
        except subprocess.TimeoutExpired as exc:
            raise DeviceError("Radio command timed out; check the cable and port.") from exc
        except OSError as exc:
            raise DeviceError(f"Unable to start K6 CLI: {exc}") from exc
        if p.returncode:
            detail = (p.stderr or p.stdout or "Unknown radio error").strip()
            raise DeviceError(detail[-600:])
        return p.stdout

    def status(self) -> dict:
        with self.lock:
            return json.loads(self._execute("status"))

    def screenshot(self) -> bytes:
        with self.lock:
            with tempfile.TemporaryDirectory(prefix="flux-k6-screen-") as folder:
                output = Path(folder) / "lcd.png"
                self._execute("screenshot", "--out", str(output), "--scale", "1")
                return output.read_bytes()

    def key(self, key: str, is_long: bool) -> dict:
        if key not in KEYS or type(is_long) is not bool:
            raise ValueError("Invalid K6 key request.")
        if is_long and key in UNSUPPORTED_LONG:
            raise ValueError("Long presses for numeric keys 1–9 are not supported by the radio.")
        with self.lock:
            args = ["key", key]
            if is_long:
                args.append("--long")
            return json.loads(self._execute(*args))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, bridge: Bridge, token: str | None):
        super().__init__(address, Handler)
        self.bridge = bridge
        self.token = token


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # No credentials or request bodies in access logs.
        sys.stderr.write("[k6 companion] " + fmt % args + "\n")

    def respond(self, status: int, body: bytes, mime: str):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "img-src 'self' blob: data:; connect-src 'self'; "
                         "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status: int, obj: dict):
        self.respond(status, json.dumps(obj, separators=(",", ":")).encode(),
                     "application/json; charset=utf-8")

    def authorized(self) -> bool:
        token = self.server.token
        if token is None:
            return True
        supplied = self.headers.get("X-K6-Token", "")
        if not secrets.compare_digest(supplied, token):
            self.reply_json(401, {"error": "Access token required or incorrect."})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/config":
            return self.reply_json(200, {
                "auth_required": self.server.token is not None,
                "device": self.server.bridge.device,
                "keys": sorted(KEYS),
                "poll_ms": 2800,
            })
        if path in ("/", "/index.html", "/style.css", "/app.js"):
            filename = {
                "/": "index.html", "/index.html": "index.html",
                "/style.css": "style.css", "/app.js": "app.js",
            }[path]
            mime = {
                "index.html": "text/html; charset=utf-8",
                "style.css": "text/css; charset=utf-8",
                "app.js": "application/javascript; charset=utf-8",
            }[filename]
            return self.respond(200, (STATIC / filename).read_bytes(), mime)
        if path not in ("/api/status", "/api/screen"):
            return self.reply_json(404, {"error": "Not found"})
        if not self.authorized():
            return
        try:
            if path == "/api/status":
                return self.reply_json(200, self.server.bridge.status())
            return self.respond(200, self.server.bridge.screenshot(), "image/png")
        except (DeviceError, OSError, ValueError) as exc:
            return self.reply_json(502, {"error": str(exc)})

    def do_POST(self):
        # Close POST connections so rejected requests cannot be parsed as a
        # second HTTP request if the body was deliberately left unread.
        self.close_connection = True
        if urlsplit(self.path).path != "/api/key":
            return self.reply_json(404, {"error": "Not found"})
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urlsplit(origin).netloc != host:
            return self.reply_json(403, {"error": "Cross-origin key presses are forbidden."})
        if not self.authorized():
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip() != "application/json":
            return self.reply_json(415, {"error": "Expected application/json"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 512:
                raise ValueError("Invalid request length.")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or set(data) != {"key", "long"}:
                raise ValueError("Expected key and long fields.")
            result = self.server.bridge.key(data["key"], data["long"])
            return self.reply_json(200, result)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.reply_json(400, {"error": str(exc)})
        except (DeviceError, OSError) as exc:
            return self.reply_json(502, {"error": str(exc)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", "--serial", default="/dev/ttyUSB0",
                        help="programming cable serial port (default /dev/ttyUSB0)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="web bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8776,
                        help="web port (default 8776)")
    parser.add_argument("--token", default=os.environ.get("K6_COMPANION_TOKEN"),
                        help="required access token when listening beyond loopback")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "::1", "localhost") and not args.token:
        parser.error("--token is required when binding to a LAN/non-loopback interface")
    if args.token is not None and len(args.token) < 12:
        parser.error("--token must have at least 12 characters")
    server = Server((args.host, args.port), Bridge(args.device), args.token)
    print(f"Flux K6 Companion: http://{args.host}:{server.server_port}", flush=True)
    print(f"Device: {args.device}; virtual keys and LCD only (no PTT).", flush=True)
    if args.token:
        print("Access token enabled; enter it in the browser on first visit.", flush=True)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        print("\nStopping companion.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
