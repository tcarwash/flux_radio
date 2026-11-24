#!/usr/bin/env python3
"""Run existing owner-owned RF research tests in parallel, entirely device-free.

A sweep is a historical offline reproduction, NOT an on-radio qualification
or permission to key a transmitter. GitHub issues supply IDs, never code.
Only local pinned refs and explicit test paths in the checked-in config run.
"""
from __future__ import annotations

import argparse
import concurrent.futures
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

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/k6-experiment-offline-sweep.json"
STATE = Path.home() / ".local/state/flux-experiment-autopilot/offline-sweep.json"
OUT = Path.home() / ".local/state/flux-experiment-autopilot/offline-sweeps"
REPO = Path("/storage5/dev/ag7su-blowfang")
WORKSPACES = Path("/storage5/dev")
PYTHON = Path("/home/tyler/.local/share/flux-rf-venv/bin/python")
SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^origin/research/[A-Za-z0-9_.-]+$")
TEST = re.compile(r"^tests/test_[A-Za-z0-9_]+\.py$")


class Refused(RuntimeError):
    pass


def command(*argv, timeout=30, cwd=None):
    p = subprocess.run(list(argv), cwd=cwd, capture_output=True,
                       text=True, check=True, timeout=timeout)
    return p.stdout.strip()


def config_load(path=CONFIG):
    c = json.loads(Path(path).read_text())
    if set(c) != {"schema", "enabled", "repo", "max_parallel", "max_per_cycle",
                  "timeout_seconds", "jobs"} or c["schema"] != 1:
        raise Refused("invalid offline sweep configuration")
    if c["repo"] != "tcarwash/flux_radio" or type(c["enabled"]) is not bool:
        raise Refused("untrusted offline sweep repository")
    if not 1 <= c["max_parallel"] <= 6 or not 1 <= c["max_per_cycle"] <= 32:
        raise Refused("invalid bounded batch sizing")
    if not 10 <= c["timeout_seconds"] <= 240:
        raise Refused("invalid per-job timeout")
    if not isinstance(c["jobs"], dict):
        raise Refused("invalid offline job set")
    for n, j in c["jobs"].items():
        if not re.fullmatch(r"[1-9]\d*", n):
            raise Refused("invalid source issue number")
        if set(j) != {"ref", "tests"} or not REF.fullmatch(j["ref"]):
            raise Refused("unreviewed offline source ref")
        if not isinstance(j["tests"], list) or not 1 <= len(j["tests"]) <= 5:
            raise Refused("invalid test scope")
        if any(not isinstance(t, str) or not TEST.fullmatch(t) for t in j["tests"]):
            raise Refused("test path is not an explicit allowed pytest target")
    return c


def load_state(path=STATE):
    try:
        d = json.loads(Path(path).read_text())
    except FileNotFoundError:
        d = {"schema": 1, "jobs": {}}
    if set(d) != {"schema", "jobs"} or d["schema"] != 1 or not isinstance(d["jobs"], dict):
        raise Refused("invalid durable sweep state")
    return d


def write_state(path, data):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    tmp = path.with_suffix(".pending")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def sandbox_args(worktree, tests):
    # No radio/USB devices, no network, no writes outside isolated checkout
    # or ephemeral tmpfs. The source under test is not a live host controller.
    return [
        "/usr/bin/bwrap", "--ro-bind", "/", "/", "--dev", "/dev",
        "--proc", "/proc", "--unshare-net", "--tmpfs", "/tmp",
        "--bind", str(worktree), str(worktree), "--chdir", str(worktree),
        "--setenv", "XDG_CACHE_HOME", "/tmp/flux-sweep-cache",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--die-with-parent", "--", str(PYTHON), "-m", "pytest", "-q", *tests,
    ]


def run_sandbox(n, sha, worktree, tests, timeout, logpath):
    started = time.monotonic()
    logpath.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    argv = sandbox_args(worktree, tests)
    with logpath.open("xb") as f:
        p = subprocess.Popen(argv, cwd=worktree, stdout=f,
                             stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        try:
            rc = p.wait(timeout=timeout)
            status = "PASS" if rc == 0 else "FAIL_TESTS"
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
            rc = 124
            status = "TIMEOUT"
    raw = logpath.read_bytes()
    text = raw.decode("utf-8", "replace")
    passed = sum(int(x) for x in re.findall(r"(?<!\d)(\d+) passed", text))
    # Require pytest to have actually executed tests: no empty-test PASS.
    if status == "PASS" and passed <= 0:
        status = "NO_TESTS_REPORTED"
    return {
        "issue": n, "source_sha": sha, "result": status, "exit_code": rc,
        "test_passed": passed, "tests": list(tests),
        "elapsed_seconds": round(time.monotonic()-started, 2),
        "log": str(logpath), "log_sha256": hashlib.sha256(raw).hexdigest(),
        "hardware_operations": 0, "rf_transmissions": 0
    }


def one_cycle(config=CONFIG, state_path=STATE, out_root=OUT, repo=REPO,
              workspaces=WORKSPACES, *, max_per_cycle=None):
    c = config_load(config)
    if not c["enabled"]:
        return {"mode": "OFFLINE_SWEEP_DISABLED", "events": []}
    if not repo.is_dir():
        raise Refused("missing trusted local firmware repository")
    limit = min(c["max_per_cycle"], max_per_cycle or c["max_per_cycle"])
    if not 1 <= limit <= 32:
        raise Refused("unsafe batch size")
    state_path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with state_path.with_suffix(".lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load_state(state_path)
        scheduled = []
        for n, rec in c["jobs"].items():
            if len(scheduled) >= limit:
                break
            sha = command("git", "-C", str(repo), "rev-parse", rec["ref"])
            if not SHA.fullmatch(sha):
                raise Refused("invalid local source SHA")
            key = f"issue-{n}:{sha}"
            if key in state["jobs"]:
                continue
            tree = command("git", "-C", str(repo), "ls-tree", "-r", "--name-only",
                           sha, "tests/")
            paths = set(tree.splitlines())
            if any(t not in paths for t in rec["tests"]):
                state["jobs"][key] = {
                    "issue": int(n), "source_sha": sha,
                    "result": "MISSING_LOCAL_TEST", "tests": rec["tests"],
                    "hardware_operations": 0
                }
                write_state(state_path, state)
                continue
            worktree = workspaces / f"flux-sweep-{n}-{sha[:10]}"
            if worktree.exists():
                raise Refused(f"unmanaged worktree already exists: {worktree}")
            # An explicitly local, reviewed SHA; never pull or execute issue text.
            command("git", "-C", str(repo), "worktree", "add",
                    "--detach", str(worktree), sha, timeout=45)
            if command("git", "-C", str(worktree), "status", "--porcelain"):
                raise Refused("non-pristine offline worktree")
            state["jobs"][key] = {
                "issue": int(n), "source_sha": sha, "result": "STARTED_NO_AUTO_RETRY",
                "tests": rec["tests"], "worktree": str(worktree),
                "at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "hardware_operations": 0
            }
            write_state(state_path, state)
            scheduled.append((key, int(n), sha, worktree, rec["tests"]))
        events = []
        out_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=c["max_parallel"]) as pool:
            futures = {
                pool.submit(run_sandbox,n,sha,wt,tests,c["timeout_seconds"],
                            out_root/f"issue-{n}-{sha[:12]}.log"): key
                for key,n,sha,wt,tests in scheduled
            }
            for future in concurrent.futures.as_completed(futures):
                key = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"result": "INFRA_FAILURE", "error": f"{type(exc).__name__}: {exc}"[:250],
                              "hardware_operations": 0, "rf_transmissions": 0}
                state["jobs"][key].update(result)
                write_state(state_path,state)
                events.append(dict(issue=state["jobs"][key]["issue"],
                                   source_sha=state["jobs"][key]["source_sha"][:12],
                                   result=result["result"],
                                   tests_passed=result.get("test_passed",0)))
        counts = {}
        for rec in state["jobs"].values():
            status = rec.get("result", "UNKNOWN")
            counts[status] = counts.get(status, 0)+1
        return {
            "mode": "CONCURRENT_DEVICE_FREE_RESEARCH_SWEEP",
            "events": sorted(events, key=lambda x:x["issue"]),
            "totals": counts, "source_jobs": len(c["jobs"]),
            "hardware_operations": 0, "rf_transmissions": 0
        }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once", action="store_true")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--config", type=Path, default=CONFIG)
    p.add_argument("--state", type=Path, default=STATE)
    p.add_argument("--max-per-cycle", type=int)
    args = p.parse_args(argv)
    if args.preview == args.once:
        p.error("specify exactly one of --preview or --once")
    try:
        if args.preview:
            c = config_load(args.config)
            current = load_state(args.state)["jobs"]
            result = {"mode": "READ_ONLY_OFFLINE_SWEEP_PREVIEW", "jobs": [
                {"issue": int(n), "ref":j["ref"], "tests":j["tests"],
                 "already_attempted":any(k.startswith(f"issue-{n}:") for k in current)}
                for n,j in c["jobs"].items()
            ]}
        else:
            result=one_cycle(args.config,args.state,max_per_cycle=args.max_per_cycle)
        print(json.dumps(result, indent=2))
        return 0
    except (Refused,ValueError,OSError,subprocess.SubprocessError,
            json.JSONDecodeError) as exc:
        print(f"OFFLINE SWEEP REFUSED: {exc}",file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
