#!/usr/bin/env python3
"""Extract K6 physical evidence into compact, provenance-checkable model fixtures.

Read-only: never opens UART, SDR, or a radio bench. Explicit allowlist avoids
silently treating every similar-looking experiment or stale monitor event as
an independent packet observation. No radio bytes/audio are exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

# These experiment types were confirmed by the physical experiment report.
# Config metadata is derived from the specifically named bench procedures;
# monitor good_before/good_after is the observed measurement, NOT the event
# list (whose entries are cumulative). No packet contents are imported.
TX_SPECS = (
    (r"api31-direct-pa20-gap60-prefetch-run[1-5]", "direct_pa20_gap60", 3, True, 20, 60, False, "direct_fsk"),
    (r"api31-onradio-pcm-pa20-gap0-run[1-6]", "onradio_pa20_gap0", 3, True, 20, 0, False, "onradio_codec"),
    (r"api31-onradio-pcm-direct-run[1-4]", "onradio_pa0_gap0", 3, True, 0, 0, False, "onradio_codec"),
    (r"api31-pa0-settle20-prefetch-control1", "pa_muted_preload", 3, False, 20, 60, False, "direct_fsk"),
    (r"api31-pa1-settle0-gap0-run1", "pa_enabled_zero_guard", 3, True, 0, 0, False, "direct_fsk"),
    (r"api31-rekey-each-prefill-run1", "rekey_each_packet", 3, True, 20, 0, True, "direct_fsk"),
    (r"api31-rekey-once-prefill-run1", "rekey_once", 3, True, 20, 0, True, "direct_fsk"),
    (r"frozen-fsk13-current-fw-run1", "legacy_sdr_reader", 8, True, None, None, False, "fsk13_old_reader"),
    (r"fsk13-prefetch-usb-fix-run1", "threaded_sdr_reader", 8, True, None, None, False, "fsk13_new_reader"),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def spec_for(stem: str):
    matches = [x for x in TX_SPECS if re.fullmatch(x[0], stem)]
    if len(matches) != 1:
        raise ValueError(f"unrecognized or ambiguous physical run: {stem}")
    return matches[0]


def tx_record(path: Path) -> dict:
    stem = path.parent.name
    _, family, expected, pa, settle, gap, rekey, mode = spec_for(stem)
    raw = json.loads(path.read_text())
    radio = raw["radio"]
    monitor = raw["monitor"]
    if radio["rc"] != 0 or monitor["state"] != "receiving":
        raise ValueError(f"radio/SDR monitor not healthy in {stem}")
    for field in ("good_before", "good_after", "bad_before", "bad_after"):
        if not isinstance(monitor[field], int) or monitor[field] < 0:
            raise ValueError(f"bad {field} in {stem}")
    good = monitor["good_after"]-monitor["good_before"]
    bad = monitor["bad_after"]-monitor["bad_before"]
    if good < 0 or bad < 0 or good+bad > expected:
        raise ValueError(f"invalid per-run count in {stem}")
    # Preserve missing frames separately; do not assume every missing frame
    # was transmitted, decoded or even observed at the receiver.
    return {
        "id": stem, "family": family,
        "source": f"bench-rf-goal-20261125/{stem}/result.json",
        "sha256": sha(path), "provenance": "physical_k6_and_rtlsdr_monitor",
        "config_provenance": "reviewed_bench_procedure_and_run_identifier",
        "pa_enabled_at_preload": pa, "settle_ms": settle,
        "interframe_gap_ms": gap, "rekey": rekey,
        "receiver_mode": mode,
        "expected_packets": expected, "crc_valid_delta": good,
        "crc_rejected_delta": bad,
        "not_crc_valid_or_rejected": expected-good-bad,
        "complete_session": good == expected and bad == 0,
        "capture_caution": ("Legacy SDR USB read path produced false packet failures; "
                            "do not fit as silicon TX corruption") if family == "legacy_sdr_reader" else None,
    }


def mic_record(path: Path, kind: str, stimulus: str) -> dict:
    raw = json.loads(path.read_text())
    v = 6 if kind == "dma" else 8
    if raw.get("version") != v or not raw.get("available") or raw.get("gp0_selector") != 12:
        raise ValueError(f"unqualified GP0 report {path}")
    windows = raw.get("windows")
    if not isinstance(windows,list) or len(windows) != 4:
        raise ValueError("expected four completed measurement windows")
    freq = (raw["decimated_samples_per_second"] if kind == "dma"
            else raw["dma_decimated_samples_per_second"])
    if not (70_000 < freq < 100_000) or not (0 <= raw["vox_max"] <= 1023):
        raise ValueError("implausible measured rate or VOX")
    a = "amplitude_1khz_q" if kind == "dma" else "pcm_tone_1khz_peak"
    b = "amplitude_2khz_q" if kind == "dma" else "pcm_tone_2khz_peak"
    rms = "rms_raw_density" if kind == "dma" else "pcm_rms_approx"
    row = {
        "id": f"gp0_{kind}_{stimulus}",
        "source": f"sotto-mic-calibration-20261125/gp0-{kind}-v1/tone-{stimulus}-01.json",
        "sha256": sha(path), "provenance": "physical_k6_pa_muted_tx_gp0_selector12",
        "stimulus": stimulus, "nominal_input_v": 0 if stimulus == "off" else 0.1,
        "nominal_input_hz": {"off": None, "1khz": 1000, "2khz": 2000}[stimulus],
        "measurement": kind, "density_fs_hz": freq,
        "inferred_raw_dma_hz": freq * 64,
        "vox_max": raw["vox_max"],
        "spectral_scores": [{"tone_1khz": w[a], "tone_2khz": w[b], "rms": w[rms]}
                            for w in windows],
        "frequency_response_status": "not_calibrated; scores have different scales across DMA and PCM formats",
    }
    if kind == "pcm":
        if raw.get("pcm_samples_total") != 1824 or raw.get("pcm_warmup_samples_discarded") != 800:
            raise ValueError("unexpected PCM length or warmup")
        if any(w["pcm_samples"] != 256 or w["pcm_clipped_samples"] != 0 for w in windows):
            raise ValueError("incomplete or clipped physical PCM capture")
        row["pcm_samples"] = 1824
        row["clipped_samples"] = 0
    return row


def build(txroot: Path, microot: Path) -> dict:
    tx = []
    for pattern,*_ in TX_SPECS:
        for path in sorted(txroot.glob("*/result.json")):
            if re.fullmatch(pattern,path.parent.name):
                tx.append(tx_record(path))
    # Require a complete, stable training/control set; no silent partial import.
    expected_runs = 5+6+4+1+1+1+1+1+1
    if len(tx) != expected_runs or len({r["id"] for r in tx}) != expected_runs:
        raise ValueError(f"TX evidence missing/duplicated: {len(tx)}/{expected_runs}")
    mic = []
    for kind in ("dma", "pcm"):
        for stim in ("off", "1khz", "2khz"):
            mic.append(mic_record(microot/f"gp0-{kind}-v1"/f"tone-{stim}-01.json",kind,stim))
    return {
        "schema": 1, "name": "k6_bchip_20261125_calibration",
        "measured": True,
        "sources": ["RTL-SDR per-run CRC deltas", "K6 GP0/PB1 physical MAB1 telemetry"],
        "notes": [
            "No raw IQ, radio SPI firmware bytes or private SDR logs copied into fixtures.",
            "Observed successful sessions are not sufficient to infer a silicon failure probability.",
            "The 128ms USB reader regression is a host measurement artifact, not a TX RFIC fault.",
            "PA/FIFO settings documented from test procedure, not guaranteed from register trace in result.json.",
            "GP0 amplitude results are relative and tone-specific; no extrapolated frequency response or recovered waveform.",
        ],
        "transmit": sorted(tx,key=lambda x:x["id"]),
        "microphone": sorted(mic,key=lambda x:x["id"]),
    }


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tx-root",required=True,type=Path)
    ap.add_argument("--mic-root",required=True,type=Path)
    ap.add_argument("--output",required=True,type=Path)
    args=ap.parse_args()
    result=build(args.tx_root,args.mic_root)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"output":str(args.output),"tx_records":len(result["transmit"]),
                      "mic_records":len(result["microphone"])}))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
