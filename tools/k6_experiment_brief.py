#!/usr/bin/env python3
"""Turn trusted owner-authored Flux experiment briefs into device-free recipes.

An issue is a *goal*, not an executable command or RF permit. Everything
derived from GitHub is restricted to inert text passed to an isolated writer;
the actual physical path must still compile/test, freeze, review and qualify.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

REPO = "tcarwash/flux_radio"
AUTHOR = "tcarwash"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path.home()/".local/state/flux-experiment-autopilot/brief-recipes.json"
EXP_RE = re.compile(r"^\[Flux RF\]\[(EXPERIMENT|HARDWARE)\](?:\s|$)",re.I)
LABEL = "experiment:brief"


class Refused(RuntimeError): pass


def eligible(issue):
    if (not isinstance(issue,dict) or
        (issue.get("author") or {}).get("login")!=AUTHOR or
        issue.get("state")!="OPEN" or
        type(issue.get("number")) is not int or issue["number"]<=11):
        return False
    title=issue.get("title")
    if not isinstance(title,str) or len(title)>300:
        return False
    labels=issue.get("labels",[])
    names={str(x.get("name")) for x in labels if isinstance(x,dict)}
    return bool(EXP_RE.match(title) or
                (title.startswith("[Flux RF]") and LABEL in names))


def field(body,*headings):
    for name in headings:
        # One-line Markdown, in paragraphs without requiring a form.
        m=re.search(r"(?im)^\s*(?:#{1,4}\s*)?"+re.escape(name)+
                    r"\s*:\s*(.+?)\s*$",body)
        if m: return m.group(1).strip()
    return None


def make_brief(issue):
    if not eligible(issue):
        raise Refused("issue is not an open, owner-authored Flux experiment brief")
    number=issue["number"]
    body=issue.get("body","")
    if not isinstance(body,str) or not 40<=len(body)<=12000:
        raise Refused("brief needs at least a short paragraph (40..12000 characters)")
    title=issue["title"]
    what=field(body,"Goal","What","Objective") or title
    how=field(body,"Method","How","Approach") or "Agent designs a bounded repeatable test from the supplied brief."
    expected=field(body,"Expected","Expected results","Success","Expected observation") or (
        "Agent must write a measurable expected observation and distinguish negative/inconclusive results."
    )
    api=field(body,"Firmware API","API","Module API")
    if api:
        m=re.search(r"\b(\d{1,3})\b",api)
        if not m or not 1<=int(m.group(1))<=29:
            raise Refused("requested API version is outside installed API support (1..29)")
        api=int(m.group(1))
    else:
        api=None
    placement_text=field(body,"Placement","Location","Build target") or ""
    on_flash=bool(re.search(r"(?i)\b(on[- ]flash|flashed|flash image|firmware image|built-in firmware)\b",placement_text))
    placement="FLASH_FIRMWARE" if on_flash else "LOADABLE_MODULE"
    kind_hint=field(body,"Type","Radio action","RF") or title+" "+what+" "+how
    tx=bool(re.search(r"(?i)\b(transmit|transmitting|transmission|TX|FSK bursts?|keyed RF|modulation)\b",kind_hint))
    kind="BOUNDED_TX" if tx else "RX_ONLY"
    return {"issue":number,"url":f"https://github.com/{REPO}/issues/{number}",
            "title":title,"goal":what[:1800],"method":how[:1800],
            "expected":expected[:1800],"body":body[:9500],"api":api,
            "placement":placement,"kind":kind,
            "state":"FIRMWARE_REVIEW_REQUIRED" if on_flash else "CANDIDATE_READY"}


def make_recipe(brief):
    n=brief["issue"]
    if brief["placement"]!="LOADABLE_MODULE":
        raise Refused("on-flash firmware requires separate review before RF promotion")
    source=f"c/k6/mod/k6_exp_{n}_probe.c"
    script=f"tools/k6_exp_{n}_bench.py"
    tests=f"tests/test_k6_exp_{n}_bench.py"
    docs=f"docs/k6-exp-{n}-brief.md"
    objective=(
        f"Owner-authored Flux RF brief #{n} (not a prequalified experiment).\n"
        f"GOAL: {brief['goal']}\n"
        f"METHOD: {brief['method']}\n"
        f"EXPECTED: {brief['expected']}\n"
        f"FIRMWARE API: {brief['api'] if brief['api'] is not None else 'not specified; determine from installed target (max 29)'}\n"
        f"PLACEMENT: {brief['placement']}; class {brief['kind']}.\n"
        f"ORIGINAL BRIEF: {brief['body']}\n"
        "Design and implement a real offline-testable experiment (not a paper proposal). "
        "Compile Cortex-M0 firmware in an inert-by-default module, create the bench harness "
        "and real device-free tests, then explicitly report gaps. The sandbox has no radio. "
        "The supervisor must independently validate the frozen result before any physical run. "
        "If the proposed measurement needs no modem decoder, measure only what the claim requires."
    )
    return {"base_ref":"origin/main",
            "kind":brief["kind"],
            "allowed_paths":[source,script,tests,docs],
            "tests":[tests],
            "objective":objective[:12000]}


def discover(fetch=None):
    if fetch is None:
        def fetch():
            return json.loads(subprocess.run(
                ["gh","issue","list","-R",REPO,"--state","open","--limit","250",
                 "--json","number,title,body,state,author,labels"],
                capture_output=True,text=True,check=True,timeout=35).stdout)
    rows=fetch()
    if not isinstance(rows,list) or len(rows)>=250:
        raise Refused("incomplete GitHub issue discovery")
    briefs={}
    recipes={}
    for issue in rows:
        if not eligible(issue): continue
        try: b=make_brief(issue)
        except Refused as exc:
            # Preserve the diagnostic, never run unvalidated briefs.
            briefs[str(issue["number"])]={"issue":issue["number"],"title":issue["title"],
                                           "state":"BRIEF_NEEDS_DETAIL","reason":str(exc)}
            continue
        briefs[str(b["issue"])]=b
        if b["state"]=="CANDIDATE_READY":
            recipes[str(b["issue"])]=make_recipe(b)
    return {"schema":1,"repo":REPO,"trusted_author":AUTHOR,
            "generated_utc":datetime.now(timezone.utc).isoformat(),
            "briefs":briefs,"recipes":recipes}


def sync(output=DEFAULT_OUT,fetch=None):
    data=discover(fetch)
    output=Path(output)
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(prefix=".brief-",dir=output.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w") as f:
            json.dump(data,f,indent=2,sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,output)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return {"registered_briefs":len(data["briefs"]),
            "ready_for_device_free_builder":len(data["recipes"]),
            "requires_firmware_review":sum(x.get("state")=="FIRMWARE_REVIEW_REQUIRED"
                                             for x in data["briefs"].values()),
            "state":str(output),"radio_operations":0}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sync",action="store_true")
    p.add_argument("--status",action="store_true")
    args=p.parse_args(argv)
    if args.sync==args.status:p.error("choose --sync or --status")
    try:
        print(json.dumps(sync() if args.sync else json.loads(DEFAULT_OUT.read_text()),
                         indent=2,sort_keys=True))
        return 0
    except (OSError,ValueError,subprocess.SubprocessError,Refused) as exc:
        print("BRIEF INTAKE REFUSED: "+str(exc),file=sys.stderr)
        return 2


if __name__=="__main__":
    raise SystemExit(main())
