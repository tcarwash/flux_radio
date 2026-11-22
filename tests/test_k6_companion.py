"""Tests for the cable-only Flux K6 Companion web bridge."""
import http.client
import json
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import k6_companion as companion


class FakeBridge:
    device = "/dev/fake-k6"
    def __init__(self):
        self.presses = []
    def status(self):
        return {"transmitting": False, "vfos": [], "battery_volts": 7.5}
    def screenshot(self):
        return b"\x89PNG\r\n\x1a\n" + b"test"
    def key(self, key, is_long):
        if key not in companion.KEYS or not isinstance(is_long, bool):
            raise ValueError("Invalid K6 key request.")
        if is_long and key in companion.UNSUPPORTED_LONG:
            raise ValueError("Long digits are not supported.")
        self.presses.append((key, is_long))
        return {"key": key, "gesture": "long" if is_long else "short", "queued": True}


class BridgeTests(unittest.TestCase):
    def test_cli_command_invocation(self):
        bridge = companion.Bridge("/dev/ttyUSB9")
        result = mock.Mock(returncode=0, stdout='{"queued":true}', stderr='')
        with mock.patch.object(companion.subprocess, "run", return_value=result) as run:
            self.assertTrue(bridge.key("MENU", False)["queued"])
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[2:5], ["--port", "/dev/ttyUSB9", "key"])
        self.assertEqual(cmd[5:], ["MENU"])
        with mock.patch.object(companion.subprocess, "run", return_value=result) as run:
            bridge.key("SIDE1", True)
        self.assertEqual(run.call_args.args[0][-3:], ["key", "SIDE1", "--long"])

    def test_invalid_key_never_reaches_cli(self):
        bridge = companion.Bridge("/dev/ttyUSB9")
        with mock.patch.object(companion.subprocess, "run") as run:
            with self.assertRaises(ValueError): bridge.key("PTT", False)
            with self.assertRaises(ValueError): bridge.key("1", True)
            with self.assertRaises(ValueError): bridge.key("MENU", "false")
        run.assert_not_called()

    def test_all_keys_have_radio_buttons(self):
        html = (companion.STATIC / "index.html").read_text()
        keys = re.findall(r'data-key="([^"]+)"', html)
        self.assertEqual(len(keys), len(companion.KEYS))
        self.assertEqual(set(keys), companion.KEYS)


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bridge = FakeBridge()
        cls.server = companion.Server(("127.0.0.1", 0), cls.bridge, "test-secret-token")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def http(self, method, path, payload=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        body = json.dumps(payload).encode() if payload is not None else None
        base = {"Content-Type": "application/json"} if body is not None else {}
        base.update(headers or {})
        conn.request(method, path, body=body, headers=base)
        res = conn.getresponse()
        out = (res.status, res.getheader("Content-Type"), res.read())
        conn.close()
        return out

    def test_static_routes_and_config(self):
        code, mime, data = self.http("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"FLUX LAB", data)
        code, mime, data = self.http("GET", "/api/config")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(data)["auth_required"])

    def test_auth_protects_status_and_screenshot(self):
        self.assertEqual(self.http("GET", "/api/screen")[0], 401)
        self.assertEqual(self.http("GET", "/api/status")[0], 401)
        headers = {"X-K6-Token": "test-secret-token"}
        self.assertEqual(self.http("GET", "/api/status", headers=headers)[0], 200)
        code, mime, data = self.http("GET", "/api/screen", headers=headers)
        self.assertEqual(code, 200)
        self.assertEqual(mime, "image/png")
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_key_and_long_gesture(self):
        headers = {"X-K6-Token": "test-secret-token"}
        code, _, data = self.http("POST", "/api/key",
                                  {"key": "AB", "long": True}, headers)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(data)["gesture"], "long")
        self.assertIn(("AB", True), self.bridge.presses)

    def test_ptt_and_unsupported_long_are_rejected(self):
        headers = {"X-K6-Token": "test-secret-token"}
        self.assertEqual(self.http("POST", "/api/key",
                                   {"key": "PTT", "long": False}, headers)[0], 400)
        self.assertEqual(self.http("POST", "/api/key",
                                   {"key": "1", "long": True}, headers)[0], 400)

    def test_cross_origin_key_is_refused(self):
        headers = {"X-K6-Token": "test-secret-token",
                   "Origin": "http://malicious.invalid"}
        code, _, _ = self.http("POST", "/api/key",
                               {"key": "MENU", "long": False}, headers)
        self.assertEqual(code, 403)

    def test_no_anonymous_key(self):
        code, _, _ = self.http("POST", "/api/key", {"key": "EXIT", "long": False})
        self.assertEqual(code, 401)


if __name__ == "__main__":
    unittest.main()
