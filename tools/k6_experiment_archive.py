#!/usr/bin/env python3
"""Search and close historical Flux experiments after verified resolution or migration.

Offline fixtures cannot automatically close issues: a green fixture can leave
physical qualification and the issue's stated acceptance criteria incomplete.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import k6_offline_results as ledger

REPO = "tcarwash/flux_radio"
BASE = "https://github.com/" + REPO + "/issues/"
STATE = Path.home() / ".local/state/flux-experiment-autopilot"


class Refused(Exception):
    pass


def gh(*args):
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True,
                              check=True, timeout=45).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused(f"GitHub operation failed: {exc}") from exc


def get_issue(n):
    issue = json.loads(gh("api", f"repos/{REPO}/issues/{n}"))
    if issue.get("html_url") != BASE + str(n) or issue.get("pull_request"):
        raise Refused("not a Flux issue")
    return issue


def active_candidate(n, state_dir=STATE):
    """Never close a live hardware-candidate owner thread."""
    path = state_dir / "builder.json"
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot read builder state: {exc}") from exc
    return any(k == f"issue-{n}" or k.startswith(f"issue-{n}:")
               for k in d.get("jobs", {}))


def close_plan(issue, disposition, summary, evidence, *, scope="offline",
               restoration_evidence=None, acceptance_reviewed=False, state_dir=STATE):
    n = issue["number"]
    title = issue.get("title", "")
    if issue.get("state") != "open" or not title.startswith("[Flux RF]"):
        raise Refused("only open Flux research issues can use this archive path")
    if n == 11:
        raise Refused("cannot close shared bench coordination issue")
    if scope == "offline" and "[OFFLINE]" not in title:
        raise Refused("offline closeout requires OFFLINE research")
    if scope == "hardware":
        if disposition != "completed" or not ("HARDWARE" in title or "[OFFLINE]" in title):
            raise Refused("hardware closeout requires a completed RF experiment")
        if not acceptance_reviewed or not restoration_evidence:
            raise Refused("hardware closeout requires reviewed acceptance and independent restoration evidence")
    if active_candidate(n, state_dir):
        raise Refused(f"issue #{n} owns active or historical physical candidate work; keep open")
    if disposition not in ("completed", "migrated"):
        raise Refused("invalid disposition")
    if not summary or len(summary.strip()) < 15:
        raise Refused("include a substantive verified conclusion or migration handoff")
    if not evidence or any(not re.match(
            r"^https://github[.]com/tcarwash/flux_radio/(issues/[0-9]+|commit/[a-f0-9]{7,40}|blob/|tree/)", e)
            for e in evidence):
        raise Refused("supply source links from the Flux repository")
    if restoration_evidence and not re.match(
            r"^https://github[.]com/tcarwash/flux_radio/issues/[0-9]+(?:#issuecomment-[0-9]+)?$", restoration_evidence):
        raise Refused("restoration evidence must be a repository issue or comment link")
    return {"issue": n, "title": title, "url": BASE + str(n), "disposition": disposition,
            "scope": scope, "summary": summary.strip(), "evidence": evidence,
            "restoration_evidence": restoration_evidence,
            "ledger_id": f"legacy-issue-{n}" if disposition == "migrated" else None}


def apply_close(plan):
    n = plan["issue"]
    actual = get_issue(n)
    if actual.get("state") != "open" or actual.get("title") != plan["title"]:
        raise Refused("issue changed before closeout")
    if active_candidate(n):
        raise Refused("hardware candidate appeared; keep issue open")
    rid = plan["ledger_id"]
    if rid:
        with ledger.database() as con:
            row = con.execute("SELECT id FROM results WHERE id=?", (rid,)).fetchone()
            if not row:
                ledger.register(con, rid=rid, name=plan["title"],
                                owner="legacy-offline-migration", ref=plan["url"],
                                summary=plan["summary"])
    note = (
        f"EXPERIMENT ARCHIVE | {plan['disposition'].upper()} | {plan['scope'].upper()}\n\n"
        f"{plan['summary']}\n\n"
        "Evidence:\n" + "".join("- " + x + "\n" for x in plan["evidence"]) +
        (f"- Independent restoration: {plan['restoration_evidence']}\n"
         if plan.get("restoration_evidence") else "") +
        "\nOnly the explicitly reviewed scope is resolved or migrated. "
        "A software PASS alone never establishes RF qualification. Archived "
        "issues remain searchable by GitHub and offline records by the shared host ledger.\n"
    )
    if rid:
        note += (f"\nUnfinished research moved into shared offline result record "
                 f"{rid} (REGISTERED, not PASS). New offline agent work belongs "
                 "in the shared ledger, not a new GitHub issue.\n")
    gh("issue", "comment", str(n), "--repo", REPO, "--body", note)
    reason = "completed" if plan["disposition"] == "completed" else "not planned"
    gh("issue", "close", str(n), "--repo", REPO, "--reason", reason)
    if get_issue(n).get("state") != "closed":
        raise Refused("GitHub did not confirm issue closeout")
    return {"state": "CLOSED", "url": plan["url"], "reason": reason,
            "ledger_id": rid}


def search(query, state, limit):
    if not 1 <= limit <= 500:
        raise Refused("limit must be 1..500")
    argv = ["issue", "list", "--repo", REPO, "--state", state,
            "--limit", str(limit), "--json",
            "number,title,state,url,closedAt,updatedAt"]
    if query:
        argv += ["--search", query]
    rows = json.loads(gh(*argv))
    return {"query": query, "state": state, "count": len(rows), "issues": rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("search")
    a.add_argument("--state", choices=("open", "closed", "all"), default="closed")
    a.add_argument("--query", default="")
    a.add_argument("--limit", type=int, default=100)
    a = sub.add_parser("close")
    a.add_argument("--issue", type=int, required=True)
    a.add_argument("--disposition", choices=("completed", "migrated"), required=True)
    a.add_argument("--scope", choices=("offline", "hardware"), default="offline")
    a.add_argument("--restoration-evidence", help="independent whole-radio postflight issue/comment URL")
    a.add_argument("--acceptance-reviewed", action="store_true",
                   help="explicit verification that the issue's full physical acceptance criteria are met")
    a.add_argument("--summary", required=True)
    a.add_argument("--evidence", action="append", required=True)
    a.add_argument("--apply", action="store_true",
                   help="default is dry-run; apply publishes summary and closes issue")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "search":
            result = search(a.query, a.state, a.limit)
        else:
            plan = close_plan(get_issue(a.issue), a.disposition, a.summary, a.evidence,
                              scope=a.scope, restoration_evidence=a.restoration_evidence,
                              acceptance_reviewed=a.acceptance_reviewed)
            result = apply_close(plan) if a.apply else {"state": "DRY_RUN", "plan": plan}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, KeyError, OSError, Refused, json.JSONDecodeError) as exc:
        print(f"FLUX ISSUE ARCHIVE REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
