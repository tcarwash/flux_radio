#!/usr/bin/env python3
"""Shared, device-free experiment results ledger. Never invokes the hardware queue.

This is a results registry, NOT a scheduler or a command execution interface.
Run offline tests yourself, then register/finish them here. The existing fixture
sweep imports its signed-off local execution receipts automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid

DEFAULT_DB = Path.home() / ".local/state/flux-experiments/offline-results.sqlite3"
DEFAULT_SWEEP = Path.home() / ".local/state/flux-experiment-autopilot/offline-sweep.json"
DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "config/k6-experiment-offline-sweep.json"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FINAL = {"PASS", "FAIL", "BLOCKED"}
SWEEP_TO_STATUS = {
    "PASS": "PASS",
    "FAIL_TESTS": "FAIL",
    "TIMEOUT": "FAIL",
    "INFRA_FAILURE": "BLOCKED",
    "NO_TESTS_REPORTED": "BLOCKED",
    "MISSING_LOCAL_TEST": "BLOCKED",
}


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def database(path=None):
    dest = Path(path or os.getenv("FLUX_OFFLINE_RESULTS_DB") or DEFAULT_DB).expanduser()
    dest.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    con = sqlite3.connect(str(dest), timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
       CREATE TABLE IF NOT EXISTS results (
           id TEXT PRIMARY KEY,
           experiment TEXT NOT NULL,
           scope TEXT NOT NULL DEFAULT 'OFFLINE' CHECK(scope='OFFLINE'),
           status TEXT NOT NULL CHECK(status IN ('REGISTERED','RUNNING','PASS','FAIL','BLOCKED')),
           owner TEXT NOT NULL,
           source_sha TEXT,
           source_ref TEXT,
           tests_json TEXT NOT NULL DEFAULT '[]',
           summary TEXT NOT NULL DEFAULT '',
           metrics_json TEXT NOT NULL DEFAULT '{}',
           log_path TEXT,
           log_sha256 TEXT,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL,
           finished_at TEXT,
           provenance TEXT NOT NULL,
           hardware_operations INTEGER NOT NULL DEFAULT 0 CHECK(hardware_operations=0),
           rf_transmissions INTEGER NOT NULL DEFAULT 0 CHECK(rf_transmissions=0)
       )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_results_updated ON results(updated_at DESC)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_results_status ON results(status)")
    # Directory is already private; also keep bare DB mode private.
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return con


def as_dict(row):
    d = dict(row)
    d["tests"] = json.loads(d.pop("tests_json"))
    d["metrics"] = json.loads(d.pop("metrics_json"))
    return d


def checks(name, owner, sha, tests, metrics, log_hash):
    if not name or not name.strip() or len(name) > 220:
        raise ValueError("experiment name is required (max 220 characters)")
    if not owner or not owner.strip() or len(owner) > 100:
        raise ValueError("owner is required (max 100 characters)")
    if sha is not None and not HEX40.fullmatch(sha):
        raise ValueError("source SHA must be a full 40-character lowercase hex commit")
    if not isinstance(tests, list) or len(tests) > 100 or any(not isinstance(t, str) or len(t)>240 for t in tests):
        raise ValueError("tests must be a list of test file names")
    if not isinstance(metrics, dict):
        raise ValueError("metrics must be an object")
    if log_hash is not None and not HEX64.fullmatch(log_hash):
        raise ValueError("log digest must be sha256 lowercase hex")


def register(con, *, name, owner, sha=None, ref=None, tests=(), summary="", rid=None, provenance="agent"):
    checks(name, owner, sha, list(tests), {}, None)
    rid = rid or "off-" + uuid.uuid4().hex[:20]
    now = timestamp()
    with con:
        con.execute("""INSERT INTO results
            (id, experiment, status, owner, source_sha, source_ref, tests_json,
             summary, created_at, updated_at, provenance)
            VALUES (?, ?, 'REGISTERED', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, name.strip(), owner.strip(), sha, ref, json.dumps(list(tests)),
             summary, now, now, provenance))
    return rid


def finish(con, rid, *, status, summary, metrics=None, log_path=None, log_sha256=None):
    if status not in FINAL:
        raise ValueError("finish status must be PASS, FAIL or BLOCKED")
    metrics = metrics or {}
    row = con.execute("SELECT * FROM results WHERE id=?", (rid,)).fetchone()
    if row is None:
        raise ValueError("unknown offline result ID")
    checks(row["experiment"], row["owner"], row["source_sha"],
           json.loads(row["tests_json"]), metrics, log_sha256)
    if not summary or not summary.strip():
        raise ValueError("finished experiments need a nonempty conclusion")
    if len(summary) > 8000:
        raise ValueError("summary too long")
    if row["status"] in FINAL:
        raise ValueError("already finalized; register a new run instead of overwriting evidence")
    now = timestamp()
    with con:
        changed = con.execute("""UPDATE results SET status=?, summary=?, metrics_json=?,
             log_path=?, log_sha256=?, updated_at=?, finished_at=?
             WHERE id=? AND status IN ('REGISTERED','RUNNING')""",
             (status, summary.strip(), json.dumps(metrics, sort_keys=True),
              log_path, log_sha256, now, now, rid)).rowcount
    if not changed:
        raise ValueError("concurrent finalization; result already finalized")
    return as_dict(con.execute("SELECT * FROM results WHERE id=?", (rid,)).fetchone())


def import_sweep(state_path=DEFAULT_SWEEP, *, con=None, config_path=DEFAULT_CONFIG):
    """Idempotently migrate terminal device-free sweep receipts.

    Historical issue-number keys are only legacy input identifiers, never
    Github requests, output IDs, or physical queue registrations.
    """
    state = json.loads(Path(state_path).read_text())
    if state.get("schema") != 1 or not isinstance(state.get("jobs"), dict):
        raise ValueError("invalid offline sweep receipt journal")
    refs = {}
    if Path(config_path).exists():
        cfg = json.loads(Path(config_path).read_text())
        refs = {str(k): v.get("ref") for k, v in cfg.get("jobs", {}).items()}
    own = con is None
    con = con or database()
    imported = 0
    already = 0
    try:
        for legacy_key, item in sorted(state["jobs"].items()):
            status = SWEEP_TO_STATUS.get(item.get("result"))
            if status is None:
                continue
            tests = item.get("tests", [])
            sha = item.get("source_sha")
            if not HEX40.fullmatch(str(sha)) or not isinstance(tests, list):
                raise ValueError("malformed offline sweep provenance")
            base = json.dumps({"source_sha": sha, "tests": tests}, sort_keys=True)
            rid = "sweep-" + hashlib.sha256(base.encode()).hexdigest()[:24]
            name = "Fixture: " + ", ".join(Path(t).stem for t in tests)
            issue = str(item.get("issue", ""))
            passed = int(item.get("test_passed", 0))
            metrics = {"tests_passed": passed, "elapsed_seconds": item.get("elapsed_seconds"),
                       "exit_code": item.get("exit_code"),
                       "original_result": item["result"]}
            digest = item.get("log_sha256")
            if digest is not None and not HEX64.fullmatch(str(digest)):
                raise ValueError("invalid fixture log digest")
            checks(name, "fixture-sweep", sha, tests, metrics, digest)
            conclusion = (
                f"{passed} offline fixture tests passed; no radio used" if status == "PASS"
                else f"Offline fixture run {item['result']}; no radio used"
            )
            now = item.get("at") or timestamp()
            with con:
                changed = con.execute("""INSERT OR IGNORE INTO results
                    (id, experiment, status, owner, source_sha, source_ref, tests_json,
                    summary, metrics_json, log_path, log_sha256, created_at, updated_at,
                    finished_at, provenance)
                    VALUES (?, ?, ?, 'fixture-sweep', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'fixture-sweep')""",
                    (rid, name, status, sha, refs.get(issue), json.dumps(tests),
                     conclusion, json.dumps(metrics, sort_keys=True), item.get("log"),
                     digest, now, now, now)).rowcount
            imported += changed
            already += not changed
    finally:
        if own:
            con.close()
    return {"imported": imported, "already_present": already, "source": str(state_path)}


def query(con, *, text="", status=None, limit=25):
    if not 1 <= limit <= 500:
        raise ValueError("limit must be 1..500")
    if status is not None and status not in FINAL | {"REGISTERED","RUNNING"}:
        raise ValueError("invalid status filter")
    sql = "SELECT * FROM results WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if text:
        sql += """ AND (experiment LIKE ? OR summary LIKE ? OR
                    tests_json LIKE ? OR source_ref LIKE ? OR source_sha LIKE ?)"""
        args.extend(["%" + text + "%"] * 5)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    args.append(limit)
    return [as_dict(r) for r in con.execute(sql, args)]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("register", help="Register work; run offline tests independently")
    a.add_argument("--name", required=True)
    a.add_argument("--owner", required=True)
    a.add_argument("--sha")
    a.add_argument("--ref")
    a.add_argument("--test", action="append", default=[])
    a.add_argument("--summary", default="")
    a = sub.add_parser("finish", help="Finalize one registered offline run")
    a.add_argument("id")
    a.add_argument("--status", choices=sorted(FINAL), required=True)
    a.add_argument("--summary", required=True)
    a.add_argument("--metrics-json", default="{}")
    a.add_argument("--log")
    a.add_argument("--log-sha256")
    a = sub.add_parser("list", help="Search all shared offline results")
    a.add_argument("--query", default="")
    a.add_argument("--status", choices=["REGISTERED", "RUNNING"] + sorted(FINAL))
    a.add_argument("--limit", type=int, default=25)
    a = sub.add_parser("show")
    a.add_argument("id")
    a = sub.add_parser("import-sweep", help="Backfill fixture-sweep receipts, idempotent")
    a.add_argument("--state", type=Path, default=DEFAULT_SWEEP)
    a.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = p.parse_args(argv)
    try:
        with database() as con:
            if args.cmd == "register":
                rid = register(con, name=args.name, owner=args.owner, sha=args.sha,
                               ref=args.ref, tests=args.test, summary=args.summary)
                result = as_dict(con.execute("SELECT * FROM results WHERE id=?", (rid,)).fetchone())
            elif args.cmd == "finish":
                metrics = json.loads(args.metrics_json)
                result = finish(con, args.id, status=args.status, summary=args.summary,
                                metrics=metrics, log_path=args.log, log_sha256=args.log_sha256)
            elif args.cmd == "list":
                result = {"scope": "OFFLINE", "results": query(con, text=args.query, status=args.status,
                                                               limit=args.limit)}
            elif args.cmd == "show":
                row = con.execute("SELECT * FROM results WHERE id=?", (args.id,)).fetchone()
                if not row:
                    raise ValueError("unknown result ID")
                result = as_dict(row)
            else:
                result = import_sweep(args.state, con=con, config_path=args.config)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, sqlite3.Error, ValueError, json.JSONDecodeError) as exc:
        print(f"OFFLINE RESULT ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
