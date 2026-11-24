#!/usr/bin/env python3
"""Read-only GitHub intake. Issue text is data, never executable commands."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import k6_experiment_batch as batch

ROOT = Path(__file__).resolve().parents[1]

def gh(*args):
    return json.loads(subprocess.run(["gh", *args], check=True,
                      capture_output=True, text=True, timeout=45).stdout)

def plan(queue, issues, registry, api, repo, owner):
    prefix = f"https://github.com/{repo}/issues/"
    by_number = {i["number"]: i for i in issues}
    ranked = []
    for line in queue["body"].splitlines():
        if not line.startswith("|"):
            continue
        for display, destination in re.findall(
                r"\[#(\d+)[^\]]*\]\(" + re.escape(prefix) + r"(\d+)\)", line):
            if display == destination and int(display) not in ranked:
                ranked.append(int(display))
    out = []
    for n in ranked + sorted(set(by_number) - set(ranked)):
        i = by_number.get(n)
        if not i or i["state"] != "OPEN":
            continue
        entry = registry.get(str(n))
        reason = None
        if i.get("author", {}).get("login") != owner:
            reason = "untrusted issue author"
        elif n not in ranked:
            reason = "not ranked in coordination issue"
        elif not entry:
            reason = "needs locally reviewed harness and API qualification"
        elif entry.get("kind") == "OFFLINE":
            reason = "locally pinned OFFLINE harness; ready for trusted dispatcher"
        elif entry.get("validated_module_api") != api:
            reason = "needs retargeting or requalification"
        else:
            reason = "locally registered; requires fresh evidence and bench claim review"
        out.append({"number": n, "url": prefix + str(n), "title": i["title"],
                    "status": reason, "ranked": n in ranked})
    return out

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", type=Path, default=ROOT / "config/k6-experiment-queue.json")
    p.add_argument("--api-config", type=Path, default=batch.API_CONFIG)
    a = p.parse_args()
    cfg = json.loads(a.registry.read_text())
    api, _ = batch.read_api_config(a.api_config)
    repo, owner = cfg["repo"], cfg["trusted_author"]
    q = gh("issue", "view", str(cfg["queue_issue"]), "-R", repo, "--json", "body,author")
    if q.get("author", {}).get("login") != owner:
        raise SystemExit("untrusted coordination issue author")
    issues = gh("issue", "list", "-R", repo, "--state", "open", "--limit", "1000",
                "--json", "number,title,state,author")
    if len(issues) >= 1000:
        raise SystemExit("incomplete issue listing")
    issues = [i for i in issues if i["number"] != cfg["queue_issue"]]
    print(json.dumps({"mode": "READ_ONLY", "target_module_api": api["target_module_api"],
        "experiments": plan(q, issues, cfg["experiments"], api["target_module_api"], repo, owner)}, indent=2))

if __name__ == "__main__":
    main()
