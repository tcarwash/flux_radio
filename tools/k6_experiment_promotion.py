#!/usr/bin/env python3
"""Candidate-to-reviewed-physical-manifest promotion; NEVER itself touches radio.

The *only* inputs are verified local builder receipts and administrator-owned
recipes. Untrusted GitHub text never defines scripts or device operations.
Registers a frozen one-shot manifest for independent Muse review. A separate
grant stage, after that APPROVED review, enables the existing hardware
autopilot; it never executes device commands from this module.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timedelta,timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import k6_experiment_autopilot as journal
import k6_experiment_batch as batch
import k6_experiment_builder as builder
import k6_experiment_dispatch as dispatch
import k6_experiment_hardware as hardware

STATE_ROOT=Path.home()/".local/state/flux-experiment-autopilot"
GENERATED=STATE_ROOT/"hardware-generated.json"
PERMISSIONS=STATE_ROOT/"hardware-generated-standing.json"
MANIFEST_DIR=STATE_ROOT/"generated-manifests"
APPROVAL_POLICY=builder.ROOT/"config/k6-autonomous-radio-policy.json"
HARDWARE=builder.ROOT/"config/k6-experiment-hardware.json"
STANDING=builder.ROOT/"config/k6-experiment-hardware-standing.json"
SESSION_PROBE=builder.ROOT/"tools/k6_experiment_bench_probe.py"


class Refused(RuntimeError):pass


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,tmp=tempfile.mkstemp(prefix=".grant-",dir=path.parent)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,"w") as f:
            json.dump(data,f,indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def policy(path=APPROVAL_POLICY):
    p=json.loads(Path(path).read_text())
    if set(p)!={"schema","enabled","operator","dummy_load","expected_build",
               "exact_api","permitted_kinds","max_sessions_per_day","rf_session_limit_ms"}:
        raise Refused("invalid autonomous radio policy")
    if p["schema"]!=1 or p["operator"]!="AG7SU" or p["dummy_load"] is not True:
        raise Refused("operator/dummy-load ownership not confirmed")
    if not re.fullmatch(r"[a-f0-9]{16}",p["expected_build"]):
        raise Refused("invalid pinned firmware build")
    if type(p["exact_api"]) is not int or p["exact_api"]!=29:
        raise Refused("installed API not separately qualified for automatic approval")
    if p["permitted_kinds"]!=["RX_ONLY","BOUNDED_TX"]:
        raise Refused("physical scope outside approved RX-only/finite-TX")
    if not 1<=p["max_sessions_per_day"]<=16:
        raise Refused("excessive daily physical test allowance")
    if not 500<=p["rf_session_limit_ms"]<=1900:
        raise Refused("unsafe RF watchdog ceiling")
    return p


def ensure_generated():
    if not GENERATED.exists():
        atomic(GENERATED,hardware.load_config(HARDWARE))
    if not PERMISSIONS.exists():
        from k6_experiment_hardware_autopilot import standing_policy
        atomic(PERMISSIONS,standing_policy(STANDING))
    return hardware.load_config(GENERATED)


def ranked(owner,issue):
    order=dispatch.check_github(owner,[])
    return issue in order


def ensure_ranked(reg,issue,*,write=False):
    if ranked(reg,issue):return "ALREADY_RANKED"
    if not write:raise Refused(f"#{issue} missing coordination priority")
    # Only a trusted operator's Github issue can become eligible.
    i=dispatch.gh("issue","view",str(issue),"-R",reg["repo"],
                  "--json","author,number,state,title")
    if (i.get("author") or {}).get("login")!=reg["trusted_author"] or i.get("state")!="OPEN":
        raise Refused("only a trusted owner's open experiment can be enrolled")
    q=dispatch.gh("issue","view",str(reg["queue_issue"]),"-R",reg["repo"],
                  "--json","body,author,state")
    if (q.get("author") or {}).get("login")!=reg["trusted_author"] or q.get("state")!="OPEN":
        raise Refused("untrusted coordination issue")
    before=q["body"]
    sep="\n## Autonomous reviewed hardware candidate enrollments\n"
    if sep not in before:
        appendix=sep+"\n| Source | Stage | Review status |\n| --- | --- | --- |\n"
        revised=before.rstrip()+"\n"+appendix
    else:
        revised=before
    row=(f"| [#{issue} hardware candidate](https://github.com/{reg['repo']}/issues/{issue}) "
         "| Candidate prepared | Independent review and exact one-shot grant required |")
    if row not in revised:revised+="\n"+row+"\n"
    # Re-read before replacing the coordination body; concurrent changes
    # must not be silently overwritten. Any race after read still requires
    # a fresh authorization/claim downstream.
    confirm=dispatch.gh("issue","view",str(reg["queue_issue"]),"-R",reg["repo"],
                        "--json","body,author,state")
    if confirm.get("body")!=before:raise Refused("coordination body changed concurrently")
    with tempfile.NamedTemporaryFile(mode="w",prefix="flux-hardware-rank-",delete=True) as f:
        os.chmod(f.name,0o600)
        f.write(revised)
        f.flush()
        subprocess.run(["gh","issue","edit",str(reg["queue_issue"]),"-R",reg["repo"],
                        "--body-file",f.name],check=True,timeout=45,
                       stdout=subprocess.DEVNULL)
    if not ranked(reg,issue):
        raise Refused("priority entry not visible after enrollment")
    return "ENROLLED"


def entrypoint_for(n,recipe):
    scripts=[x for x in recipe["allowed_paths"] if x.startswith("tools/") and x.endswith("_bench.py")]
    if len(scripts)!=1:raise Refused(f"#{n}: expected exactly one bench entrypoint")
    return scripts[0]


def module_level(worktree,recipe):
    sources=[x for x in recipe["allowed_paths"] if x.startswith("c/k6/mod/")]
    for rel in sources:
        p=worktree/rel
        if p.is_file():
            m=re.search(r"#define\s+K6_MODULE_LEVEL\s+(\d+)",p.read_text())
            if m and 1<=int(m.group(1))<=29:
                return int(m.group(1))
    raise Refused("cannot establish physical module API level")


def generate(n,recipe,record,p):
    worktree=Path(record["worktree"])
    if not worktree.is_dir():raise Refused("candidate worktree missing")
    # A delegate may edit only the allowed files; no symlinks or manual drift.
    if record.get("frozen_commit"):
        actual=builder.run("git","-C",str(worktree),"rev-parse","HEAD")
        if actual!=record["frozen_commit"]:
            raise Refused("frozen candidate commit drift")
        if builder.run("git","-C",str(worktree),"status","--porcelain"):
            raise Refused("frozen candidate worktree is dirty")
        files=record["changed_files"]
        if not set(files).issubset(set(recipe["allowed_paths"])):
            raise Refused("candidate changed files outside reviewed recipe")
    else:
        files=builder.candidate_files(worktree,set(recipe["allowed_paths"]))
    if set(files)!=set(record["changed_files"]):
        raise Refused("candidate source differs from tested files")
    for pin in record["pins"]:
        f=worktree/pin["path"]
        if sha(f)!=pin["sha256"]:raise Refused("candidate source changed since tests")
    if not record.get("tests_log_sha256"):
        raise Refused("no validated device-free test evidence")
    script=entrypoint_for(n,recipe)
    source=worktree/script
    if script not in files:raise Refused("physical entrypoint was not created or reviewed")
    text=source.read_text()
    for expected in ("--preflight-only","--verify-only","--run","--prefix",
                     "--dummy-load-confirmed","--operator-lease-confirmed",
                     "--queue-lease-acknowledged"):
        if expected not in text:raise Refused(f"bench script lacks stage {expected}")
    if recipe["kind"]=="BOUNDED_TX":
        # A spectral-power peak is NOT an RF public-sync correlation.
        # The first #26 delegate mapped PSD peaks to invented 0.99
        # correlation, which could score broadband noise as valid FSK.
        if re.search(r"corrs\.append\(MIN_SYNC_CORR\s*\+",text):
            raise Refused("RF scorer fabricates synchronization from PSD peak")
        if "--max-tx-ms" not in text or not any(word in text for word in (
                "watchdog","WATCHDOG","TX_TIMEOUT")):
            raise Refused("bounded TX watchdog flag not implemented in bench entrypoint")
        if "--frequency-hz" not in text:
            raise Refused("bounded TX frequency pin not implemented in bench entrypoint")
    if not source.is_file() or source.is_symlink():
        raise Refused("invalid bench script")
    output=Path.home()/f"flux-bench/batches/auto-{n}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    session_id=output.name
    timeout=70
    script_abs=str(source)
    probe=str(SESSION_PROBE)
    base=["/usr/bin/python3",probe,"--expected-build",p["expected_build"],
          "--exact-api",str(p["exact_api"]),"--include-slots"]
    before=output/"session-before.json"
    preflight=base+["--output",str(before)]
    after=base+["--baseline",str(before)]
    steps={
        "session_preflight":{"argv":preflight,"timeout_seconds":timeout},
        "session_restore":{"argv":after,"timeout_seconds":timeout},
        "session_verify":{"argv":after,"timeout_seconds":timeout},
    }
    prefix=output/"auto-bench"
    run_args=["/usr/bin/python3",script_abs,"--run","--prefix",str(prefix),
              "--dummy-load-confirmed","--operator-lease-confirmed",
              "--queue-lease-acknowledged"]
    if recipe["kind"]=="BOUNDED_TX":
        run_args.extend(["--max-tx-ms",str(p["rf_session_limit_ms"]),
                         "--frequency-hz","433500000"])
    step={
        "preflight":{"argv":["/usr/bin/python3",script_abs,"--preflight-only"],
                     "timeout_seconds":45},
        "run":{"argv":run_args,"timeout_seconds":120},
        "restore":{"argv":["/usr/bin/python3",script_abs,"--verify-only",
                           "--prefix",str(prefix)],"timeout_seconds":70},
        "verify":{"argv":["/usr/bin/python3",script_abs,"--verify-only",
                          "--prefix",str(prefix)],"timeout_seconds":70},
    }
    deps=["tools/k6_install.py","tools/k6_live.py","tools/k6_port_lock.py",
          "tools/k6_user_store_migrate.py","modem/k6mod.py","c/k6/k6_mod.h",
          "c/k6/mod/module.ld"]
    candidate_pins=list({str(worktree/x) for x in record["changed_files"]}|{
        str(worktree/x) for x in deps if (worktree/x).is_file()})
    session_deps=["tools/k6_experiment_bench_probe.py","tools/k6_live.py",
                  "tools/k6_install.py","tools/k6_port_lock.py",
                  "tools/k6_user_store_migrate.py"]
    session_pins=[{"path":str(builder.ROOT/x),"sha256":sha(builder.ROOT/x)}
                  for x in session_deps]
    jobs=[{
        "id":f"auto-{n}-bounded-one-shot",
        "issue_url":f"https://github.com/tcarwash/flux_radio/issues/{n}",
        "minimum_module_api":module_level(worktree,recipe),
        "cwd":str(worktree),
        "pins":[{"path":path,"sha256":sha(path)} for path in sorted(candidate_pins)],
        **step,
    }]
    m={
        "version":1,"session_id":session_id,"port":"/dev/ttyUSB0",
        "queue_issue_url":"https://github.com/tcarwash/flux_radio/issues/11",
        "lease_expires_utc":(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat(),
        "session_pins":session_pins,
        **steps,"jobs":jobs
    }
    batch.validate_manifest(m)
    batch.check_command_pins(m)
    batch._check_pins({"id":"session","pins":m["session_pins"]})
    batch._check_pins(jobs[0])
    api,_=batch.read_api_config(batch.API_CONFIG)
    if batch.check_api_compatibility(m,api):
        raise Refused("current module API not qualified for generated candidate")
    return m


def register_once(policy_path=APPROVAL_POLICY,builder_state=builder.STATE,
                  hardware_config=GENERATED):
    """Never clobber a concurrent candidate writer's durable journal."""
    builder_state=Path(builder_state)
    builder_state.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    with builder_state.with_suffix(".lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            return {"mode":"PROMOTION_BUSY_SKIP","events":[],
                    "radio_access":False}
        return _register_locked(policy_path,builder_state,hardware_config)


def _register_locked(policy_path,builder_state,hardware_config):
    p=policy(policy_path)
    if not p["enabled"]:return {"mode":"AUTO_HARDWARE_PREPARATION_DISABLED","events":[]}
    reg=ensure_generated()
    b=journal.state_load(builder_state)
    recipes=builder.config_load()["recipes"]
    events=[]
    for nstr,recipe in recipes.items():
        record=b["jobs"].get(f"issue-{nstr}",{})
        if record.get("state")!="TESTED_NEEDS_INDEPENDENT_REVIEW":
            continue
        n=int(nstr)
        old=reg["manifests"].get(nstr)
        if old:
            # Old exact manifest may be superseded ONLY after the previous
            # physical attempt has reached a terminal state and source pins
            # changed. The old journal/manifest/ACK remain immutable.
            prev=journal.state_load(STATE_ROOT/"hardware.json")["jobs"].get(
                f"issue-{n}:{old['sha256']}",{})
            if prev.get("state") not in ("BLOCKED_NO_AUTO_RETRY","PASS"):
                continue
            old_manifest=json.loads(Path(old["path"]).read_text())
            oldpins={x["path"]:x["sha256"] for x in old_manifest["jobs"][0]["pins"]}
            if all(oldpins.get(str(Path(record["worktree"])/x["path"]))==x["sha256"]
                   for x in record["pins"]):
                continue
        # A bad *offline candidate* is not a controller/service failure.
        # Isolate it and advance other validated hardware candidates.
        # This never grants radio access, and a failed generated script is
        # never enrolled in the trusted queue.
        try:
            m=generate(n,recipe,record,p)
        except (Refused,batch.Refused,ValueError,OSError) as exc:
            record["state"]="BLOCKED_CANDIDATE_NEEDS_REPAIR"
            record["error"]=f"{type(exc).__name__}: {exc}"[:400]
            journal.state_write(builder_state,b)
            events.append({"issue":n,"state":"BLOCKED_CANDIDATE_NEEDS_REPAIR",
                           "reason":str(exc)[:150]})
            continue
        if not ranked(reg,n):
            ensure_ranked(reg,n,write=True)
            events.append({"issue":n,"state":"ENROLLED_IN_TRUSTED_QUEUE"})
        path=MANIFEST_DIR/(m["session_id"]+".json")
        if path.exists():raise Refused("physical manifest ID already used")
        atomic(path,m)
        digest=sha(path)
        # Register exact locally constructed manifest; delegated reviewer
        # must APPROVE this frozen hash before any grant or bench access.
        reg["manifests"][nstr]={"path":str(path),"sha256":digest}
        atomic(hardware_config,reg)
        record["state"]="MANIFEST_REGISTERED_FOR_REVIEW"
        record["manifest_sha256"]=digest
        record["manifest_path"]=str(path)
        journal.state_write(builder_state,b)
        events.append({"issue":n,"state":"MANIFEST_REGISTERED_FOR_REVIEW",
                       "digest":digest})
    return {"mode":"PINNED_HARDWARE_CANDIDATES","events":events,
            "registered":len(reg["manifests"]),"radio_access":False}


def grant_once(policy_path=APPROVAL_POLICY,builder_state=builder.STATE,
               hardware_config=GENERATED,standing_config=PERMISSIONS,
               review_state_path=None,hardware_state_path=None):
    from k6_experiment_hardware_autopilot import standing_policy
    from k6_experiment_review import fingerprint,result_status,DEFAULT_STATE
    p=policy(policy_path)
    if not p["enabled"]:return {"mode":"AUTO_HARDWARE_GRANT_DISABLED","events":[]}
    reg=ensure_generated()
    standing=standing_policy(standing_config)
    reviews=journal.state_load(review_state_path or DEFAULT_STATE)
    hardware_attempts=journal.state_load(
        hardware_state_path or STATE_ROOT/"hardware.json")["jobs"]
    today=datetime.now(timezone.utc).date().isoformat()
    used=sum(1 for x in hardware_attempts.values()
             if x.get("when","").startswith(today))
    events=[]
    builder_jobs=journal.state_load(builder_state)["jobs"]
    for nstr,recipe in builder.config_load()["recipes"].items():
        if used>=p["max_sessions_per_day"]:break
        record=builder_jobs.get(f"issue-{nstr}",{})
        if record.get("state")!="MANIFEST_REGISTERED_FOR_REVIEW":continue
        entry=reg["manifests"].get(nstr)
        if not entry or entry.get("sha256")!=record.get("manifest_sha256"):
            raise Refused("builder manifest registry drift")
        n=int(nstr)
        old_grant=standing["approved"].get(nstr)
        if old_grant:
            if old_grant["manifest_sha256"]==entry["sha256"]:
                continue
            old_attempt=hardware_attempts.get(
                f"issue-{n}:{old_grant['manifest_sha256']}",{})
            if old_attempt.get("state") not in ("PASS","BLOCKED_NO_AUTO_RETRY"):
                raise Refused("old hardware grant still active; no source rotation")
        m,manifest_sha,raw=hardware.load_registered_manifest(reg,n)
        api,api_raw=batch.read_api_config(batch.API_CONFIG)
        key=fingerprint(n,manifest_sha,api_raw)
        decision=reviews["jobs"].get(key,{})
        if decision.get("state")!="APPROVED" or not decision.get("job_id"):
            events.append({"issue":n,"state":"WAITING_INDEPENDENT_HARDWARE_REVIEW"})
            continue
        if result_status(decision["job_id"])!="APPROVED":
            events.append({"issue":n,"state":"REVIEW_NOT_APPROVED"})
            continue
        if not ranked(reg,n):raise Refused("candidate no longer ranked in trusted queue")
        if recipe["kind"] not in p["permitted_kinds"]:
            raise Refused("RF permission outside operator scope")
        if datetime.now(timezone.utc)>=datetime.fromisoformat(
                m["lease_expires_utc"].replace("Z","+00:00")):
            events.append({"issue":n,"state":"MANIFEST_EXPIRED"})
            continue
        # Strong exact-digest standing permit, not broad GitHub code execution.
        standing["approved"][nstr]={
          "manifest_sha256":manifest_sha,
          "expected_build":p["expected_build"],
          "exact_api":p["exact_api"],
          "expires_utc":m["lease_expires_utc"],
          "one_shot":True,
          "dummy_load_confirmed":True,
          "no_cwid_for_dummy_load":True
        }
        atomic(standing_config,standing)
        used+=1
        events.append({"issue":n,"state":"ONE_SHOT_PERMIT_ISSUED",
                       "digest":manifest_sha,"profile":recipe["kind"]})
    return {"mode":"EXACT_REVIEWED_ONE_SHOT_GRANTS",
            "events":events,"operator":"AG7SU",
            "dummy_load":True,"implied_further_permissions":False}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--once",action="store_true")
    p.add_argument("--grants",action="store_true",
                   help="after exact independent review, issue bounded one-shot permits")
    p.add_argument("--policy",type=Path,default=APPROVAL_POLICY)
    args=p.parse_args(argv)
    if not args.once:p.error("only one deterministic iteration allowed")
    try:
        result=(grant_once(args.policy) if args.grants else register_once(args.policy))
        print(json.dumps(result,indent=2))
        return 0
    except (Refused,ValueError,KeyError,OSError,
            batch.Refused,dispatch.Refused,subprocess.SubprocessError) as exc:
        print(f"PHYSICAL PREPARATION REFUSED: {exc}",file=sys.stderr)
        return 2


if __name__=="__main__":raise SystemExit(main())
