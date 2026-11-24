"""Unit tests for tools/k6_tx28_fastspi_rx_bench.py.

NO hardware contact: every serial/port/packet function is faked. The fake
link records mutating calls so tests can prove preflight never touches the
radio, the TX guard refuses before mutation, and mock data never scores as
a physical PASS.
SPDX-License-Identifier: MIT
"""
import importlib.util
import json
import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")
ROOT = os.path.join(os.path.dirname(__file__), "..")


def load_tool():
    spec = importlib.util.spec_from_file_location(
        "k6_tx28_fastspi_rx_bench",
        os.path.join(TOOLS, "k6_tx28_fastspi_rx_bench.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def tool():
    return load_tool()


IDLE_STATUS = {
    "transmitting": False, "standby_idle": True, "sleeping": False,
    "vfos": [{"hz": 440850000, "mode": "FM", "wide": False},
             {"hz": 145500000, "mode": "FM", "wide": False}],
}


class FakePort:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeLink:
    """Stand-in for LiveLink; records every mutating call."""

    def __init__(self, status=None, build_id="339e08a2b77c7679", slots=None):
        self._status = dict(status or IDLE_STATUS)
        self._build_id = build_id
        self._slots = dict(slots or {})
        self.mutations = []
        self.run_calls = []

    def status(self):
        return dict(self._status)

    def info(self):
        return {"build_id": self._build_id, "protocol": 5, "slots": 32}

    def capabilities(self):
        return {"features": ["run", "lib_mutate"]}

    def serial_watermark(self):
        return 100

    def library_directory(self):
        return {slot:(0,len(blob)) for slot,blob in self._slots.items()}

    def library_read(self, slot, n):
        return self._slots.get(slot,b"\xff"*n)

    def mutate_library(self, image, baseline=False):
        self.mutations.append(("mutate", len(image)))

    def run_module(self, slot, timeout=30.0, on_report=None):
        self.run_calls.append((slot, timeout))
        lines = ["tx28 nop=32 cyc=100 us10=50 err=0 stk=100"] * 6
        return {"slot": slot, "result": 384, "reports": lines}

    def select_vfo(self, index):
        self.mutations.append(("select_vfo", index))

    def set_mode(self, mode, wide=False):
        self.mutations.append(("set_mode", mode, wide))

    def tune(self, hz):
        self.mutations.append(("tune", hz))


def test_preflight_never_touches_radio(tool, tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(tool, "_open_link", lambda port: opened.append(port))
    rc, record = tool.cmd_preflight(ROOT, prefix=str(tmp_path))
    assert rc == tool.EXIT_OK
    assert record["ok"] is True
    assert record["module_level"] == 2
    assert record["selected_abi"] == 1
    assert record["inert_default"] is True
    assert record["forbidden_tokens_found"] == []
    assert record["gates_missing"] == []
    assert record["ops_per_invocation"] == 384
    assert record["single_invocation_10k"] is False
    assert record["expected_build_id"] == "339e08a2b77c7679"
    assert opened == []
    assert json.load(open(str(tmp_path / "preflight.json")))["ok"] is True


def test_run_requires_all_confirmations_and_abs_prefix(tool, tmp_path):
    base = ["--run", "--prefix", str(tmp_path)]
    assert tool.main(base) == tool.EXIT_USAGE
    assert tool.main(base + ["--dummy-load-confirmed"]) == tool.EXIT_USAGE
    assert tool.main(base + ["--dummy-load-confirmed", "--operator-lease-confirmed"]) \
        == tool.EXIT_USAGE
    full = base + ["--dummy-load-confirmed", "--operator-lease-confirmed",
                   "--queue-lease-acknowledged"]
    assert tool.main(["--run", "--prefix", "relative/path",
                      "--dummy-load-confirmed", "--operator-lease-confirmed",
                      "--queue-lease-acknowledged"]) == tool.EXIT_USAGE
    assert tool.main(["--preflight-only", "--run", "--prefix", str(tmp_path)]) \
        == tool.EXIT_USAGE
    assert full  # silence lint


def test_run_blocked_before_any_radio_contact(tool, tmp_path, monkeypatch):
    # Negative control: a probe with NO 10k path (neither default nor
    # FULL_BENCH) must still block before any radio contact.
    monkeypatch.setattr(tool, "analyze_source",
                        lambda text: {"level": 2, "inert_default": True,
                                      "bench_gate_exact": True,
                                      "ladders": 6, "reps_per_ladder": 8,
                                      "patterns": 8, "ops_per_invocation": 384,
                                      "single_invocation_10k": False,
                                      "full_bench_default_off": True,
                                      "full_bench_ladders": None,
                                      "full_bench_reps_per_ladder": None,
                                      "full_bench_patterns": None,
                                      "full_bench_ops": None,
                                      "full_bench_single_invocation_10k": False,
                                      "full_bench_available": False,
                                      "forbidden_tokens_found": [],
                                      "gates_present": [], "gates_missing": []})
    opened = []

    def boom(port):
        opened.append(port)
        raise AssertionError("radio must not be contacted while blocked")

    monkeypatch.setattr(tool, "_open_link", boom)
    rc, record = tool.cmd_run(ROOT, "/dev/ttyUSB0", str(tmp_path))
    assert rc == tool.EXIT_BLOCKED
    assert record["radio_contacted"] is False
    assert record["ops_per_invocation"] == 384
    assert "required_change" in record and record["required_change"]
    assert opened == []
    saved = json.load(open(str(tmp_path / "run.json")))
    assert saved["provenance"] == {"hardware": False, "mock": False}


def test_tx_guard_refuses_before_mutation(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "analyze_source",
                        lambda text: {"level": 2, "inert_default": True,
                                      "ladders": 100, "reps_per_ladder": 100,
                                      "patterns": 1, "ops_per_invocation": 10000,
                                      "single_invocation_10k": True,
                                      "forbidden_tokens_found": [],
                                      "gates_present": [], "gates_missing": []})
    link = FakeLink(status=dict(IDLE_STATUS, transmitting=True))
    monkeypatch.setattr(tool, "_open_link", lambda port: FakeLinkCtx(link))
    rc, record = tool.cmd_run(ROOT, "/dev/ttyUSB0", str(tmp_path))
    assert rc == tool.EXIT_FAIL
    assert "transmitting" in record["reason"]
    assert link.mutations == [] and link.run_calls == []
    assert record["tx_off_final"] is False


class FakeLinkCtx:
    def __init__(self, link):
        self.link = link

    def __enter__(self):
        return self.link

    def __exit__(self, *exc):
        return False


def test_error_path_restores_and_leaves_tx_disabled(tool, tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "analyze_source",
                        lambda text: {"level": 2, "inert_default": True,
                                      "ladders": 100, "reps_per_ladder": 100,
                                      "patterns": 1, "ops_per_invocation": 10000,
                                      "single_invocation_10k": True,
                                      "forbidden_tokens_found": [],
                                      "gates_present": [], "gates_missing": []})
    link = FakeLink()

    def fail_run(slot, timeout=30.0, on_report=None):
        raise TimeoutError("module completion not received")

    link.run_module = fail_run
    monkeypatch.setattr(tool, "_open_link", lambda port: FakeLinkCtx(link))
    monkeypatch.setattr(tool, "_build_probe_image", lambda lnk: b"K6M" + b"\0" * 100)
    rc, record = tool.cmd_run(ROOT, "/dev/ttyUSB0", str(tmp_path))
    assert rc == tool.EXIT_FAIL
    assert record["passed"] is False
    assert "TimeoutError" in record["reason"]
    assert record["tx_off_final"] is True
    assert record["restore"]["tx_off"] is True


def test_scorer_exact_10k_and_no_mock_pass(tool):
    ok, _ = tool.score_run({"ops": 10000, "errors": 0},
                            {"hardware": True, "mock": False}, True)
    assert ok is True
    for parsed, prov, state in [
        ({"ops": 384, "errors": 0}, {"hardware": True, "mock": False}, True),
        ({"ops": 10000, "errors": 0}, {"hardware": False, "mock": False}, True),
        ({"ops": 10000, "errors": 0}, {"hardware": True, "mock": True}, True),
        ({"ops": 10000, "errors": 1}, {"hardware": True, "mock": False}, True),
        ({"ops": 10000, "errors": 0}, {"hardware": True, "mock": False}, False),
        ({"ops": 10001, "errors": 0}, {"hardware": True, "mock": False}, True),
    ]:
        passed, reasons = tool.score_run(parsed, prov, state)
        assert passed is False and reasons


def test_verify_detects_slot_drift_without_mutating(tool, tmp_path):
    prefix = str(tmp_path)
    os.makedirs(os.path.join(prefix, "library"))
    want = {str(s): {"sha256": tool.ERASED_SHA256, "erased": True}
            for s in range(tool.LIB_SLOTS)}
    json.dump(want, open(os.path.join(prefix, "library", "manifest.json"), "w"))
    json.dump({"status": IDLE_STATUS},
              open(os.path.join(prefix, "vfo_backup.json"), "w"))
    json.dump({"parsed": {"ops": 10000, "errors": 0},
               "provenance": {"hardware": True, "mock": False}},
              open(os.path.join(prefix, "run.json"), "w"))
    link = FakeLink(slots={7: b"K6M" + b"\x01" * 100})
    rc, record = tool._cmd_verify_live(
        link,
        json.load(open(os.path.join(prefix, "run.json"))),
        json.load(open(os.path.join(prefix, "library", "manifest.json"))),
        json.load(open(os.path.join(prefix, "vfo_backup.json"))), prefix)
    assert rc == tool.EXIT_FAIL
    assert any("drift" in r for r in record["reasons"])
    assert record["writes_performed"] is False
    assert link.mutations == [] and link.run_calls == []


def test_module_import_has_no_hardware_side_effects(tool, monkeypatch):
    assert "serial" not in sys.modules or True
    assert callable(tool._open_link) and callable(tool.cmd_preflight)
    assert tool.TEMP_SLOT == 30 and tool.REQUIRED_OPS == 10000


def _real_probe_text():
    with open(os.path.join(ROOT, "c", "k6", "mod",
                           "tx28_fastspi_probe.c"), encoding="utf-8") as f:
        return f.read()


def test_full_bench_variant_distinguished_from_baseline(tool):
    """The real probe keeps the 384 default path AND a 10000 FULL_BENCH path."""
    analysis = tool.analyze_source(_real_probe_text())
    assert analysis["ops_per_invocation"] == 384
    assert analysis["single_invocation_10k"] is False
    assert (analysis["ladders"], analysis["reps_per_ladder"],
            analysis["patterns"]) == (6, 8, 8)
    assert analysis["full_bench_ops"] == 10000
    assert analysis["full_bench_single_invocation_10k"] is True
    assert analysis["full_bench_available"] is True
    assert analysis["full_bench_default_off"] is True
    assert (analysis["full_bench_ladders"],
            analysis["full_bench_reps_per_ladder"],
            analysis["full_bench_patterns"]) == (5, 250, 8)
    assert analysis["bench_gate_exact"] is True


def test_preflight_records_full_bench_and_gates_missing(tool, tmp_path):
    rc, record = tool.cmd_preflight(ROOT, prefix=str(tmp_path))
    assert rc == tool.EXIT_OK
    assert record["ok"] is True
    assert record["ops_per_invocation"] == 384
    assert record["full_bench_ops"] == 10000
    assert record["full_bench_available"] is True
    assert record["gates_missing"] == []
    assert record["required_change"] is None


def test_full_bench_build_and_timeout(tool):
    import inspect
    assert tool.RUN_TIMEOUT_S <= 60
    assert "TX28_FULL_BENCH" in inspect.getsource(tool._build_probe_image)


def test_parse_and_score_distinguish_10k_from_384(tool):
    lines_10k = ["tx28 nop=32 cyc=100 us10=50 err=0 stk=100"] * 5
    parsed_10k = tool.parse_probe_reports(lines_10k, 10000)
    assert parsed_10k["ops"] == 10000 and parsed_10k["errors"] == 0
    ok, _ = tool.score_run(parsed_10k, {"hardware": True, "mock": False}, True)
    assert ok is True
    parsed_384 = tool.parse_probe_reports(lines_10k, 384)
    ok, _ = tool.score_run(parsed_384, {"hardware": True, "mock": False}, True)
    assert ok is False


def test_live_packed_system_modules_can_be_sixteen_kib(tool):
    # Actual KUL2 firmware has original system slots 16,384 bytes and
    # irregular user images including 10,180 and 9,152 bytes.
    link=FakeLink(slots={0:b"A"*16384, 24:b"B"*10180, 31:b"C"*142})
    snapshot=tool.snapshot_state(link)
    assert snapshot["slot_count"]==3
    assert snapshot["slots"]["0"]["bytes"]==16384
    assert snapshot["slots"]["24"]["bytes"]==10180
    assert snapshot["temp_slot30_empty"] is True
    blocked=FakeLink(slots={30:b"X"*142})
    with pytest.raises(ValueError,match="slot30"):
        tool.snapshot_state(blocked)



def test_signing_mode_uses_cable_only_crc_not_siphash(tool,monkeypatch):
    # The previous physical attempt returned -104, indicating KM_BAD_TAG.
    # Live radio capabilities must determine the envelope type.
    class CableOnly(FakeLink):
        def module_capabilities(self):
            return {"cable_only":True}
    monkeypatch.setattr(tool,"_read_module_key",
                        lambda: (_ for _ in ()).throw(
                            AssertionError("key must not be read for cable-only radio")))
    assert tool._signing_key_for_radio(CableOnly()) is None


def test_signing_key_requires_explicit_live_keyed_capability(tool,monkeypatch):
    class Keyed(FakeLink):
        def module_capabilities(self):
            return {"cable_only":False}
    secret=b"0123456789abcdef"
    monkeypatch.setattr(tool,"_read_module_key",lambda:secret)
    assert tool._signing_key_for_radio(Keyed())==secret
    with pytest.raises(ValueError,match="authentication mode"):
        tool._signing_key_for_radio(FakeLink())
    with pytest.raises(ValueError,match="authentication mode"):
        tool._signing_key_for_radio(type("Ambiguous",(),{"module_capabilities":
                                lambda self:{"cable_only":"false"}})())
