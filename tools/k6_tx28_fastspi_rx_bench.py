#!/usr/bin/env python3
"""Bounded, standalone, physical RX-only 10,000-readback-test candidate harness.

Issue #21 follow-up to the OFFLINE-qualified TX-28 fast-SPI probe
(c/k6/mod/tx28_fastspi_probe.c). This tool performs NO simulation and
claims NO physical results by itself: it is the single entry point the
supervising coordinator drives on the bench machine, where a separate
supervisor controls the radio (dummy load, operator lease, queue lease).

Hardware is NEVER touched during development or unit tests: every
serial/port/packet function is mocked there. Live radio contact happens
only inside `--run` / `--verify-only`, after explicit confirmations.

CLI:
  --preflight-only : NO radio mutation; deterministic validation of the
     local source, argv, selected ABI and expected build. Never opens a port.
  --run --dummy-load-confirmed --operator-lease-confirmed
        --queue-lease-acknowledged --prefix ABS_PATH :
     one finite full 10k readback experiment on the radio. Guards RX/TX off
     before and during, backs up VFO state and all packed module blobs,
     uses only temporary library slot 30 and restores its original state.
  --verify-only --prefix ABS_PATH : independently recheck no TX, no
     register/slot/VFO drift and hardware-ready results without modifying
     the radio.

Fail-closed rules (no auto retry anywhere):
  * The currently compiled 1412 B bench module performs 384 verified
    write+readback operations per invocation (6 ladders x 8 reps x
    8 patterns), NOT 10,000. `--run` therefore aborts BEFORE opening any
    port and records the precise blocker plus the required source change.
    Counts are never fabricated or extrapolated.
  * The hardware scorer accepts only an exact 10,000 readback success with
    preserved state and live-hardware provenance. Mock/fake data is never
    a physical PASS.
  * Every error/timeout/interrupt path restores state, leaves TX disabled
    and reports failure.

SPDX-License-Identifier: MIT
"""
import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time

EXPECTED_BUILD_ID = "339e08a2b77c7679"
EXPECTED_API = 29
REQUIRED_OPS = 10000
TEMP_SLOT = 30
LIB_SLOTS = 32
LIB_SLOT_BYTES = 0x4000
ERASED_SHA256 = hashlib.sha256(b"\xff" * LIB_SLOT_BYTES).hexdigest()
PROBE_SRC_REL = os.path.join("c", "k6", "mod", "tx28_fastspi_probe.c")
MOD_H_REL = os.path.join("c", "k6", "k6_mod.h")
RUN_TIMEOUT_S = 60.0

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_BLOCKED = 0, 1, 2, 3

FORBIDDEN_TOKENS = (
    "tx_on", "tx_pa", "tx_off", "fsk_tx_send", "afsk_tx_send",
    "ptt_hold", "ptt_burst", "kiss_mode", "serial_write",
)
REQUIRED_GATES = (
    "key_down", "power_switch_off", "REG_30", "R_PA",
    "saved_moder", "saved_5a", "clk_stop", "GPIOB_MODER",
)


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _int_define(text, name):
    m = re.search(r"#\s*define\s+%s\s+(\d+)" % re.escape(name), text)
    return int(m.group(1)) if m else None


def _defines_in_region(text):
    """Map of {NAME: int} for simple `#define NAME <int>` lines in text."""
    out = {}
    for m in re.finditer(r"#\s*define\s+([A-Za-z0-9_]+)\s+(\d+)", text):
        out[m.group(1)] = int(m.group(2))
    return out


def analyze_source(text):
    """Deterministic static analysis of the probe source (no hardware).

    Reports the DEFAULT bench path (ops_per_invocation, 384 baseline)
    separately from the compile-time TX28_FULL_BENCH variant
    (full_bench_ops, 10000). Conditional #if branches are parsed per
    region so the full-bench counts are never mistaken for the default
    build, nor vice versa.
    """
    level = _int_define(text, "K6_MODULE_LEVEL")
    inert_default = bool(re.search(r"#\s*define\s+TX28_BENCH_ENABLE\s+0\b", text))
    full_bench_default_off = bool(
        re.search(r"#\s*define\s+TX28_FULL_BENCH\s+0\b", text))
    bench_gate_exact = (
        bool(re.search(r"#\s*if\s+TX28_BENCH_ENABLE\b", text))
        and "return -9" in text)
    fb = re.search(
        r"#\s*if\s+TX28_FULL_BENCH\b(.*?)?#\s*else\b(.*?)#\s*endif",
        text, re.S)
    if fb:
        full_defs = _defines_in_region(fb.group(1))
        default_defs = _defines_in_region(fb.group(2))
    else:
        full_defs, default_defs = {}, _defines_in_region(text)
    ladders = default_defs.get("N_LADDERS", _int_define(text, "N_LADDERS"))
    reps = default_defs.get("REPS_PER_LADDER",
                            _int_define(text, "REPS_PER_LADDER"))
    pats = default_defs.get("N_PATTERNS", _int_define(text, "N_PATTERNS"))
    ops = None
    if ladders is not None and reps is not None and pats is not None:
        ops = ladders * reps * pats
    fl = full_defs.get("N_LADDERS")
    fr = full_defs.get("REPS_PER_LADDER")
    fp = full_defs.get("N_PATTERNS")
    full_ops = (fl * fr * fp) if (fl is not None and fr is not None
                                 and fp is not None) else None
    full_10k = full_ops == REQUIRED_OPS
    forbidden = sorted({t for t in FORBIDDEN_TOKENS if t in text})
    gates = sorted({g for g in REQUIRED_GATES if g in text})
    missing_gates = sorted({g for g in REQUIRED_GATES if g not in text})
    return {
        "level": level,
        "inert_default": inert_default,
        "ladders": ladders,
        "reps_per_ladder": reps,
        "patterns": pats,
        "ops_per_invocation": ops,
        "single_invocation_10k": ops == REQUIRED_OPS,
        "full_bench_default_off": full_bench_default_off,
        "full_bench_ladders": fl,
        "full_bench_reps_per_ladder": fr,
        "full_bench_patterns": fp,
        "full_bench_ops": full_ops,
        "full_bench_single_invocation_10k": full_10k,
        "full_bench_available": bool(full_bench_default_off and full_10k),
        "bench_gate_exact": bench_gate_exact,
        "forbidden_tokens_found": forbidden,
        "gates_present": gates,
        "gates_missing": missing_gates,
    }


def abi_from_header(text):
    abi = _int_define(text, "KM_ABI")
    level = _int_define(text, "KM_LEVEL")
    return abi, level


def required_change_text(analysis):
    ops = analysis.get("ops_per_invocation")
    return (
        "The bench-enabled probe performs %s verified write+readback "
        "operations per invocation (N_LADDERS x REPS_PER_LADDER x N_PATTERNS), "
        "not exactly 10000. A supervisor-approved C change is required before "
        "any physical 10k claim: extend c/k6/mod/tx28_fastspi_probe.c with a "
        "bounded RX-only readback loop totalling exactly 10000 verified "
        "readbacks in ONE invocation (e.g. parameterize the ladder/rep counts "
        "so their product is 10000), while keeping the inert default "
        "(TX28_BENCH_ENABLE=0 -> -9), API level 2, TX/PA/FSK refusal gates, "
        "explicit GPIOB_MODER/TIM17/REG_5A restoration and the key/power "
        "watchdog abort. Do not fabricate counts by looping invocations or "
        "scaling 384-invocation results." % (ops,)
    )


def cmd_preflight(root, prefix=None):
    """Deterministic local validation only. Never opens a port. Returns (rc, record)."""
    src_path = os.path.join(root, PROBE_SRC_REL)
    mod_h_path = os.path.join(root, MOD_H_REL)
    errors = []
    try:
        with open(src_path, "r", encoding="utf-8") as f:
            src_text = f.read()
    except OSError as exc:
        return EXIT_FAIL, {"ok": False, "errors": ["cannot read probe source: %s" % exc]}
    try:
        with open(mod_h_path, "r", encoding="utf-8") as f:
            mod_h_text = f.read()
    except OSError as exc:
        return EXIT_FAIL, {"ok": False, "errors": ["cannot read k6_mod.h: %s" % exc]}
    analysis = analyze_source(src_text)
    abi, header_level = abi_from_header(mod_h_text)
    if analysis["level"] != 2:
        errors.append("probe K6_MODULE_LEVEL is %r, want 2 (RX-only)" % (analysis["level"],))
    if not analysis["inert_default"]:
        errors.append("probe default build is not inert (TX28_BENCH_ENABLE=0 missing)")
    if analysis["forbidden_tokens_found"]:
        errors.append("probe source mentions TX-capable APIs: %s"
                      % ",".join(analysis["forbidden_tokens_found"]))
    if analysis["gates_missing"]:
        errors.append("probe source lacks safety/restore gates: %s"
                      % ",".join(analysis["gates_missing"]))
    if abi != 1:
        errors.append("selected ABI is %r, want 1" % (abi,))
    if EXPECTED_API < (analysis["level"] or 99):
        errors.append("expected radio API %d below module level" % EXPECTED_API)
    can_10k = bool(analysis.get("single_invocation_10k")
                   or analysis.get("full_bench_single_invocation_10k"))
    record = {
        "ok": not errors,
        "errors": errors,
        "source": PROBE_SRC_REL,
        "module_level": analysis["level"],
        "selected_abi": abi,
        "header_km_level": header_level,
        "inert_default": analysis["inert_default"],
        "bench_gate_exact": analysis.get("bench_gate_exact"),
        "expected_build_id": EXPECTED_BUILD_ID,
        "expected_api": EXPECTED_API,
        "ops_per_invocation": analysis["ops_per_invocation"],
        "required_ops": REQUIRED_OPS,
        "single_invocation_10k": analysis["single_invocation_10k"],
        "full_bench_default_off": analysis.get("full_bench_default_off"),
        "full_bench_ladders": analysis.get("full_bench_ladders"),
        "full_bench_reps_per_ladder": analysis.get(
            "full_bench_reps_per_ladder"),
        "full_bench_patterns": analysis.get("full_bench_patterns"),
        "full_bench_ops": analysis.get("full_bench_ops"),
        "full_bench_single_invocation_10k": analysis.get(
            "full_bench_single_invocation_10k"),
        "full_bench_available": analysis.get("full_bench_available"),
        "forbidden_tokens_found": analysis["forbidden_tokens_found"],
        "gates_present": analysis["gates_present"],
        "gates_missing": analysis["gates_missing"],
        "required_change": None if can_10k
        else required_change_text(analysis),
    }
    if prefix is not None:
        os.makedirs(prefix, exist_ok=True)
        with open(os.path.join(prefix, "preflight.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, sort_keys=True)
            f.write("\n")
    return (EXIT_OK if not errors else EXIT_FAIL), record


# ---- live radio helpers: imported lazily so development/tests never touch hardware ----

@contextlib.contextmanager
def _open_link(port):
    """Yield a LiveLink on the port held under the advisory serial lock.

    Single attempt only: no retry, no fallback port, no auto reconnect.
    All imports are lazy so module import never touches hardware.
    """
    sys.path.insert(0, repo_root())
    sys.path.insert(0, os.path.join(repo_root(), "tools"))
    import k6_port_lock as port_lock  # noqa: E402
    import k6_install as install  # noqa: E402
    import k6_live as live  # noqa: E402
    with port_lock.serial_lock(port):
        raw = install.open_port(argparse.Namespace(port=port))
        try:
            yield live.LiveLink(raw)
        finally:
            try:
                raw.close()
            except Exception:  # noqa: BLE001 - close must not mask run outcome
                pass


def _new_serial(link):
    sys.path.insert(0, repo_root())
    sys.path.insert(0, os.path.join(repo_root(), "tools"))
    import k6_install as install  # noqa: E402
    serials = install.Serials()
    serials.advance_after(link.serial_watermark())
    serial = serials.next()
    if serial > 0xFFFFFFFF:
        raise ValueError("module replay watermark exhausted")
    return serial


def _build_probe_image(link):
    """Compile the bench-enabled probe and sign it for temporary slot 30.

    Reads the module key from disk (supervisor-provisioned); never from the radio.
    """
    sys.path.insert(0, repo_root())
    from modem import k6mod  # noqa: E402
    root = repo_root()
    src = os.path.join(root, PROBE_SRC_REL)
    wrapped_src = src + ".benchwrap.c"
    with open(src, "r", encoding="utf-8") as f:
        body = f.read()
    if "TX28_BENCH_ENABLE" not in body:
        raise ValueError("probe source lacks the TX28_BENCH_ENABLE gate")
    with open(wrapped_src, "w", encoding="utf-8") as f:
        f.write('#define TX28_BENCH_ENABLE 1\n#define TX28_FULL_BENCH 1\n'
                '#include "%s"\n' % src)
    try:
        code, entry = k6mod.build(wrapped_src)
    finally:
        try:
            os.unlink(wrapped_src)
        except OSError:
            pass
    level = k6mod.source_level(src)
    if level != 2:
        raise ValueError("probe module level is %r, want 2 (RX-only)" % (level,))
    key = _read_module_key()
    serial = _new_serial(link)
    return k6mod.image(code, entry, serial, key, level=level, lib=TEMP_SLOT)


def _read_module_key():
    sys.path.insert(0, repo_root())
    sys.path.insert(0, os.path.join(repo_root(), "tools"))
    from modem import k6mod  # noqa: E402
    import k6_install as install  # noqa: E402
    path = install.find_key()
    key = k6mod.read_key(path)
    if key == k6mod.PLACEHOLDER:
        raise ValueError("do not sign with the key placeholder")
    return key


def _packed_inventory(link):
    """Exact populated KUL2 directory; never read fabricated 16KiB slots."""
    directory=link.library_directory()
    if not isinstance(directory,dict):
        raise ValueError("radio does not expose verified packed KUL2 directory")
    if TEMP_SLOT in directory:
        raise ValueError("temporary slot30 is occupied; no safe exact restoration")
    out={}
    for slot,(_,size) in directory.items():
        if not isinstance(slot,int) or not 0<=slot<LIB_SLOTS or not 24<=size<=7168:
            raise ValueError("invalid live packed module metadata")
        blob=bytes(link.library_read(slot,size))
        if len(blob)!=size:
            raise ValueError("incomplete packed module readback")
        out[str(slot)]={"sha256":hashlib.sha256(blob).hexdigest(),
                        "bytes":size,"erased":False}
    return out


def snapshot_state(link):
    """Read-only VFO + exact populated module hashes. Slot30 must be empty."""
    status=link.status()
    info=link.info()
    caps=link.capabilities()
    slots=_packed_inventory(link)
    return {
        "taken_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
        "status":status,
        "build_id":info.get("build_id").hex() if isinstance(info.get("build_id"),bytes) else info.get("build_id"),
        "protocol":info.get("protocol"),"features":caps.get("features"),
        "slots":slots,"slot_count":len(slots),
        "temp_slot30_empty":str(TEMP_SLOT) not in slots,
    }


def write_blob_backup(link,prefix):
    """Backup only populated, exactly sized packed module images."""
    libdir=os.path.join(prefix,"library")
    os.makedirs(libdir,exist_ok=True)
    manifest=_packed_inventory(link)
    for slot,data in manifest.items():
        blob=bytes(link.library_read(int(slot),data["bytes"]))
        if hashlib.sha256(blob).hexdigest()!=data["sha256"]:
            raise ValueError("module changed between independent backup passes")
        with open(os.path.join(libdir,"slot%02d.bin"%int(slot)),"wb") as f:
            f.write(blob)
    with open(os.path.join(libdir,"manifest.json"),"w",encoding="utf-8") as f:
        json.dump(manifest,f,indent=2,sort_keys=True)
        f.write("\n")
    return manifest


def check_rx_idle(status):
    """Refuse unless the radio reports a non-transmitting idle RX state."""
    if status.get("transmitting"):
        return "radio reports transmitting; TX guard refuses the run"
    if status.get("sleeping"):
        return "radio reports sleeping; standby required"
    if not status.get("standby_idle"):
        return "radio is not standby-idle (a session, key or conflicting mode is active)"
    return None


def restore_state(link,prefix,manifest,vfo_backup):
    """Remove ONLY the supervised temporary slot, verify original state."""
    outcome={"slot30_restored":False,"vfo_restored":False,"tx_off":False,
             "notes":[]}
    try:
        directory=link.library_directory()
        if not isinstance(directory,dict):
            raise ValueError("packed directory disappeared")
        if TEMP_SLOT in directory:
            sys.path.insert(0,repo_root())
            from modem import k6mod
            key=_read_module_key()
            serial=_new_serial(link)
            removal=k6mod.image(b"K6ERASE",0,serial,key,lib=TEMP_SLOT,remove=True)
            link.mutate_library(removal)
        latest=link.library_directory()
        outcome["slot30_restored"]=TEMP_SLOT not in latest
        if not outcome["slot30_restored"]:
            outcome["notes"].append("temporary slot30 still occupied")
    except Exception as exc:
        outcome["notes"].append("slot30 restore not confirmed: %s"%exc)
    try:
        status=link.status()
        want=(vfo_backup or {}).get("status",{}).get("vfos")
        if want is None:
            outcome["notes"].append("missing VFO backup")
        else:
            outcome["vfo_restored"]=status.get("vfos")==want
            if not outcome["vfo_restored"]:
                outcome["notes"].append("VFO drift; refusing unreviewed retune")
        outcome["tx_off"]=status.get("transmitting") is False
    except Exception as exc:
        outcome["notes"].append("VFO/TX recheck failed: %s"%exc)
    return outcome


def parse_probe_reports(reports, ret):
    """Score module output WITHOUT hardware: ops must equal the module return.

    The bench probe returns its verified write+readback count on success
    (384 today) or a negative refusal/error code. Anything else is a failure.
    """
    parsed_errors=[]
    for line in reports:
        m=re.search(r"(?:^|\s)err=(\d+)(?:\s|$)",line)
        parsed_errors.append(int(m.group(1)) if m else 1)
    errors=sum(parsed_errors)
    ladder_lines = [line for line in reports if line.startswith("tx28 nop=")]
    ops = ret if isinstance(ret, int) and ret > 0 else 0
    return {"ops": ops, "errors": errors, "ladder_lines": len(ladder_lines)}


def score_run(parsed, provenance, state_ok):
    """Hardware scorer: exact 10,000 + preserved state + live provenance.

    Mock, replayed or offline data is NEVER a physical PASS, no matter the counts.
    Returns (passed, [reasons]).
    """
    reasons = []
    if not provenance.get("hardware"):
        reasons.append("no live-hardware provenance: mock/offline data is not a physical PASS")
    if provenance.get("mock"):
        reasons.append("mock provenance flag set: refuse as physical result")
    if parsed.get("ops") != REQUIRED_OPS:
        reasons.append("ops=%s, want exactly %d" % (parsed.get("ops"), REQUIRED_OPS))
    if parsed.get("errors"):
        reasons.append("readback errors=%s, want 0" % (parsed.get("errors"),))
    if not state_ok:
        reasons.append("radio state not fully preserved (slot/VFO/TX recheck)")
    return (not reasons), reasons


def _write_json(prefix, name, record):
    os.makedirs(prefix, exist_ok=True)
    with open(os.path.join(prefix, name), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")


def cmd_run(root, port, prefix):
    """One finite full 10k readback experiment. Single attempt, no retry."""
    os.makedirs(prefix, exist_ok=True)
    rc, pre = cmd_preflight(root, prefix=None)
    if rc != EXIT_OK:
        rec = {"passed": False, "provenance": {"hardware": False, "mock": False},
               "reason": "preflight failed", "preflight": pre}
        _write_json(prefix, "run.json", rec)
        return EXIT_FAIL, rec
    can_10k = bool(pre.get("single_invocation_10k")
                   or pre.get("full_bench_single_invocation_10k"))
    if not can_10k:
        rec = {"passed": False, "provenance": {"hardware": False, "mock": False},
               "reason": "blocked: probe cannot provide 10000 readbacks in one invocation",
               "ops_per_invocation": pre["ops_per_invocation"],
               "full_bench_ops": pre.get("full_bench_ops"),
               "required_ops": REQUIRED_OPS,
               "required_change": pre["required_change"],
               "radio_contacted": False,
               "preflight": pre}
        _write_json(prefix, "run.json", rec)
        return EXIT_BLOCKED, rec
    # A capable probe exists: proceed to the single guarded hardware attempt.
    with _open_link(port) as link:
        return _cmd_run_live(link, root, prefix, pre)


def _cmd_run_live(link, root, prefix, pre):
    vfo_backup, manifest, result = None, None, None
    failure = None
    try:
        info = link.info()
        live_build = info.get("build_id")
        live_build = live_build.hex() if isinstance(live_build, bytes) else live_build
        if live_build != EXPECTED_BUILD_ID:
            raise ValueError("radio build %r != expected %s; refusing"
                             % (live_build, EXPECTED_BUILD_ID))
        status = link.status()
        refusal = check_rx_idle(status)
        if refusal:
            raise ValueError(refusal)
        vfo_backup = snapshot_state(link)
        _write_json(prefix, "vfo_backup.json", vfo_backup)
        manifest = write_blob_backup(link, prefix)
        image = _build_probe_image(link)
        link.mutate_library(image)
        completion = link.run_module(TEMP_SLOT, timeout=RUN_TIMEOUT_S)
        reports = completion.get("reports", [])
        parsed = parse_probe_reports(reports, completion.get("result"))
        provenance = {"hardware": True, "mock": False, "build_id": live_build,
                      "slot": TEMP_SLOT, "job": completion.get("job_id")}
        result = {"parsed": parsed, "reports": reports,
                  "module_result": completion.get("result"), "provenance": provenance}
    except Exception as exc:  # noqa: BLE001 - all paths restore and report
        failure = "%s: %s" % (type(exc).__name__, exc)
    restore = None
    try:
        if manifest is not None and vfo_backup is not None:
            restore = restore_state(link, prefix, manifest, vfo_backup)
    except Exception as exc:  # noqa: BLE001
        restore = {"slot30_restored": False, "vfo_restored": False,
                   "tx_off": False, "notes": ["restore raised: %s" % exc]}
    try:
        final_status = link.status()
        tx_off = not final_status.get("transmitting", True)
    except Exception:  # noqa: BLE001
        tx_off = False
    state_ok = bool(restore) and all(
        restore.get(k) for k in ("slot30_restored", "vfo_restored", "tx_off")) and tx_off
    if failure is not None:
        rec = {"passed": False, "provenance": {"hardware": True, "mock": False},
               "reason": failure, "restore": restore, "tx_off_final": tx_off}
    else:
        passed, reasons = score_run(result["parsed"], result["provenance"], state_ok)
        rec = {"passed": passed, "parsed": result["parsed"],
               "module_result": result["module_result"],
               "provenance": result["provenance"],
               "restore": restore, "tx_off_final": tx_off,
               "reasons": reasons}
    _write_json(prefix, "run.json", rec)
    return (EXIT_OK if rec["passed"] else EXIT_FAIL), rec


def cmd_verify(root, prefix):
    """Read-only recheck: no TX, no drift, hardware-ready results. Never mutates."""
    try:
        with open(os.path.join(prefix, "run.json"), "r", encoding="utf-8") as f:
            run_rec = json.load(f)
    except OSError as exc:
        return EXIT_FAIL, {"passed": False, "reasons": ["no run.json under prefix: %s" % exc]}
    try:
        with open(os.path.join(prefix, "library", "manifest.json"), "r",
                               encoding="utf-8") as f:
            manifest = json.load(f)
    except OSError as exc:
        return EXIT_FAIL, {"passed": False, "reasons": ["no library manifest: %s" % exc]}
    try:
        with open(os.path.join(prefix, "vfo_backup.json"), "r", encoding="utf-8") as f:
            vfo_backup = json.load(f)
    except OSError:
        vfo_backup = None
    port = os.environ.get("K6_PORT", "/dev/ttyUSB0")
    with _open_link(port) as link:
        return _cmd_verify_live(link, run_rec, manifest, vfo_backup, prefix)


def _cmd_verify_live(link, run_rec, manifest, vfo_backup, prefix):
    reasons = []
    try:
        status = link.status()
    except Exception as exc:  # noqa: BLE001
        return EXIT_FAIL, {"passed": False, "reasons": ["status unreadable: %s" % exc]}
    if status.get("transmitting"):
        reasons.append("radio reports transmitting")
    want_vfos = (vfo_backup or {}).get("status", {}).get("vfos")
    if want_vfos is not None and status.get("vfos") != want_vfos:
        reasons.append("VFO drift vs backup")
    drifted=[]
    try:
        directory=link.library_directory()
        if not isinstance(directory,dict):
            raise ValueError("packed KUL2 directory unavailable")
        current=set(directory)
        expected={int(slot) for slot in manifest}
        if TEMP_SLOT in current or current!=expected:
            reasons.append("packed module directory/slot30 differs from baseline")
        for slot,(_,size) in directory.items():
            want=manifest.get(str(slot))
            if not want or want.get("bytes")!=size:
                drifted.append(slot)
                continue
            blob=bytes(link.library_read(slot,size))
            if hashlib.sha256(blob).hexdigest()!=want["sha256"]:
                drifted.append(slot)
    except Exception as exc:
        reasons.append("packed module readback failed: %s"%exc)
    if drifted:
        reasons.append("slot drift vs backup: %s"%drifted)
    parsed = run_rec.get("parsed", {})
    provenance = run_rec.get("provenance", {})
    state_ok = not reasons
    passed, score_reasons = score_run(parsed, provenance, state_ok)
    reasons.extend(score_reasons)
    rec = {"passed": passed, "reasons": reasons,
           "ops": parsed.get("ops"), "provenance": provenance,
           "writes_performed": False}
    _write_json(prefix, "verify.json", rec)
    return (EXIT_OK if passed else EXIT_FAIL), rec


def build_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--run", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--dummy-load-confirmed", action="store_true")
    p.add_argument("--operator-lease-confirmed", action="store_true")
    p.add_argument("--queue-lease-acknowledged", action="store_true")
    p.add_argument("--prefix", default=None)
    p.add_argument("--port", default=None,
                   help="serial port (default $K6_PORT or /dev/ttyUSB0); run/verify only")
    return p


def _require_abs_prefix(prefix):
    if not prefix or not os.path.isabs(prefix):
        return "--prefix ABS_PATH is required"
    return None


def main(argv=None):
    args = build_parser().parse_args(argv)
    root = repo_root()
    modes = [args.preflight_only, args.run, args.verify_only]
    if sum(bool(m) for m in modes) != 1:
        print("choose exactly one of --preflight-only, --run, --verify-only",
              file=sys.stderr)
        return EXIT_USAGE
    if args.preflight_only:
        if args.dummy_load_confirmed or args.operator_lease_confirmed \
                or args.queue_lease_acknowledged:
            print("--preflight-only takes no confirmation flags", file=sys.stderr)
            return EXIT_USAGE
        rc, record = cmd_preflight(root, prefix=args.prefix)
        print(json.dumps(record, indent=2, sort_keys=True))
        return rc
    if args.run:
        err = _require_abs_prefix(args.prefix)
        if err or not (args.dummy_load_confirmed and args.operator_lease_confirmed
                       and args.queue_lease_acknowledged):
            print("run needs --dummy-load-confirmed --operator-lease-confirmed "
                  "--queue-lease-acknowledged --prefix ABS_PATH", file=sys.stderr)
            return EXIT_USAGE
        port = args.port or os.environ.get("K6_PORT", "/dev/ttyUSB0")
        rc, record = cmd_run(root, port, args.prefix)
        print(json.dumps(record, indent=2, sort_keys=True))
        return rc
    err = _require_abs_prefix(args.prefix)
    if err:
        print("verify needs --prefix ABS_PATH", file=sys.stderr)
        return EXIT_USAGE
    rc, record = cmd_verify(root, args.prefix)
    print(json.dumps(record, indent=2, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main())
