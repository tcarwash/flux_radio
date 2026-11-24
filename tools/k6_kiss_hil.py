#!/usr/bin/env python3
"""Exclusively opt-in, one-shot, NO-RF hardware qualification harness for the
Flux Data-pack KISS TNC (``c/k6/mod/fluxtnc.c``).

SAFETY CONTRACT (read before running anything):

* Default mode (no ``--execute``) is validate/freeze ONLY. It touches no
  device, serial port, bench lock, or network. It computes the source SHA256,
  the compiled Thumb PIC relocatable image (``modem.k6mod.build``: clang
  ``-fropi``, linked twice, byte-identical), the word-aligned entry, the API
  level, and the session manifest digest, then prints them. Use this to review
  exactly what an ``--execute`` run would attest.
* ``--execute`` performs exactly ONE one-shot session. It NEVER transmits,
  never flashes MCU firmware, never sends KISS type-0 DATA, never keys PTT,
  and never touches TX tuning. Its only mutation is one temporary SPI library
  slot (slot 30), installed via the legacy serial Cable session as a signed
  cable-only module, read back byte-exact, exercised over read-only KISS
  CONTROL frames (LIST / GET / SET APRS-1200 / STATUS / STOP), then uninstalled
  in a ``finally`` block. Any deviation FAILS CLOSED and leaves the bench on
  HOLD for operator inspection: no blind erase, no retry, no restart.
* Hardware access additionally requires ALL of: the cooperative whole-bench
  ``flock`` at ``~/.cache/flux/bench.lock``, the canonical ``serial_lock``
  on the cable port (same helper as the rest of the repo,
  ``tools/k6_port_lock.py``), and a fresh externally-coordinated GitHub
  issue #11 trusted-actor lease ACK whose session manifest digest matches this
  run EXACTLY. The ACK is provided as a file (``--lease-ack``); this tool
  never synthesises an ACK, never posts anything, and never touches the
  network. Coordinate the ACK on issue #11 out of band and hand the file over.
* Known-good radio state is re-attested immediately before executing, never
  trusted from the task text: build ``339e08a2b77c7679``, module-loader API 29,
  packed user directory with 31 occupied slots and slot 30 vacant, standby
  idle, no PTT, VFO A 433.500 MHz / VFO B 144.390 MHz, dummy load attached
  (the load is an operator precondition, verified by sight, not by software).
* Bounded timeouts everywhere; one EOS session timeout aborts between phases.
  A lost launch ACK or a failed STOP is NEVER retried: the run goes to HOLD
  and an operator inspects the radio (EXIT key / fresh STOP) before anything
  else touches the bench.

Operator usage::

  # 1. Freeze only (safe anywhere, no hardware):
  python3 tools/k6_kiss_hil.py
  python3 tools/k6_kiss_hil.py --freeze-out /tmp/kiss-hil-freeze.json

  # 2. Externally: coordinate the bench on GitHub issue #11, have a
  #    trusted actor attest the freeze's session_digest, and save their
  #    ACK JSON as a file, e.g. /tmp/lease-ack.json:
  #    {"issue": 11, "actor": "<trusted-name>", "session_digest": "<hex>",
  #     "issued_at": <unix>, "expires_at": <unix>}

  # 3. One-shot execute (radio on dummy load, standby, operator present):
  python3 tools/k6_kiss_hil.py --execute \\
      --lease-ack /tmp/lease-ack.json --trusted-actor <trusted-name> \\
      --private-dir ~/.cache/flux/kiss-hil/20261124T120000Z

  # 4. Inspect the private dir: snapshot.json, transcript.jsonl,
  #    evidence.json. On HOLD, read bench.hold reason first.

Failure handling:

* Any attestation mismatch (build, API, source/code SHA, entry, slot 30 not
  vacant, not idle, transmitting, wrong VFOs) aborts BEFORE any mutation, no
  HOLD (nothing changed), exit 2.
* Any failure AFTER the install was attempted -- including a lost launch ACK,
  a failed STOP, a readback SHA mismatch, or a failed erase verification --
  writes ``~/.cache/flux/bench.hold`` (reason + session digest), keeps the
  exact slot-30 image hash in the transcript, and exits 3. The operator then:
  inspects the radio, re-runs ONLY the read-only post checks
  (``--execute`` will refuse while the HOLD file exists -- remove it only
  after inspection), and never blindly retries the install or erases another
  slot. Uninstall removes EXACTLY slot 30 and only after its readback SHA
  matches the installed envelope byte-exact AND the KISS control path has
  returned to live serial.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import re
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---- Known-good expectations (re-attested on every --execute run) ----
EXPECTED_BUILD_HEX = "339e08a2b77c7679"
EXPECTED_API_MAX = 29
EXPECTED_MODULE_LEVEL = 14
EXPECTED_MODULE_NAME = "FLUX TNC"
EXPECTED_MODULE_SRC = os.path.join(ROOT, "c", "k6", "mod", "fluxtnc.c")
EXPECTED_MUX_HDR = os.path.join(ROOT, "c", "k6", "mod", "flux_kiss_mux.h")
TEMP_SLOT = 30
EXPECTED_OCCUPIED_SLOTS = 31
EXPECTED_VFO_A_HZ = 433_500_000
EXPECTED_VFO_B_HZ = 144_390_000
# The exact clean tracked HEAD is bound by the trusted GitHub ACK's
# SHA256 manifest; there is deliberately no self-referential fixed commit.
GIT_POLICY = "clean-tracked-tree + exact HEAD/source/code/harness digest"
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BENCH_LOCK = os.path.expanduser("~/.cache/flux/bench.lock")
BENCH_HOLD_FILE = os.path.expanduser("~/.cache/flux/bench.hold")
LEASE_ISSUE = 11
EXPERIMENT_ISSUE = 33
TRUSTED_ACTOR = "tcarwash"
OPERATOR_CALL = "AG7SU"


# KISS CONTROL ops this harness may ever send (mirrors tools/k6_kiss_tnc.py:
# LIST=1 GET=2 SET=3 STATUS=5 STOP=6; CAPS=4 is deliberately excluded,
# type-0 DATA is forbidden).
ALLOWED_KISS_OPS = frozenset({1, 2, 3, 5, 6})
KISS_DATA_CMD = 0x00
KISS_HARDWARE_CMD = 0x06

TIMEOUTS = {
    "handshake": 20.0,
    "cable_call": 3.0,
    "live_call": 2.0,
    "kiss_call": 3.0,
    "run_launch": 5.0,
    "lock": 10.0,
    "commit": 10.0,
    "session": 300.0,
}

GIT_COMMIT_FILE = "HIL_GIT_COMMIT"


class HilError(Exception):
    """Fail-closed harness abort (never a retry signal)."""


def _utc_stamp(when=None):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(when))


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_head():
    try:
        import subprocess
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
            text=True, timeout=10)
        head = out.stdout.strip()
        if len(head) == 40 and all(c in "0123456789abcdef" for c in head):
            return head
    except Exception:
        pass
    return "unknown"


def freeze():
    """Compute the offline freeze manifest (NO hardware, NO locks, NO net).

    Returns (manifest, code_bytes, entry_offset). Raises HilError fail-closed.
    """
    sys.path.insert(0, ROOT)
    sys.path.insert(0, HERE)
    from modem import k6mod  # noqa: E402 pure-python + local toolchain only

    for path in (EXPECTED_MODULE_SRC, EXPECTED_MUX_HDR):
        if not os.path.isfile(path):
            raise HilError("missing module source: %s" % path)
    source_sha = _sha256_file(EXPECTED_MODULE_SRC)
    mux_sha = _sha256_file(EXPECTED_MUX_HDR)
    level = k6mod.source_level(EXPECTED_MODULE_SRC)
    if level != EXPECTED_MODULE_LEVEL:
        raise HilError(
            "module source level is %r, expected %d (no build attempted)"
            % (level, EXPECTED_MODULE_LEVEL))
    try:
        code, entry = k6mod.build(EXPECTED_MODULE_SRC)
    except Exception as exc:
        raise HilError("module did not build as Thumb PIC relocatable: %s"
                       % exc) from exc
    if not code:
        raise HilError("empty module build")
    if len(code) + 48 > 0x4000:
        raise HilError("module does not fit a 16 KiB library slot")
    if entry >= len(code) or entry & 1:
        raise HilError("module entry 0x%x is not a word entry into %d bytes"
                       % (entry, len(code)))
    if entry != k6mod.bin_entry(code):
        raise HilError("module entry 0x%x is not recoverable from its binary"
                       % entry)
    name = k6mod.module_name(code)
    if name != EXPECTED_MODULE_NAME:
        raise HilError("module name block is %r, expected %r"
                       % (name, EXPECTED_MODULE_NAME))
    if code[:4] != b"K6NM":
        raise HilError("module lacks the K6NM name block (not relocatable TNC)")
    head = _git_head()
    manifest = {
        "format": 1,
        "harness": "tools/k6_kiss_hil.py",
        "git_head": head,
        "git_policy": GIT_POLICY,
        "expected_build": EXPECTED_BUILD_HEX,
        "harness_sha256": _sha256_file(__file__),
        "experiment_issue": EXPERIMENT_ISSUE,
        "trusted_actor": TRUSTED_ACTOR,
        "expected_api_max": EXPECTED_API_MAX,
        "module_source": os.path.relpath(EXPECTED_MODULE_SRC, ROOT),
        "module_source_sha256": source_sha,
        "mux_header_sha256": mux_sha,
        "module_level": level,
        "module_name": name,
        "code_sha256": hashlib.sha256(bytes(code)).hexdigest(),
        "code_len": len(code),
        "entry": entry,
        "temp_slot": TEMP_SLOT,
        "expected_occupied_slots": EXPECTED_OCCUPIED_SLOTS,
        "expected_vfo_a_hz": EXPECTED_VFO_A_HZ,
        "expected_vfo_b_hz": EXPECTED_VFO_B_HZ,
        "allowed_kiss_ops": sorted(ALLOWED_KISS_OPS),
        "forbidden": ["kiss-data-type0", "ptt", "tx", "tune", "mcu-flash"],
        "timeouts": dict(TIMEOUTS),
    }
    return manifest, bytes(code), entry


def session_digest(manifest):
    canonical = json.dumps(manifest, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def validate_lease_ack(path, expected_digest, trusted_actor, max_age_s, now):
    """Validate the external issue-#11 lease ACK file. Fail closed.

    Never synthesises or posts anything: the file must already exist
    with issue 11, trusted actor, exact session digest, canonical GitHub
    comment URL and a fresh issuance window. execute() independently
    checks the corresponding real GitHub comment before touching devices.
    """
    try:
        ack = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise HilError("lease ACK unreadable at %s: %s" % (path, exc)) from exc
    if not isinstance(ack, dict):
        raise HilError("lease ACK is not a JSON object")
    if ack.get("issue") != LEASE_ISSUE:
        raise HilError("lease ACK is not for issue #%d" % LEASE_ISSUE)
    actor = ack.get("actor")
    if not actor or not isinstance(actor, str):
        raise HilError("lease ACK names no trusted actor")
    if actor != trusted_actor or actor != TRUSTED_ACTOR:
        raise HilError("lease ACK actor %r is not the authorized %r"
                       % (actor, TRUSTED_ACTOR))
    url = ack.get("comment_url")
    if not isinstance(url, str) or not re.fullmatch(
            r"https://github\.com/tcarwash/flux_radio/issues/11#issuecomment-[0-9]+", url):
        raise HilError("lease ACK requires canonical trusted GitHub comment URL")
    if ack.get("session_digest") != expected_digest:
        raise HilError("lease ACK digest does not match this freeze exactly")
    try:
        issued = float(ack.get("issued_at"))
        expires = float(ack.get("expires_at"))
    except (TypeError, ValueError) as exc:
        raise HilError("lease ACK has no numeric issued_at/expires_at") from exc
    if not 0 <= now - issued <= max_age_s:
        raise HilError("lease ACK is stale (issued %.0fs ago, max %ds)"
                       % (now - issued, max_age_s))
    if now > expires:
        raise HilError("lease ACK is expired")
    return ack


def verify_real_github_lease(ack, manifest_digest):
    """Read-only GH verification of authenticated claim + complete lease log.

    Never accept a local JSON alone as bench authority. An active claim by
    another agent, a released claim, stale comment or unavailable GitHub
    must refuse before opening the cable.
    """
    comment_id = int(ack["comment_url"].rsplit("-", 1)[-1])
    def call(route):
        p = subprocess.run(["gh", "api", route], check=True,
                           capture_output=True, text=True, timeout=30)
        return json.loads(p.stdout)
    try:
        comment = call(f"repos/tcarwash/flux_radio/issues/comments/{comment_id}")
        if comment.get("user", {}).get("login") != TRUSTED_ACTOR:
            raise HilError("lease comment was not authored by trusted GitHub owner")
        expected = ("BENCH ACK | issue=%d | session=kiss-hil-%s | "
                    "approval_sha256=%s | operator=%s | scope=whole-bench"
                    % (EXPERIMENT_ISSUE, manifest_digest[:12], manifest_digest,
                       OPERATOR_CALL))
        body = comment.get("body", "")
        if not isinstance(body, str) or body.splitlines()[0].strip() != expected:
            raise HilError("GitHub lease comment does not authorize this exact session")
        from datetime import datetime, timezone
        created = comment.get("created_at", "")
        timestamp = datetime.fromisoformat(created.replace("Z", "+00:00"))
        diff = (datetime.now(timezone.utc) - timestamp).total_seconds()
        if not 0 <= diff <= 900:
            raise HilError("GitHub ACK comment is not fresh (15-minute limit)")
        # Check complete coordination history through all pages, even with
        # older gh CLI versions lacking --slurp.
        process = subprocess.run(
            ["gh", "api", "--paginate",
             "repos/tcarwash/flux_radio/issues/11/comments?per_page=100"],
            check=True, capture_output=True, text=True, timeout=45)
        dec = json.JSONDecoder()
        raw = process.stdout
        pos = 0
        items = []
        while pos < len(raw):
            while pos < len(raw) and raw[pos].isspace():
                pos += 1
            if pos >= len(raw):
                break
            page, pos = dec.raw_decode(raw, pos)
            if not isinstance(page, list):
                raise HilError("malformed paginated coordination history")
            items.extend(page)
        if not items:
            raise HilError("empty coordination history")
        active = None
        for item in sorted(items, key=lambda x: x.get("id", 0)):
            if item.get("user", {}).get("login") != TRUSTED_ACTOR:
                continue
            lines = item.get("body", "").strip().splitlines()
            line = lines[0].strip().upper() if lines else ""
            if ("RELEASE" in line or line.startswith("BENCH FREE |")):
                active = None
            elif line.startswith(("BENCH ACK |", "CLAIM ", "CLAIM |",
                                  "CLAIM /", "RUNNING ", "RUNNING |",
                                  "RUNNING /", "BLOCKED / HOLD")):
                active = item.get("id")
        if active != comment_id:
            raise HilError("GitHub bench has a different/unreleased claim or was released")
        return True
    except (subprocess.SubprocessError, OSError, ValueError, KeyError,
            IndexError, TypeError) as exc:
        raise HilError(f"cannot verify trusted GitHub bench lease: {exc}") from exc


def check_hold_file():
    if os.path.exists(BENCH_HOLD_FILE):
        try:
            reason = Path(BENCH_HOLD_FILE).read_text(encoding="utf-8").strip()
        except OSError:
            reason = "(unreadable)"
        raise HilError(
            "bench is on HOLD (%s): inspect the radio and private-dir "
            "transcript first; remove %s only after inspection"
            % (reason[:200], BENCH_HOLD_FILE))


def write_hold_file(reason, digest):
    try:
        Path(os.path.dirname(BENCH_HOLD_FILE)).mkdir(mode=0o700, parents=True,
                                                     exist_ok=True)
        with open(BENCH_HOLD_FILE, "w", encoding="utf-8") as f:
            json.dump({"reason": reason[:500], "session_digest": digest,
                       "at": _utc_stamp()}, f, indent=2)
    except OSError:
        pass


@contextmanager
def bench_flock(path, timeout_s):
    """Cooperative whole-bench flock. Fail closed while another agent holds it."""
    import fcntl
    Path(os.path.dirname(path)).mkdir(mode=0o700, parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    handle = open(path, "a+b")
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(),
                            fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise HilError(
                        "bench is busy (another agent holds %s); "
                        "do not disturb their lease" % path)
                time.sleep(0.2)
        yield handle
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _new_private_dir(explicit):
    path = Path(explicit).expanduser() if explicit else Path(
        os.path.expanduser("~/.cache/flux/kiss-hil")) / _utc_stamp()
    path.mkdir(mode=0o700, parents=True, exist_ok=False)
    return path


class Transcript:
    def __init__(self, path):
        self._f = open(path, "w", encoding="utf-8")
        self.events = []

    def note(self, kind, detail=None, **fields):
        rec = {"t": round(time.time(), 3), "kind": kind}
        if detail is not None:
            rec["detail"] = detail
        rec.update(fields)
        self._f.write(json.dumps(rec, sort_keys=True) + "\n")
        self._f.flush()
        self.events.append(rec)
        return rec

    def close(self):
        try:
            self._f.close()
        except OSError:
            pass


def describe_kiss_write(frame_bytes):
    """Accept only one fully framed, whitelisted Flux CONTROL request.

    Full-byte inspection prevents false safety claims when a pyserial write
    contains multiple frames or a partial KISS DATA command.
    """
    raw = bytes(frame_bytes)
    if len(raw) < 7 or raw[0] != 0xC0 or raw[-1] != 0xC0:
        return "unframed"
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    from k6_kiss_tnc import KISSDecoder, HARDWARE, PREFIX, LIST, GET, SET, STATUS, STOP
    frames = KISSDecoder().feed(raw)
    if len(frames) != 1:
        return "multi-or-malformed"
    frame = frames[0]
    if frame[0] == KISS_DATA_CMD:
        return "DATA-FORBIDDEN"
    if frame[0] != HARDWARE or frame[1:4] != PREFIX:
        return "non-flux-control"
    if len(frame) < 5:
        return "malformed-control"
    op, body = frame[4], frame[5:]
    if op not in (LIST, GET, SET, STATUS, STOP):
        return "unsupported-control"
    if op == SET and body != bytes((1, 0)):
        return "unsafe-set"
    if op != SET and body:
        return "unexpected-control-payload"
    return "control"


def execute(args):
    """One-shot hardware session. All imports touching devices stay local."""
    sys.path.insert(0, ROOT)
    sys.path.insert(0, HERE)
    from k6_install import (Cable, Serials, InstallError,  # noqa: E402
                            CABLE_HANDSHAKE)
    from k6_live import LiveLink, LiveUnavailable, RUN  # noqa: E402 actual API
    from k6_port_lock import serial_lock  # noqa: E402 canonical serial lock
    import serial  # noqa: E402 pyserial; opened only here, never in freeze

    _ = serial  # opened via open_port below
    t0 = time.monotonic()

    def remaining():
        left = TIMEOUTS["session"] if args.session_timeout is None \
            else args.session_timeout
        left = left - (time.monotonic() - t0)
        if left <= 0:
            raise HilError("session timeout exceeded; aborting between phases")
        return left

    manifest, code, entry = freeze()
    digest = session_digest(manifest)
    ack = validate_lease_ack(args.lease_ack, digest, args.trusted_actor,
                             args.lease_max_age_s, time.time())
    check_hold_file()
    if _git_head() == "unknown":
        raise HilError("source revision unavailable; no hardware permitted")
    clean = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT, check=True, capture_output=True, text=True, timeout=10)
    if clean.stdout.strip():
        raise HilError("tracked source changes since commit; freeze cannot be attested")
    verify_real_github_lease(ack, digest)
    priv = _new_private_dir(args.private_dir)
    tr = Transcript(priv / "transcript.jsonl")
    (priv / "freeze.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    tr.note("freeze", digest=digest, code_sha=manifest["code_sha256"],
            entry=entry, actor=ack["actor"])
    mutated = False
    envelope = b""
    envelope_sha = ""
    serial_used = None
    tx_proof = {"kiss_writes": 0, "data_frames_sent": 0,
                "ptt_api_called": False, "tune_api_called": False,
                "transmitting_seen": []}

    def open_serial():
        from k6_install import open_port
        return open_port(SimpleNamespace(port=args.port))

    def live_status(link):
        st = link.status()
        tx_proof["transmitting_seen"].append(bool(st["transmitting"]))
        return st

    def attest_live_idle(link, where):
        st = live_status(link)
        if st["transmitting"]:
            raise HilError("%s: radio is transmitting; abort" % where)
        if not st.get("standby_idle"):
            raise HilError("%s: radio is not standby-idle" % where)
        vfos = st.get("vfos", [])
        if len(vfos) != 2 or vfos[0].get("hz") != EXPECTED_VFO_A_HZ \
                or vfos[1].get("hz") != EXPECTED_VFO_B_HZ:
            raise HilError("%s: VFOs are %r, expected A=%d B=%d" % (
                where, [v.get("hz") for v in vfos], EXPECTED_VFO_A_HZ,
                EXPECTED_VFO_B_HZ))
        return st

    def attest_cable(cable, where):
        """INFO may omit legacy build identity; format-2 CAPS must prove it."""
        info = cable.info()
        if info.get("protocol") != 5:
            raise HilError("%s: expected packed KUL2 protocol 5, got %r"
                           % (where, info.get("protocol")))
        legacy = info.get("build_id")
        legacy_hex = bytes(legacy).hex() if legacy else None
        if legacy_hex is not None and legacy_hex != EXPECTED_BUILD_HEX:
            raise HilError("%s: INFO build %s differs from expected %s"
                           % (where, legacy_hex, EXPECTED_BUILD_HEX))
        caps = cable.capabilities()
        if caps.get("format") != 2:
            raise HilError("%s: firmware lacks exact format-2 CAPS identity"
                           % where)
        if caps.get("build_id") != EXPECTED_BUILD_HEX:
            raise HilError("%s: CAPS build %r mismatch" %
                           (where, caps.get("build_id")))
        if caps.get("module_api_max") != EXPECTED_API_MAX:
            raise HilError("%s: module API max is %r, expected %d" %
                           (where, caps.get("module_api_max"),
                            EXPECTED_API_MAX))
        return info, caps

    def attest_live_identity(link, where):
        """Never infer build from optional legacy INFO; require live K6CP."""
        info = link.info()
        if info.get("protocol") != 5:
            raise HilError("%s: unexpected live protocol %r" %
                           (where, info.get("protocol")))
        legacy = info.get("build_id")
        legacy_hex = bytes(legacy).hex() if legacy else None
        if legacy_hex is not None and legacy_hex != EXPECTED_BUILD_HEX:
            raise HilError("%s: live INFO build %s mismatch" %
                           (where, legacy_hex))
        caps = link.module_capabilities()
        if caps.get("format") != 2 or caps.get("build_id") != EXPECTED_BUILD_HEX:
            raise HilError("%s: live K6CP identity missing or mismatched" % where)
        if caps.get("module_api_max") != EXPECTED_API_MAX:
            raise HilError("%s: live API max %r differs from %d" %
                           (where, caps.get("module_api_max"),
                            EXPECTED_API_MAX))
        return info, caps

    try:
        remaining()
        with bench_flock(args.bench_lock, args.lock_timeout):
            # A lease may have been released between preflight and acquisition.
            verify_real_github_lease(ack, digest)
            tr.note("bench-lock", path=args.bench_lock)
            try:
                with serial_lock(args.port):
                    tr.note("serial-lock", port=args.port)
                    # ---- Phase 0: re-attest (cable) + snapshot BEFORE install ----
                    with open_serial() as port:
                        cable = Cable(port, log=lambda *a: tr.note(
                            "cable-log", detail=" ".join(
                                str(x) for x in a)[:160]),
                                      timeout=TIMEOUTS["cable_call"])
                        if not cable.handshake(
                                CABLE_HANDSHAKE,
                                wait=TIMEOUTS["handshake"]):
                            raise HilError("no cable session (radio not in "
                                           "EXPLORE/arena?); abort, no retry")
                        info, checked_caps = attest_cable(cable, "pre-install")
                        directory = cable.library_directory()
                        tr.note("directory",
                                slots=sorted(directory),
                                count=len(directory))
                        if len(directory) != EXPECTED_OCCUPIED_SLOTS:
                            raise HilError(
                                "packed directory holds %d slots, expected "
                                "%d with slot %d vacant"
                                % (len(directory), EXPECTED_OCCUPIED_SLOTS,
                                   TEMP_SLOT))
                        if TEMP_SLOT in directory:
                            raise HilError(
                                "temp slot %d is occupied; operator "
                                "inspection needed (never overwrite)"
                                % TEMP_SLOT)
                        if cable.slot_head(TEMP_SLOT) is not None:
                            raise HilError("temp slot %d head is not empty"
                                           % TEMP_SLOT)
                        blobs = {}
                        for slot in sorted(directory):
                            remaining()
                            data = cable.library_read(
                                slot, directory[slot][1])
                            blobs[str(slot)] = {
                                "len": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                            }
                        snap = {"build": checked_caps["build_id"],
                                "directory": {str(k): list(v)
                                              for k, v in directory.items()},
                                "slot_hashes": blobs}
                        (priv / "snapshot.json").write_text(
                            json.dumps(snap, indent=2, sort_keys=True),
                            encoding="utf-8")
                        tr.note("snapshot", slots=len(blobs))
                        watermark = cable.serial_watermark()
                        cable.end()
                    # ---- Phase 0b: live idle attestation ----
                    with open_serial() as port:
                        link = LiveLink(port, timeout=TIMEOUTS["live_call"])
                        attest_live_identity(link, "pre-install")
                        attest_live_idle(link, "pre-install")
                        tr.note("live-idle")
                    remaining()
                    # ---- Phase 1: Cable install into verified-empty slot 30 ----
                    from modem import k6mod
                    serials = Serials()
                    with open_serial() as port:
                        cable = Cable(port, log=lambda *a: tr.note(
                            "cable-log", detail=" ".join(
                                str(x) for x in a)[:160]),
                                      timeout=TIMEOUTS["cable_call"])
                        if not cable.handshake(
                                CABLE_HANDSHAKE,
                                wait=TIMEOUTS["handshake"]):
                            raise HilError("no cable session for install")
                        attest_cable(cable, "install")
                        if TEMP_SLOT in cable.library_directory():
                            raise HilError("temp slot filled since snapshot; "
                                           "abort (operator inspection)")
                        serials.advance_after(cable.serial_watermark())
                        serial_used = serials.next()
                        envelope = k6mod.image(
                            code, entry, serial_used, None,
                            level=EXPECTED_MODULE_LEVEL, lib=TEMP_SLOT)
                        envelope_sha = hashlib.sha256(envelope).hexdigest()
                        tr.note("envelope", slot=TEMP_SLOT, serial=serial_used,
                                sha=envelope_sha, bytes=len(envelope))
                        mutated = True
                        r, have, count, put = cable.send_image(
                            envelope, serial_used & 0xFF)
                        tr.note("installed", r=r, have=have, count=count,
                                put=put)
                        if r != 3 or put != len(envelope):
                            raise HilError(
                                "install not acknowledged (r=%r put=%r); "
                                "HOLD for inspection, no retry" % (r, put))
                        readback = cable.library_read(
                            TEMP_SLOT, len(envelope))
                        if readback != envelope:
                            raise HilError("install readback differs; HOLD, "
                                           "no retry")
                        tr.note("readback-exact", sha=envelope_sha)
                        cable.end()
                    remaining()
                    # ---- Phase 2: live RUN + KISS CONTROL only, then STOP ----
                    import importlib.util
                    spec = importlib.util.spec_from_file_location(
                        "k6_kiss_tnc_rt", os.path.join(HERE, "k6_kiss_tnc.py"))
                    kiss = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(kiss)
                    if {kiss.LIST, kiss.GET, kiss.SET, kiss.STATUS,
                            kiss.STOP} != ALLOWED_KISS_OPS:
                        raise HilError("repo KISS op numbers moved; harness "
                                       "allowlist is stale")
                    with open_serial() as port:
                        link = LiveLink(port, timeout=TIMEOUTS["live_call"])
                        attest_live_identity(link, "pre-run")
                        attest_live_idle(link, "pre-run")
                        try:
                            link.request(RUN, TEMP_SLOT | 0x100)
                        except LiveUnavailable as exc:
                            raise HilError(
                                "launch ACK lost; execution unknown -- HOLD, "
                                "do NOT retry: %s" % exc) from exc
                        tr.note("run-launched", slot=TEMP_SLOT,
                                allow_higher_api=True)
                        # Wrap writes for the no-TX proof.
                        real_write = port.write
                        writes = []

                        def audited_write(data):
                            writes.append(bytes(data))
                            tx_proof["kiss_writes"] += 1
                            kind = describe_kiss_write(data)
                            if kind != "control":
                                if kind == "DATA-FORBIDDEN":
                                    tx_proof["data_frames_sent"] += 1
                                raise HilError(f"unsafe KISS write {kind} blocked before wire")
                            return real_write(data)

                        port.write = audited_write
                        try:
                            modes = kiss.list_modes(port)
                            tr.note("kiss-list",
                                    modes=[m["name"] for m in modes])
                            names = [m["name"] for m in modes]
                            if "APRS-1200" not in names:
                                raise HilError("APRS-1200 not advertised")
                            cur = kiss.get_mode(port)
                            tr.note("kiss-get", mode=cur)
                            kiss.set_mode(port, 1)
                            tr.note("kiss-set", mode=1)
                            status_replies = kiss.request(
                                port, kiss.STATUS,
                                timeout=TIMEOUTS["kiss_call"])
                            payload = status_replies[0]["payload"]
                            if len(payload) != 4:
                                raise HilError("unexpected STATUS body")
                            tr.note("kiss-status",
                                    tx_ok=int.from_bytes(payload[0:2],
                                                         "little"),
                                    tx_fail=int.from_bytes(payload[2:4],
                                                           "little"))
                            try:
                                kiss.stop_module(port)
                            except Exception as exc:
                                raise HilError(
                                    "STOP not acknowledged; UART state "
                                    "unknown -- HOLD, inspect (EXIT key or "
                                    "fresh STOP), no auto retry: %s"
                                    % exc) from exc
                            tr.note("kiss-stop")
                        finally:
                            port.write = real_write
                        if tx_proof["data_frames_sent"]:
                            raise HilError("DATA frames were attempted; HOLD")
                        st = live_status(link)
                        if st["transmitting"] or not st.get("standby_idle"):
                            raise HilError("radio not idle after STOP; HOLD")
                        tr.note("live-restored")
                    remaining()
            except BlockingIOError as exc:
                raise HilError("serial port busy (another agent holds %s); "
                               "do not disturb their lease" % args.port
                               ) from exc
            # ---- cleanup of EXACTLY slot 30 while original bench lease is held
            # session; original whole-bench lock stays held through postflight) ----
            try:
                with nullcontext():
                    with serial_lock(args.port):
                        with open_serial() as port:
                            cable = Cable(
                                port, log=lambda *a: tr.note("cable-log", detail=
                                                             " ".join(str(x) for x
                                                                      in a)[:160]),
                                timeout=TIMEOUTS["cable_call"])
                            if not cable.handshake(
                                    CABLE_HANDSHAKE,
                                    wait=TIMEOUTS["handshake"]):
                                raise HilError("no cable session for uninstall; "
                                               "HOLD, operator inspection needed")
                            if not envelope:
                                raise HilError("temp slot identity unknown "
                                               "(install never recorded); "
                                               "operator inspection needed -- no "
                                               "blind erase")
                            now = cable.library_read(TEMP_SLOT, len(
                                envelope))
                            if hashlib.sha256(now).hexdigest() != envelope_sha \
                                    or now != envelope:
                                raise HilError(
                                    "slot %d readback is not the exact installed "
                                    "image; refuse erase -- HOLD" % TEMP_SLOT)
                            tr.note("uninstall-readback-exact", sha=envelope_sha)
                            # Control path must already have returned to live
                            # (verified in Phase 2); re-verify read-only first.
                            cable.end()
                        with open_serial() as port:
                            link = LiveLink(port,
                                            timeout=TIMEOUTS["live_call"])
                            attest_live_identity(link, "pre-uninstall")
                            attest_live_idle(link, "pre-uninstall")
                        with open_serial() as port:
                            cable = Cable(
                                port, log=lambda *a: tr.note("cable-log", detail=
                                                             " ".join(str(x) for x
                                                                      in a)[:160]),
                                timeout=TIMEOUTS["commit"])
                            if not cable.handshake(
                                    CABLE_HANDSHAKE,
                                    wait=TIMEOUTS["handshake"]):
                                raise HilError("no cable session for erase; HOLD")
                            serials2 = Serials(start=(serial_used or 0) + 1)
                            try:
                                serials2.advance_after(cable.serial_watermark())
                            except InstallError:
                                pass
                            erase_serial = serials2.next()
                            cable.remove_slot(TEMP_SLOT, None, erase_serial)
                            fresh = cable.library_directory()
                            if TEMP_SLOT in fresh:
                                raise HilError("slot %d still present after "
                                               "erase; HOLD" % TEMP_SLOT)
                            # KUL2 synthesizes 0xFF reads for a missing
                            # logical slot. Directory absence + pre/post
                            # exact object hashes are the meaningful checks.
                            tr.note("uninstalled", slot=TEMP_SLOT,
                                    erase_serial=erase_serial)
                            cable.end()
            except HilError:
                raise
            # ---- post-session read-only verification (full 31 original hashes) ----
            with nullcontext():
                with serial_lock(args.port):
                    with open_serial() as port:
                        cable = Cable(port, log=lambda *a: None,
                                      timeout=TIMEOUTS["cable_call"])
                        if not cable.handshake(
                                CABLE_HANDSHAKE,
                                wait=TIMEOUTS["handshake"]):
                            raise HilError("no cable session for post checks")
                        directory = cable.library_directory()
                        if TEMP_SLOT in directory:
                            raise HilError("temp slot still occupied post-run")
                        if len(directory) != EXPECTED_OCCUPIED_SLOTS:
                            raise HilError("post-run directory holds %d slots, "
                                           "expected %d" % (len(directory),
                                                            EXPECTED_OCCUPIED_SLOTS
                                                            ))
                        snap = json.loads((priv / "snapshot.json").read_text(
                            encoding="utf-8"))
                        for slot_str, meta in snap["slot_hashes"].items():
                            remaining()
                            data = cable.library_read(int(slot_str),
                                                      meta["len"])
                            if hashlib.sha256(data).hexdigest() != meta["sha256"]:
                                raise HilError("post-run slot %s differs from "
                                               "pre-install snapshot" % slot_str)
                        tr.note("post-slots-verified", count=len(directory))
                        cable.end()
                    with open_serial() as port:
                        link = LiveLink(port, timeout=TIMEOUTS["live_call"])
                        st = attest_live_idle(link, "post-run")
                        attest_live_identity(link, "post-run")
                        evidence = {
                            "session_digest": digest,
                            "actor": ack["actor"],
                            "build": EXPECTED_BUILD_HEX,
                            "api_max": EXPECTED_API_MAX,
                            "temp_slot": TEMP_SLOT,
                            "envelope_sha256": envelope_sha,
                            "pre_snapshot_slots": EXPECTED_OCCUPIED_SLOTS,
                            "post_slots": len(directory),
                            "vfos_hz": [v["hz"] for v in st["vfos"]],
                            "standby_idle": st["standby_idle"],
                            "transmitting": st["transmitting"],
                            "tx_proof": tx_proof,
                            "no_kiss_data_written": tx_proof["data_frames_sent"] == 0,
                            "transmitting_not_observed_at_pre_post_samples":
                                not any(tx_proof["transmitting_seen"]),
                            "continuous_rf_monitoring_performed": False,
                        }
                        (priv / "evidence.json").write_text(
                            json.dumps(evidence, indent=2, sort_keys=True),
                            encoding="utf-8")
                        tr.note("evidence", **{k: v for k, v in evidence.items()
                                               if k != "tx_proof"})
        tr.note("done")
        tr.close()
        print(json.dumps({"ok": True, "private_dir": str(priv),
                          "session_digest": digest,
                          "envelope_sha256": envelope_sha}, indent=2))
        return 0
    except HilError as exc:
        try:
            tr.note("ABORT", detail=str(exc)[:500], mutated=mutated,
                    temp_slot_known=bool(envelope))
        except Exception:
            pass
        try:
            tr.close()
        except Exception:
            pass
        if mutated:
            write_hold_file(str(exc), digest if "digest" in dir() else "?")
            print("HIL ABORT (bench on HOLD): %s" % exc, file=sys.stderr)
            print("Inspect the radio; see %s and %s" % (
                priv if "priv" in dir() else "(no private dir)",
                BENCH_HOLD_FILE), file=sys.stderr)
            return 3
        print("HIL ABORT (nothing mutated): %s" % exc, file=sys.stderr)
        return 2
    except (InstallError, LiveUnavailable, OSError) as exc:
        try:
            tr.note("ABORT-TRANSPORT", detail=str(exc)[:500], mutated=mutated)
        except Exception:
            pass
        try:
            tr.close()
        except Exception:
            pass
        if mutated:
            write_hold_file("transport failure after install: %s" % exc,
                            digest if "digest" in dir() else "?")
            print("HIL ABORT (bench on HOLD): %s" % exc, file=sys.stderr)
            return 3
        print("HIL ABORT (nothing mutated): %s" % exc, file=sys.stderr)
        return 2


    except BaseException as exc:
        # An unanticipated parser/transport assertion, tool exception, or
        # keyboard interrupt is never permission to assume state restored.
        try:
            tr.note("ABORT-UNEXPECTED", detail=f"{type(exc).__name__}: {exc}"[:500],
                    mutated=mutated)
            tr.close()
        except Exception:
            pass
        if mutated:
            write_hold_file("unexpected failure after install attempt: "
                            f"{type(exc).__name__}: {exc}",
                            digest if "digest" in locals() else "unavailable")
            print(f"HIL ABORT (bench on HOLD): {exc}", file=sys.stderr)
            return 3
        print(f"HIL ABORT (no mutation): {exc}", file=sys.stderr)
        return 2


def build_parser():
    p = argparse.ArgumentParser(
        description="Opt-in one-shot NO-RF KISS TNC hardware qualification "
                    "(default: offline freeze only, no device access).")
    p.add_argument("--execute", action="store_true",
                   help="perform the one-shot hardware session (requires "
                        "--lease-ack, --trusted-actor, locks, and the radio)")
    p.add_argument("--lease-ack", default=None,
                   help="path to the external issue-#11 trusted-actor lease "
                        "ACK JSON (fresh, exact session digest)")
    p.add_argument("--trusted-actor", default=None,
                   help="trusted actor name the ACK must carry")
    p.add_argument("--lease-max-age-s", type=float, default=900.0)
    p.add_argument("--port", default=DEFAULT_PORT)
    p.add_argument("--bench-lock", default=DEFAULT_BENCH_LOCK)
    p.add_argument("--private-dir", default=None)
    p.add_argument("--freeze-out", default=None,
                   help="write the freeze manifest JSON here (default mode)")
    p.add_argument("--lock-timeout", type=float, default=TIMEOUTS["lock"])
    p.add_argument("--session-timeout", type=float, default=TIMEOUTS["session"])
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not args.execute:
        manifest, _code, _entry = freeze()
        out = {"freeze": manifest, "session_digest": session_digest(manifest),
               "note": "validate-only; no device, lock, or network touched; "
                       "pass --execute with a fresh issue-#11 lease ACK for "
                       "the one-shot session"}
        text = json.dumps(out, indent=2, sort_keys=True)
        if args.freeze_out:
            Path(args.freeze_out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    if not args.lease_ack or not args.trusted_actor:
        print("--execute requires --lease-ack and --trusted-actor",
              file=sys.stderr)
        return 2
    if args.port != DEFAULT_PORT:
        print("refusing unexpected port %r (expected %s)" % (
            args.port, DEFAULT_PORT), file=sys.stderr)
        return 2
    if not (0 < args.session_timeout <= TIMEOUTS["session"]):
        print("session timeout must be in (0, 300] seconds", file=sys.stderr)
        return 2
    if not (0 < args.lease_max_age_s <= 900):
        print("lease freshness must be in (0, 900] seconds", file=sys.stderr)
        return 2
    return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
