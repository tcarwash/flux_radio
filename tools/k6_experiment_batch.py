#!/usr/bin/env python3
"""Flux K6 multi-experiment bench-session coordinator (OFFLINE by default).

This runner does not transmit, flash, or operate serial devices by itself.
Trusted, hash-pinned experiment harness commands execute only with explicit
per-session approval. Each harness must enforce its own serial_lock and TX
watchdog; the coordinator's lock prevents concurrent *batch runners* only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any

SCHEMA = 1
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
ACK_RE = re.compile(r"^https://github\.com/[^/]+/[^/]+/issues/\d+#issuecomment-\d+$")
STEPS = ("preflight", "run", "restore", "verify")
GLOBAL_STEPS = ("session_preflight", "session_restore", "session_verify")
MAX_JOBS = 20
MAX_TIMEOUT = 300
API_CONFIG = Path(__file__).resolve().parents[1] / "config/k6-experiment-runner.json"


class Refused(RuntimeError):
    """Fail closed: no claim of authorization, safe state, or successful test."""


def read_api_config(path: Path) -> tuple[dict[str, Any], bytes]:
    """Load the editable API target; do not mistake it for radio attestation."""
    raw = path.read_bytes()
    try:
        cfg = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused(f"invalid API configuration: {exc}") from exc
    if (not isinstance(cfg, dict) or set(cfg) != {"schema", "target_module_api", "notes"}
            or type(cfg["schema"]) is not int or cfg["schema"] != 1
            or type(cfg["target_module_api"]) is not int
            or not 1 <= cfg["target_module_api"] <= 255
            or not isinstance(cfg["notes"], str)):
        raise Refused("API configuration must declare schema=1 and target_module_api=1..255")
    return cfg, raw


def check_api_compatibility(m: dict, cfg: dict) -> list[str]:
    """Return execution blockers; older minimum APIs remain valid on newer hosts.

    A module requesting newer API cannot run on the configured development host.
    Missing requirements are not silently treated as compatible.
    """
    blockers = []
    target = cfg["target_module_api"]
    for job in m["jobs"]:
        required = job.get("minimum_module_api")
        if required is None:
            blockers.append(f"{job['id']}: minimum_module_api missing")
        elif required > target:
            blockers.append(f"{job['id']}: requires API {required}, configured host target is {target}")
    return blockers


def read_manifest(path: Path) -> tuple[dict[str, Any], str, bytes]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Refused(f"invalid manifest JSON: {exc}") from exc
    validate_manifest(data)
    return data, digest, raw


def _step(value: Any, name: str) -> None:
    if not isinstance(value, dict) or set(value) != {"argv", "timeout_seconds"}:
        raise Refused(f"{name}: expected argv and timeout_seconds")
    argv, timeout = value["argv"], value["timeout_seconds"]
    if not isinstance(argv, list) or not argv or not all(
        isinstance(a, str) and a and "\0" not in a for a in argv
    ):
        raise Refused(f"{name}: argv must be a nonempty string array")
    # A fixed executable path makes PATH hijacking less likely and review simpler.
    if not Path(argv[0]).is_absolute():
        raise Refused(f"{name}: argv[0] must be an absolute executable path")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= MAX_TIMEOUT:
        raise Refused(f"{name}: timeout_seconds must be 1..{MAX_TIMEOUT}")


def _sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _validate_pins(pins: Any, label: str) -> None:
    if not isinstance(pins, list) or not pins:
        raise Refused(f"{label}: at least one pinned file required")
    for pin in pins:
        if not isinstance(pin, dict) or set(pin) != {"path", "sha256"}:
            raise Refused(f"{label}: pin must contain path and sha256")
        if not isinstance(pin["path"], str) or not Path(pin["path"]).is_absolute():
            raise Refused(f"{label}: pin paths must be absolute")
        if not isinstance(pin["sha256"], str) or not SHA_RE.fullmatch(pin["sha256"]):
            raise Refused(f"{label}: invalid SHA256 pin")


def validate_manifest(m: Any) -> None:
    if not isinstance(m, dict) or set(m) != {
        "version", "session_id", "port", "queue_issue_url",
        "lease_expires_utc", "session_pins", "session_preflight",
        "session_restore", "session_verify", "jobs"
    }:
        raise Refused("manifest: missing or unknown top-level keys")
    if m["version"] != SCHEMA:
        raise Refused(f"manifest: require version {SCHEMA}")
    if not isinstance(m["session_id"], str) or not ID_RE.fullmatch(m["session_id"]):
        raise Refused("manifest: invalid session_id")
    if not isinstance(m["port"], str) or not m["port"].startswith("/dev/"):
        raise Refused("manifest: port must identify a Linux /dev device")
    if not isinstance(m["queue_issue_url"], str) or not re.fullmatch(
        r"https://github\.com/[^/]+/[^/]+/issues/\d+", m["queue_issue_url"]
    ):
        raise Refused("manifest: queue_issue_url required")
    try:
        expires = dt.datetime.fromisoformat(m["lease_expires_utc"].replace("Z", "+00:00"))
        if expires.tzinfo is None:
            raise ValueError("timezone required")
    except (ValueError, AttributeError) as exc:
        raise Refused("manifest: lease_expires_utc must be an offset-aware ISO timestamp") from exc
    for name in GLOBAL_STEPS:
        _step(m[name], name)
    _validate_pins(m["session_pins"], "session_pins")
    jobs = m["jobs"]
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= MAX_JOBS:
        raise Refused(f"manifest: expected 1..{MAX_JOBS} jobs")
    seen = set()
    for i, j in enumerate(jobs):
        if not isinstance(j, dict) or not {"id", "issue_url", "cwd", "pins", *STEPS}.issubset(j) or set(j) - {"id", "issue_url", "cwd", "pins", "minimum_module_api", *STEPS}:
            raise Refused(f"jobs[{i}]: missing or unknown keys")
        if "minimum_module_api" in j and (type(j["minimum_module_api"]) is not int or not 1 <= j["minimum_module_api"] <= 255):
            raise Refused(f"jobs[{i}]: minimum_module_api must be 1..255")
        if not isinstance(j["id"], str) or not ID_RE.fullmatch(j["id"]) or j["id"] in seen:
            raise Refused(f"jobs[{i}]: invalid or duplicate job ID")
        seen.add(j["id"])
        if not isinstance(j["issue_url"], str) or not re.fullmatch(
            r"https://github\.com/[^/]+/[^/]+/issues/\d+", j["issue_url"]
        ):
            raise Refused(f"jobs[{i}]: invalid issue URL")
        if not isinstance(j["cwd"], str) or not Path(j["cwd"]).is_absolute():
            raise Refused(f"jobs[{i}]: cwd must be an absolute directory")
        _validate_pins(j["pins"], f"jobs[{i}].pins")
        for name in STEPS:
            _step(j[name], f"jobs[{i}].{name}")


def _check_pins(j: dict) -> None:
    if "cwd" in j and not Path(j["cwd"]).is_dir():
        raise Refused(f"{j['id']}: missing worktree {j['cwd']}")
    for pin in j["pins"]:
        p = Path(pin["path"])
        try:
            actual = _sha_file(p)
        except OSError as exc:
            raise Refused(f"{j['id']}: missing frozen source {p}: {exc}") from exc
        if actual != pin["sha256"]:
            raise Refused(f"{j['id']}: source drift {p}; expected {pin['sha256']}, got {actual}")


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def check_command_pins(m: dict) -> None:
    """Every invoked program/script must appear in the corresponding file pins.

    This is a review invariant, NOT a sandbox. Python imports and child
    executables remain the responsibility of the audited local harness.
    """
    interpreters={"/usr/bin/python3", "/usr/bin/python3.14", "/usr/bin/bash", "/bin/bash", sys.executable}
    for steps, pins, label in [
        (GLOBAL_STEPS, m["session_pins"], "session"),
        *[(STEPS, j["pins"], j["id"]) for j in m["jobs"]],
    ]:
        reviewed={str(Path(p["path"]).resolve()) for p in pins}
        container=m if label=="session" else next(j for j in m["jobs"] if j["id"]==label)
        for stage in steps:
            argv=container[stage]["argv"]
            executable=str(Path(argv[0]).resolve())
            if argv[0] in interpreters:
                if len(argv)<2 or argv[1].startswith("-"):
                    raise Refused(f"{label}.{stage}: unreviewed interpreter arguments")
                script=str((Path(container.get("cwd", Path(__file__).resolve().parents[1]))/argv[1]).resolve())
                if script not in reviewed:
                    raise Refused(f"{label}.{stage}: entrypoint must be SHA256-pinned")
            elif executable not in reviewed:
                raise Refused(f"{label}.{stage}: executable must be SHA256-pinned")


def _check_lease(m: dict) -> None:
    expiry = dt.datetime.fromisoformat(m["lease_expires_utc"].replace("Z", "+00:00"))
    if _utc_now() >= expiry:
        raise Refused("session lease expired; no further experiment commands permitted")


def authorize(m: dict, digest: str, *, approved_digest: str | None,
              ack_url: str | None, operator: str | None,
              dummy_load_confirmed: bool) -> None:
    if approved_digest != digest:
        raise Refused("manifest digest not explicitly approved (or file changed)")
    if not isinstance(ack_url, str) or not ACK_RE.fullmatch(ack_url):
        raise Refused("independent, specific GitHub hardware lease ACK comment URL required")
    # Syntax is checked, not whether the external ACK is genuine or still valid.
    if not isinstance(operator, str) or not ID_RE.fullmatch(operator):
        raise Refused("named bench operator required")
    if not dummy_load_confirmed:
        raise Refused("operator must explicitly confirm the connected dummy load")
    _check_lease(m)


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def _event(state: dict, output: Path, **kwargs: Any) -> None:
    state["events"].append({"at_utc": _utc_now().isoformat(), **kwargs})
    _write_private(output / "result.json", json.dumps(state, indent=2).encode())


def _run_step(step: dict, *, cwd: Path, name: str, output: Path) -> dict:
    """Run without a shell; isolate process group so timeout stops its children."""
    start = time.monotonic()
    record: dict[str, Any] = {"name": name, "argv": step["argv"], "status": "ERROR"}
    logpath = output / f"{name}.log"
    with os.fdopen(os.open(logpath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as out:
        try:
            proc = subprocess.Popen(
                step["argv"], cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy(),
            )
            try:
                rc = proc.wait(timeout=step["timeout_seconds"])
                record["returncode"] = rc
                record["status"] = "PASS" if rc == 0 else "FAIL"
            except subprocess.TimeoutExpired:
                record["status"] = "TIMEOUT"
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=2)
        except OSError as exc:
            out.write(f"Could not start process: {exc}\n".encode())
            record["error"] = str(exc)
    record["elapsed_seconds"] = round(time.monotonic() - start, 3)
    record["log"] = logpath.name
    return record


def run_session(m: dict, raw: bytes, digest: str, *, output_root: Path,
                lock_root: Path, approved_digest: str, ack_url: str,
                operator: str, dummy_load_confirmed: bool,
                api_config: dict | None = None) -> dict:
    check_command_pins(m)
    if api_config is not None:
        blockers = check_api_compatibility(m, api_config)
        if blockers:
            raise Refused("API compatibility blocked: " + "; ".join(blockers))
    authorize(m, digest, approved_digest=approved_digest, ack_url=ack_url,
              operator=operator, dummy_load_confirmed=dummy_load_confirmed)
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Session lock is separate from the port lock. Children can take the canonical
    # port lock themselves. Other agents must honor the GitHub lease protocol.
    lockpath = lock_root / (hashlib.sha256(m["port"].encode()).hexdigest() + ".batch.lock")
    with lockpath.open("a+b") as lockfile:
        try:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Refused("another batch session holds the same radio resource") from exc
        _check_lease(m)
        output_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = output_root / m["session_id"]
        # Do not reuse IDs or overwrite evidence of an earlier attempt.
        directory.mkdir(mode=0o700)
        _write_private(directory / "manifest.json", raw)
        result = {
            "session_id": m["session_id"],
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
            "approval_sha256": digest,
            "queue_issue_url": m["queue_issue_url"],
            "ack_url": ack_url,
            "operator": operator,
            "result": "BLOCKED",
            "events": [],
            "jobs": [],
        }
        _event(result, directory, kind="SESSION_START")
        success = False
        try:
            def stage(step: dict, cwd: Path, label: str, *, require_lease: bool = True) -> bool:
                if require_lease:
                    _check_lease(m)
                item = _run_step(step, cwd=cwd, name=label, output=directory)
                _event(result, directory, kind="STEP", **item)
                return item["status"] == "PASS"

            base = Path(__file__).resolve().parents[1]
            _check_pins({"id": "session", "pins": m["session_pins"]})
            if not stage(m["session_preflight"], base, "session_preflight"):
                raise Refused("session preflight failed")
            for i, job in enumerate(m["jobs"]):
                _check_pins(job)
                name = f"job{i + 1:02d}-{job['id']}"
                current = {"id": job["id"], "issue_url": job["issue_url"], "result": "BLOCKED"}
                result["jobs"].append(current)
                _event(result, directory, kind="JOB_START", job=job["id"])
                ok = False
                clean = False
                try:
                    if not stage(job["preflight"], Path(job["cwd"]), name + "-preflight"):
                        raise Refused(f"{job['id']}: preflight failed")
                    _check_pins(job)
                    if not stage(job["run"], Path(job["cwd"]), name + "-run"):
                        raise Refused(f"{job['id']}: execution failed")
                    ok = True
                finally:
                    # Attempt both regardless of the job's execution outcome.
                    try:
                        restored = stage(job["restore"], Path(job["cwd"]), name + "-restore", require_lease=False)
                    except Refused:
                        restored = False
                    try:
                        verified = stage(job["verify"], Path(job["cwd"]), name + "-verify", require_lease=False)
                    except Refused:
                        verified = False
                    clean = restored and verified
                    current["result"] = "PASS" if ok and clean else "BLOCKED"
                    _event(result, directory, kind="JOB_END", job=job["id"],
                           result=current["result"], restored=restored, verified=verified)
                if not clean:
                    raise Refused(f"{job['id']}: restoration or verification failed")
                if not ok:
                    raise Refused(f"{job['id']}: did not pass")
            success = True
        except (Refused, OSError) as exc:
            _event(result, directory, kind="ABORT", error=str(exc))
        except BaseException as exc:
            _event(result, directory, kind="ABORT", error=f"{type(exc).__name__}: {exc}")
            raise
        finally:
            base = Path(__file__).resolve().parents[1]
            # Always attempt session restore/verification, including on failures.
            def final_stage(key: str) -> bool:
                try:
                    item = _run_step(m[key], cwd=base, name=key, output=directory)
                    _event(result, directory, kind="STEP", **item)
                    return item["status"] == "PASS"
                except (OSError, Exception) as exc:
                    _event(result, directory, kind="CLEANUP_ERROR", step=key, error=str(exc))
                    return False
            restored = final_stage("session_restore")
            verified = final_stage("session_verify")
            result["result"] = "PASS" if success and restored and verified else "BLOCKED"
            _event(result, directory, kind="SESSION_END", result=result["result"],
                   restored=restored, verified=verified)
        return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--api-config", type=Path, default=API_CONFIG,
                    help="editable development-host API target file")
    ap.add_argument("--execute", action="store_true", help="opt into approved, real commands")
    ap.add_argument("--approve-digest", help="exact SHA256 printed by dry run")
    ap.add_argument("--ack-url", help="independent GitHub lease ACK comment URL")
    ap.add_argument("--operator", help="operator identity")
    ap.add_argument("--dummy-load-confirmed", action="store_true")
    ap.add_argument("--output-root", type=Path, default=Path.home() / "flux-bench" / "batches")
    ap.add_argument("--lock-root", type=Path, default=Path.home() / ".cache" / "flux" / "batch-locks")
    args = ap.parse_args(argv)
    try:
        m, manifest_digest, raw = read_manifest(args.manifest)
        cfg, cfg_raw = read_api_config(args.api_config)
        # The approval digest commits to both source manifest and API policy.
        digest = hashlib.sha256(raw + b"\nAPI-CONFIG\n" + cfg_raw).hexdigest()
        blockers = check_api_compatibility(m, cfg)
        if not args.execute:
            print(json.dumps({
                "mode": "DRY_RUN_NO_COMMANDS",
                "session_id": m["session_id"],
                "approval_sha256": digest,
                "manifest_sha256": manifest_digest,
                "api_config_sha256": hashlib.sha256(cfg_raw).hexdigest(),
                "target_module_api": cfg["target_module_api"],
                "api_blockers": blockers,
                "jobs": [{"id": j["id"], "issue": j["issue_url"],
                           "minimum_module_api": j.get("minimum_module_api")} for j in m["jobs"]],
                "expiry": m["lease_expires_utc"],
                "execution_requirements": "fresh ACK, approved digest, operator, dummy load",
            }, indent=2))
            return 0
        if blockers:
            raise Refused("API compatibility blocked: " + "; ".join(blockers))
        # The public batch CLI is a preview tool. Only the trusted hardware
        # wrapper may invoke run_session after real issue, lease and bench checks.
        raise Refused("direct batch execution disabled; use k6_experiment_hardware.py")
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
