#!/usr/bin/env python3
"""API29 packed-slot prospective FSK09S-H B-chip FIFO AE IRQ qualification harness.

Strictly opt-in physical transaction. Default (no flags) touches NO hardware:
run with --analyze-only for offline IQ scoring, or pass all three runtime
switches (--dummy-load-confirmed --operator-lease-confirmed
--queue-lease-acknowledged) for ONE guarded physical run.

Safety contract (never weaken):
- Frozen source/code SHA checks before any radio access (chip ID 0x4829 is
  enforced inside the module itself; wrong chip returns -6, never TX).
- Exact build + module API + cable-only + VFO/standby/SDR preflight gates.
- Auto-select a VERIFIED EMPTY library slot; never overwrite an existing slot.
- Canonical nonblocking serial_lock; whole-directory hash restoration check.
- Single run_module call (tx_module_requests is at most 1); no retry.
- 1900 ms module-internal TX lease; independent packet + IRQ timing scoring.
- Conservative abort + cleanup on any crash/failure; no automatic retry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modem import k6mod
from k6_install import Cable, open_port, CABLE_HANDSHAKE, LIB_BASE, LIB_SLOTS, Serials
from k6_live import LiveLink
from k6_port_lock import serial_lock
from k6_user_store_migrate import locations
from k6_fsk_rf_record import RtlCapture, summarize_raw_iq, save_capture
from k6_fsk_sotto_sync_rx import find_frames

ROOT = Path(__file__).resolve().parents[1]
SOURCE_INERT = ROOT / "c/k6/mod/fsk_bchip_ae_qual.c"
SOURCE_ARMED = ROOT / "c/k6/mod/fsk_bchip_ae_qual_armed.c"
SOURCE_VECTOR = ROOT / "c/k6/mod/fsk_lab_vector.h"

EXPECTED_BUILD = "339e08a2b77c7679"
EXPECTED_API = 29
EXPECTED_CHIP = 0x4829
EXPECTED_RESULT = 98
TX_WATCHDOG_MS = 1900
VFO_A_HZ = 433_500_000
VFO_B_HZ = 144_390_000
PORT_DEFAULT = "/dev/ttyUSB0"

PIN_SOURCE_INERT_SHA256 = "4472c7d95605fe01d6f41a13f1e84380c909df493d5842e93e53306a8a06bd69"
PIN_SOURCE_ARMED_SHA256 = "7ebd2a4ac62314abee6781c8ca194a39607c667d1b1e0582a60fc6e5a65812cf"
PIN_VECTOR_SHA256 = "230ba218a033e5782b42c3d191e557723b1e0656a9ac1eaa546e09090d125450"
PIN_CODE_SHA256 = "a14bf5a7538c6510de54b85a0cc69580ae86fa06453e53efd7ca802c9f046879"
PIN_CODE_BYTES = 1666
PIN_CODE_ENTRY = 0
PIN_CODE_LEVEL = 3
PIN_WORDS_LE_SHA256 = "41a8c14cc0be749f1af3b31c13bd67bf6644a1aff7d5643e9d11de8da24f7b3e"
PIN_WORD_COUNT = 64
N_DATA_WORDS = 48
GUARD = 0xA55A
IRQ_FIELDS = ("th", "bit", "evt", "done", "irq", "last", "polls", "status")


def _sha(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def lab_words() -> tuple:
    """Parse fsk_lab_words from the frozen header (read-only, no codegen)."""
    text = SOURCE_VECTOR.read_text()
    m = re.search(r"fsk_lab_words\[.*?\]\s*=\s*\{([^}]*)\}", text, re.S)
    if not m:
        raise ValueError("frozen vector header has no fsk_lab_words array")
    words = tuple(int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", m.group(1)))
    if len(words) != PIN_WORD_COUNT or any(not 0 <= w <= 0xFFFF for w in words):
        raise ValueError("frozen vector word count/range mismatch")
    blob = b"".join(w.to_bytes(2, "little") for w in words)
    if hashlib.sha256(blob).hexdigest() != PIN_WORDS_LE_SHA256:
        raise ValueError("frozen vector words do not match header SHA comment")
    return words


def preflight_source() -> dict:
    """Verify frozen sources + compiled module. Raises on ANY mismatch."""
    checks = {
        str(SOURCE_INERT): PIN_SOURCE_INERT_SHA256,
        str(SOURCE_ARMED): PIN_SOURCE_ARMED_SHA256,
        str(SOURCE_VECTOR): PIN_VECTOR_SHA256,
    }
    for path, pin in checks.items():
        if _sha(Path(path)) != pin:
            raise ValueError(f"frozen source altered, NO radio access: {path}")
    code, entry = k6mod.build(str(SOURCE_ARMED))
    code_sha = hashlib.sha256(code).hexdigest()
    if code_sha != PIN_CODE_SHA256 or len(code) != PIN_CODE_BYTES:
        raise ValueError("compiled module mismatch, NO radio access")
    if entry != PIN_CODE_ENTRY or k6mod.source_level(str(SOURCE_ARMED)) != PIN_CODE_LEVEL:
        raise ValueError("module entry/ABI mismatch, NO radio access")
    words = lab_words()
    return dict(source_ok=True, code_sha256=code_sha, code_bytes=len(code),
                entry=entry, level=PIN_CODE_LEVEL, lab_words=len(words))


def expected_packet() -> bytes:
    return b"".join(w.to_bytes(2, "little") for w in lab_words()[:N_DATA_WORDS])


def parse_ae_report(lines) -> dict:
    """Parse raw compact AE telemetry emitted only after TX restore."""
    if not isinstance(lines, (list, tuple)):
        raise ValueError("expected a list of raw module lines")
    events = {}
    for line in lines:
        m = re.fullmatch(r"AE([01])\s+(.+)", line)
        if not m:
            continue
        n = int(m.group(1))
        ent = events.setdefault(n, {})
        for field, value in re.findall(r"([a-z0-9]+)=([0-9A-F]{4})", m.group(2)):
            if field not in IRQ_FIELDS:
                raise ValueError("unknown raw IRQ field")
            if field in ent:
                raise ValueError("duplicate IRQ field")
            ent[field] = int(value, 16)
    if set(events) != {0, 1} or any(set(x) != set(IRQ_FIELDS) for x in events.values()):
        raise ValueError("two complete B IRQ traces were not reported")
    if events[0]["th"] != 24 or events[1]["th"] != 8:
        raise ValueError("thresholds reported do not match source protocol")
    return events


def assess_ae(raw) -> dict:
    """Separate observational qualification; never asserts B semantics."""
    events = parse_ae_report(raw)
    possible = []
    for key in ("bit", "evt"):
        a, b = events[0][key], events[1][key]
        okay = (a != 0xFFFF and b != 0xFFFF and 55 <= a <= 125 and
                110 <= b <= 190 and 40 <= b - a <= 67 and
                a <= events[0]["done"] and b <= events[1]["done"])
        possible.append(dict(mode=key,
                             first_threshold24_ms=None if a == 0xFFFF else a,
                             first_threshold8_ms=None if b == 0xFFFF else b,
                             measured_delta_ms=None if a == 0xFFFF or b == 0xFFFF else b - a,
                             passes_timing_discriminator=bool(okay)))
    return dict(observations=events, possible_interrupt_encodings=possible,
                provisional_b_ae_present=any(x["passes_timing_discriminator"] for x in possible),
                not_yet_safe_for_refill=True,
                qualification="Observe repeated raw traces with SDR and no live writes before enabling refill.")


def score_iq(iq, *, min_correlation=0.90) -> dict:
    """Blind public-sync acquisition; payload truth compared only after decisions."""
    decision = find_frames(iq, rate_factors=(1.,), min_correlation=min_correlation,
                           payload_bytes=98)
    truth = expected_packet()
    frames = []
    for p in decision["packets"]:
        payload = bytes.fromhex(p["raw_payload_hex"])
        clean = payload == truth + GUARD.to_bytes(2, "little")
        prefix = payload[:2] == b"\xf8\x00" and payload[2:] == truth
        frames.append(dict(payload_start_s=p["payload_start_s"],
                           baud_estimate=p["baud_estimate"],
                           sync_correlation=p["sync_correlation"],
                           sync_bit_errors=p["sync_bit_errors"],
                           strict_pass=bool(clean or prefix),
                           control_prefix_offset_bytes=(2 if prefix else 0 if clean else None),
                           data_payload_sha256=hashlib.sha256(payload[2:] if prefix else payload[:96]).hexdigest(),
                           exact_48_word_data=bool(clean or prefix),
                           full_frame_hash=hashlib.sha256(payload).hexdigest()))
    strict = len(frames) == 2 and all(f["strict_pass"] for f in frames)
    return dict(test="FSK09S-H B chip 2x49word FIFO IRQ qualification: RF only",
                result="RF_PAYLOAD_PASS" if strict else "RF_PAYLOAD_FAIL",
                expected_frames=2, frames_found=decision["frames_found"],
                programmed_baud=4800, public_sync_only=True,
                expected_48word_data_sha256=hashlib.sha256(truth).hexdigest(),
                zero_payload_bit_errors_if_pass=strict, frames=frames,
                caveat="AE IRQ timing is an independent qualification: RF data PASS does not prove IRQ register semantics.")


def extract_module_lines(meta: dict) -> list:
    """Accept direct lists or verified restored bench reports only."""
    if isinstance(meta, list):
        return meta
    transaction = meta.get("hardware_lab_transaction", meta)
    module = transaction.get("module_completion", {})
    if not isinstance(module, dict):
        raise ValueError("invalid module transaction")
    if not (transaction.get("original_module_restored") and
            transaction.get("original_radio_frequency_restored") and
            transaction.get("transmission_confirmed_by_firmware") and
            module.get("returned") and module.get("result") == EXPECTED_RESULT):
        raise ValueError("no verified completed and restored FSK09S-H transaction")
    return module.get("reports", [])


def score_joint(rf: dict, raw: list) -> dict:
    irq = assess_ae(raw)
    joint = "RF_AND_PROVISIONAL_AE" if (
        rf["result"] == "RF_PAYLOAD_PASS" and irq["provisional_b_ae_present"]) else "FAIL_OR_INCONCLUSIVE"
    return dict(rf=rf, irq_discriminator=irq, joint_qualification=joint)


def vfo_ok(state: dict) -> bool:
    vfos = state.get("vfos", [])
    return (state.get("watching") == 0 and len(vfos) == 2 and
            [v.get("hz") for v in vfos] == [VFO_A_HZ, VFO_B_HZ] and
            all(v.get("mode") == "FM" and v.get("wide") is True and
                v.get("memory") is False for v in vfos))


def preflight_state(caps: dict, state: dict) -> dict:
    if (caps.get("build_id") != EXPECTED_BUILD or
            caps.get("module_api_max") != EXPECTED_API or
            not caps.get("cable_only") or not caps.get("fsk_rx")):
        raise RuntimeError("unsafe firmware: exact build/API29/cable-only/FSK-RX required")
    if (state.get("transmitting") or state.get("sleeping") or
            state.get("squelch_open") or not state.get("standby_idle") or
            not vfo_ok(state)):
        raise RuntimeError("unsafe radio state: standby idle with watched VFOs required")
    return state


def directory(link) -> dict:
    return locations(link.read(LIB_BASE, 8192) + bytes(512 * 1024 - 8192))


def snapshot(link) -> dict:
    return {str(i): hashlib.sha256(link.read(LIB_BASE + a, n)).hexdigest()
            for i, (a, n) in directory(link).items()}


def select_empty_slot(d: dict) -> int:
    # Only slot30 has a known reversible packed-directory protocol. The
    # highest generic free slot is NOT necessarily safe or unreserved.
    if 30 in d:
        raise RuntimeError("reviewed temporary slot30 is occupied; refusing overwrite")
    return 30


def _open(port: str):
    return open_port(SimpleNamespace(port=port))


def wait_live(port: str):
    time.sleep(3)
    with _open(port) as p:
        return preflight_state(LiveLink(p, timeout=3).module_capabilities(),
                               LiveLink(p, timeout=3).status())


def run_transaction(port: str, prefix: Path, *, dummy_load_confirmed=False,
                    operator_lease_confirmed=False,
                    queue_lease_acknowledged=False) -> dict:
    if port != PORT_DEFAULT:
        raise RuntimeError("unexpected serial port; only locally qualified K6 permitted")
    if not (dummy_load_confirmed and operator_lease_confirmed and queue_lease_acknowledged):
        raise RuntimeError("refusing hardware operation without dummy load, operator lease and queue ACK")
    frozen = preflight_source()
    if any(prefix.with_name(prefix.name + s).exists()
           for s in (".json", ".npy", "-rx.json", "-score.json", "-pre.json")):
        raise FileExistsError("refusing to overwrite existing evidence")
    os.umask(0o077)
    if not prefix.parent.is_dir() or prefix.parent.stat().st_mode & 0o077:
        raise RuntimeError("private 0700 output directory required")
    report = dict(experiment="FSK09S-H B-chip FIFO AE IRQ packed-slot qualification",
                  dummy_load_operator_confirmed=True, lease_confirmed=True,
                  build_id=EXPECTED_BUILD, module_api=EXPECTED_API,
                  chip_id_hex=hex(EXPECTED_CHIP), tx_module_requests=0,
                  tx_watchdog_ms=TX_WATCHDOG_MS, expected_module_result=EXPECTED_RESULT,
                  no_automatic_retry=True, frozen=frozen,
                  code_sha256=frozen["code_sha256"],
                  script_sha256=_sha(Path(__file__)))
    serials, raw, attempted, primary_error = Serials(), None, False, None
    with serial_lock(port):
        with _open(port) as p:
            link = LiveLink(p, timeout=3)
            initial = preflight_state(link.module_capabilities(), link.status())
            before = directory(link)
            slot = select_empty_slot(before)
            report["temp_slot"] = slot
            hashes = snapshot(link)
        with prefix.with_name(prefix.name + "-pre.json").open("x") as f:
            json.dump(dict(status=initial, slot_hashes=hashes, build=EXPECTED_BUILD,
                           temp_slot=slot), f, indent=2)
        with RtlCapture(center_hz=433_750_000, fs=1_024_000, gain_tenth_db=0) as sdr:
            baseline = summarize_raw_iq(sdr.read(0.45))
            report["sdr_preflight"] = baseline
            if baseline["potential_occupied_or_overloaded"]:
                raise RuntimeError("SDR baseline occupied/overloaded; aborting without TX")
            try:
                with _open(port) as p:
                    c = Cable(p, timeout=3)
                    if not c.handshake(CABLE_HANDSHAKE, wait=4):
                        raise RuntimeError("legacy handshake failed")
                    try:
                        caps = c.capabilities()
                        if (caps["build_id"] != EXPECTED_BUILD or
                                caps.get("module_api_max") != EXPECTED_API or
                                not caps["cable_only"] or
                                not caps.get("fsk_rx")):
                            raise RuntimeError("unexpected firmware on cable path")
                        if slot in c.library_directory():
                            raise RuntimeError("temp slot no longer empty; refusing overwrite")
                        try:
                            serials.advance_after(c.serial_watermark())
                        except Exception:
                            pass
                        n = serials.next()
                        img = k6mod.image(frozen_code(), PIN_CODE_ENTRY,
                                          n, None, level=PIN_CODE_LEVEL, lib=slot)
                        attempted = True
                        reply = c.send_image(img, n & 255)
                        if reply[0] != 3 or reply[3] != len(img):
                            raise RuntimeError(f"install reply {reply}")
                        if c.library_read(slot, len(img)) != img:
                            raise RuntimeError("readback mismatch")
                    finally:
                        c.end()
                wait_live(port)
                with _open(port) as p:
                    link = LiveLink(p, timeout=3)
                    preflight_state(link.module_capabilities(), link.status())
                    off, ln = directory(link)[slot]
                    if link.read(LIB_BASE + off, ln) != img:
                        raise RuntimeError("installed image mismatch; aborting TX")
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        capture = pool.submit(sdr.read, 5.0)
                        time.sleep(0.65)
                        report["tx_module_requests"] = 1
                        try:
                            # Firmware live_module_allowed permits level >2
                            # only with the explicit 0x100 override. The fixed
                            # manifest and exact firmware/API guards vet level3.
                            outcome = link.run_module(slot, timeout=12, allow_higher_api=True,
                                                      on_report=lambda s: print("RF MODULE:", s, flush=True))
                            report["module_completion"] = outcome
                            report["transmission_confirmed_by_firmware"] = bool(
                                outcome.get("returned") and outcome.get("result") == EXPECTED_RESULT)
                        except Exception as e:
                            report["module_error"] = type(e).__name__ + ": " + str(e)
                        finally:
                            raw = capture.result(timeout=20)
                    if link.status()["transmitting"]:
                        raise RuntimeError("TX still active after module return")
            except Exception as exc:
                primary_error = type(exc).__name__ + ": " + str(exc)
                report["operation_error"] = primary_error
            finally:
                if attempted:
                    try:
                        with _open(port) as p:
                            link = LiveLink(p, timeout=3)
                            preflight_state(link.module_capabilities(), link.status())
                            d = directory(link)
                            if slot in d:
                                off, ln = d[slot]
                                if link.read(LIB_BASE + off, ln) != img:
                                    raise RuntimeError("slot changed; refusing blind remove")
                        # Do not open a second cable handle while LiveLink is
                        # still open: some serial drivers require exclusivity.
                        if slot in d:
                            with _open(port) as p2:
                                c = Cable(p2, timeout=3)
                                if not c.handshake(CABLE_HANDSHAKE, wait=4):
                                    raise RuntimeError("cleanup handshake failed")
                                try:
                                    if c.capabilities()["build_id"] != EXPECTED_BUILD:
                                        raise RuntimeError("build changed before cleanup")
                                    if c.library_read(slot, len(img)) != img:
                                        raise RuntimeError("cleanup readback mismatch")
                                    c.remove_slot(slot, None, serials.next())
                                finally:
                                    c.end()
                        wait_live(port)
                        with _open(port) as p:
                            link = LiveLink(p, timeout=3)
                            final = preflight_state(link.module_capabilities(), link.status())
                            report["original_module_restored"] = snapshot(link) == hashes
                            report["original_radio_frequency_restored"] = final["vfos"] == initial["vfos"]
                    except Exception as exc:
                        report["restoration_error"] = type(exc).__name__ + ": " + str(exc)
    if raw is not None:
        rf = summarize_raw_iq(raw)
        rf["hardware_lab_transaction"] = report
        save_capture(raw, prefix, summary=rf)
        # SDR returns interleaved U8 at 1.024 MS/s; the RF detector needs
        # frequency-shifted, complex IQ resampled to 256 kS/s. Feeding
        # raw U8 directly is invalid and may produce false negatives.
        from k6_fsk_iq_edges import read_iq
        iq = read_iq(prefix.with_suffix(".npy"), duration=5., offset=280500.)
        scored = score_iq(iq)
        with prefix.with_name(prefix.name + "-rx.json").open("x") as f:
            json.dump(scored, f, indent=2)
        if not report.get("transmission_confirmed_by_firmware"):
            raise RuntimeError("module did not independently confirm completion; no RF PASS")
        joint = score_joint(scored, extract_module_lines(report))
        with prefix.with_name(prefix.name + "-score.json").open("x") as f:
            json.dump(joint, f, indent=2)
        print(json.dumps(joint["joint_qualification"]))
        if joint["joint_qualification"] != "RF_AND_PROVISIONAL_AE":
            raise SystemExit(1)
    else:
        with prefix.with_suffix(".json").open("x") as f:
            json.dump(report, f, indent=2)
        raise SystemExit("no RF capture; no retry")
    if not report.get("original_module_restored") or not report.get("original_radio_frequency_restored"):
        raise RuntimeError("hardware restoration incomplete; inspect saved report before new TX")
    if primary_error is not None:
        raise RuntimeError(primary_error)
    return report


def frozen_code() -> bytes:
    code, entry = k6mod.build(str(SOURCE_ARMED))
    if hashlib.sha256(code).hexdigest() != PIN_CODE_SHA256 or entry != PIN_CODE_ENTRY:
        raise ValueError("compiled module mismatch at install time")
    return code


def analyze_offline(iq_path: Path, out_path: Path, telemetry_json: Path | None = None) -> dict:
    from k6_fsk_iq_edges import read_iq
    if out_path.exists():
        raise FileExistsError("refusing to overwrite old RF report")
    rf = read_iq(iq_path, duration=3.5, offset=280500.0)
    scored = score_iq(rf)
    with iq_path.open("rb") as f:
        scored["iq_sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
    if telemetry_json is not None:
        raw = json.loads(telemetry_json.read_text())
        irq = assess_ae(extract_module_lines(raw))
        scored["irq_discriminator"] = irq
        scored["joint_qualification"] = "RF_AND_PROVISIONAL_AE" if (
            scored["result"] == "RF_PAYLOAD_PASS" and irq["provisional_b_ae_present"]) else "FAIL_OR_INCONCLUSIVE"
    out_path.write_text(json.dumps(scored, indent=2) + "\n")
    return scored


def verify_restored(port: str, prefix: Path) -> dict:
    """Independent, NON-MUTATING post-run check (even if TX raised).

    This deliberately never clears a slot automatically: unknown or changed
    slots require an operator to reconcile them, not a second experiment.
    """
    prefile=prefix.with_name(prefix.name+"-pre.json")
    if not prefile.is_file():
        raise RuntimeError("missing preflight snapshot; restoration unverified")
    baseline=json.loads(prefile.read_text())
    if baseline.get("build")!=EXPECTED_BUILD or baseline.get("temp_slot")!=30:
        raise RuntimeError("baseline build/slot mismatch")
    if not isinstance(baseline.get("slot_hashes"),dict):
        raise RuntimeError("invalid baseline packed directory")
    with serial_lock(port):
        with _open(port) as p:
            link=LiveLink(p,timeout=3)
            current=preflight_state(link.module_capabilities(),link.status())
            if 30 in directory(link):
                raise RuntimeError("temporary slot30 remains occupied; operator inspection required")
            hashes=snapshot(link)
    if hashes!=baseline["slot_hashes"] or current["vfos"]!=baseline["status"]["vfos"]:
        raise RuntimeError("packed slot hashes or VFOs differ from baseline")
    return {"verified":True,"slot30_free":True,"all_existing_slots_unchanged":True,
            "vfos_unchanged":True,"build":EXPECTED_BUILD}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", default=PORT_DEFAULT)
    p.add_argument("--prefix", type=Path)
    p.add_argument("--dummy-load-confirmed", action="store_true")
    p.add_argument("--operator-lease-confirmed", action="store_true")
    p.add_argument("--queue-lease-acknowledged", action="store_true")
    p.add_argument("--analyze-only", action="store_true")
    p.add_argument("--preflight-only", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--iq", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args(argv)
    if sum([a.analyze_only,a.preflight_only,a.verify_only])>1:
        p.error("select only one non-transmit operation")
    if a.preflight_only:
        print(json.dumps(preflight_source(),indent=2))
        return
    if a.verify_only:
        if a.prefix is None:
            p.error("--verify-only requires --prefix")
        print(json.dumps(verify_restored(a.port,a.prefix),indent=2))
        return
    if a.analyze_only:
        if a.iq is None or a.out is None:
            p.error("--analyze-only needs --iq and --out; no hardware touched")
        print(analyze_offline(a.iq, a.out)["result"])
        return
    if a.prefix is None:
        p.error("--prefix required for a physical run")
    r = run_transaction(a.port, a.prefix,
                        dummy_load_confirmed=a.dummy_load_confirmed,
                        operator_lease_confirmed=a.operator_lease_confirmed,
                        queue_lease_acknowledged=a.queue_lease_acknowledged)
    print("Restored:", r["original_module_restored"], r["original_radio_frequency_restored"],
          "TX confirmed:", r.get("transmission_confirmed_by_firmware"))


if __name__ == "__main__":
    main()

