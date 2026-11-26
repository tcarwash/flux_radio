#!/usr/bin/env python3
"""Offline Flux modulation experiments: hypothetical TX -> RFSim -> SoapyFang RX.

Requires RFSim on PYTHONPATH (e.g. PYTHONPATH="$HOME/dev/rfsim:$PYTHONPATH").
No radio, SoapySDR device, K6 serial, SPI, flashing, PTT or network access.
The SoapyFang tests decode through host-side, known-reference decoders, NOT
native K6 firmware. Even a complete pass does not qualify a physical TX mode.
"""
from __future__ import annotations

import argparse
from itertools import product
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.modulation.tx_paths import ACTUATORS, ENVELOPES, compatibility, synthesize
from tools.k6_soapyfang_modlab import CENTER_HZ, MODES, RATE, fixture, run_mode

PROFILES = (
    "static", "fixed", "fixed-fringe", "knife-edge", "pedestrian",
    "mobile-rural", "mobile-urban", "mobile-bad-urban", "mobile-hilly",
)


def csv_values(raw, converter=str):
    """No silent de-duplication or skipped empty sweep values."""
    parts = raw.split(",")
    if not parts or any(not p.strip() for p in parts):
        raise ValueError("empty or malformed comma-separated sweep")
    values = [converter(p.strip()) for p in parts]
    if len(set(values)) != len(values):
        raise ValueError("duplicate sweep value")
    return values


def candidate_waveform(mode, model, actors, envelope, spi_write_us):
    """Return test IQ plus explicit evidence class, never a hardware command."""
    reference, _ = fixture(mode)
    if model == "ideal":
        return reference, "host_ideal_IQ_only"
    if model == "phase_only":
        magnitude = np.abs(reference)
        tx = np.divide(reference, magnitude,
                       out=np.zeros_like(reference, dtype=np.complex128),
                       where=magnitude > 1e-8)
        return tx, "hypothetical_unrestricted_carrier_phase"
    if model != "k6_path":
        raise ValueError("unknown source model")
    if envelope not in ENVELOPES:
        raise ValueError("unknown envelope")
    status, explanation = compatibility(actors)
    if status in ("conflict", "reference_only") or "ideal" in actors:
        raise ValueError("invalid K6 path: " + explanation)
    return synthesize(reference, tuple(actors), envelope,
                      spi_write_us=spi_write_us), "unqualified_K6_path_model"


def run_case(mode, profile, snr_db, seed, cfo_hz, tx_model="ideal",
             actors=("carrier",), envelope="constant_pa", spi_write_us=117.,
             *, channel_class=None, receiver=run_mode):
    """Perform one virtual on-air experiment, injecting into real SoapyFang RX.

    receiver is injectable for small unit tests. The production default calls
    the actual SigMF -> RFBackend -> ChipModel plus host reference decoder.
    RFSim noise is injected exactly once; SoapyFang adds a negligible 120-dB
    signal-referenced floor rather than replacing the modeled channel noise.
    """
    if mode not in MODES or profile not in PROFILES:
        raise ValueError("unknown mode or RFSim channel")
    if not isinstance(seed, int) or seed < 0 or seed > 2**32-1:
        raise ValueError("seed must be a 32-bit unsigned integer")
    if not math.isfinite(snr_db) or not -10 <= snr_db <= 120:
        raise ValueError("SNR must be finite, -10..120 dB")
    if not math.isfinite(cfo_hz) or abs(cfo_hz) > 500:
        raise ValueError("CFO must be finite and within +/-500 Hz")
    if not math.isfinite(spi_write_us) or not 0 <= spi_write_us <= 1e6:
        raise ValueError("invalid SPI write assumption")
    if channel_class is None:
        try:
            from rfsim.session import ChannelSession
        except ImportError as exc:
            raise RuntimeError(
                "RFSim is not importable; set PYTHONPATH to your RFSim checkout"
            ) from exc
        channel_class = ChannelSession

    tx, evidence = candidate_waveform(mode, tx_model, actors, envelope, spi_write_us)
    tx = np.asarray(tx, dtype=np.complex128)
    session = channel_class(RATE, profile, carrier_hz=CENTER_HZ, seed=seed,
                            duration_s=len(tx)/RATE + .1)
    propagated = session.apply(tx, snr_db=snr_db, cfo_hz=cfo_hz,
                               noise_seed=(seed + 1) & 0xffffffff,
                               signal_power=float(np.mean(np.abs(tx)**2)))
    propagated = np.asarray(propagated, dtype=np.complex128)
    if propagated.shape != tx.shape or not np.isfinite(propagated).all():
        raise ValueError("RFSim returned invalid IQ")
    # In the existing SoapyFang harness, externally generated IQ must carry
    # the offline_path_model label; do not present it as chip-native TX.
    result = receiver(mode, snr_db=120., tx_model="offline_path_model",
                      transmitted_iq=propagated)
    return {
        "mode": mode, "profile": profile, "snr_db": snr_db, "seed": seed,
        "cfo_hz": cfo_hz, "tx_model": tx_model, "actors": list(actors)
            if tx_model == "k6_path" else [],
        "envelope": envelope if tx_model == "k6_path" else None,
        "spi_write_us": spi_write_us if tx_model == "k6_path" else None,
        "transmit_evidence": evidence, "receiver": result,
        "decoded_in_k6_firmware": False, "hardware_qualified": False,
        "physical_tx": False, "reference_assisted_receiver": True,
        "simulation_pass": bool(result["pass"]),
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--modes", default="cw,am,ssb,ssb-lsb,qpsk,ofdm")
    p.add_argument("--profiles", default="static")
    p.add_argument("--snr-db", default="30")
    p.add_argument("--seeds", default="0")
    p.add_argument("--cfo-hz", default="0")
    p.add_argument("--tx-model", choices=("ideal","phase_only","k6_path"),
                   default="ideal")
    p.add_argument("--actors", default="carrier")
    p.add_argument("--envelope", default="constant_pa")
    p.add_argument("--spi-write-us", type=float, default=117.)
    p.add_argument("--json", type=Path, required=True)
    a = p.parse_args(argv)
    try:
        modes = csv_values(a.modes)
        profiles = csv_values(a.profiles)
        snrs = csv_values(a.snr_db, float)
        seeds = csv_values(a.seeds, int)
        cfos = csv_values(a.cfo_hz, float)
        actors = tuple(csv_values(a.actors))
        if any(m not in MODES for m in modes):
            raise ValueError("unknown modulation mode")
        if any(c not in PROFILES for c in profiles):
            raise ValueError("unknown RFSim profile")
        if a.tx_model == "k6_path":
            if any(actor not in ACTUATORS for actor in actors):
                raise ValueError("unknown K6 actor")
            if a.envelope not in ENVELOPES:
                raise ValueError("unknown K6 envelope")
            status, note = compatibility(actors)
            if status in ("conflict", "reference_only"):
                raise ValueError("incompatible K6 actors: " + note)
        if len(modes)*len(profiles)*len(snrs)*len(seeds)*len(cfos) > 500:
            raise ValueError("more than 500 cases; split into bounded runs")
    except ValueError as exc:
        p.error(str(exc))

    rows = []
    for mode, profile, snr, seed, cfo in product(modes, profiles, snrs, seeds, cfos):
        case = dict(mode=mode, profile=profile, snr_db=snr, seed=seed,
                    cfo_hz=cfo, tx_model=a.tx_model, actors=actors,
                    envelope=a.envelope, spi_write_us=a.spi_write_us)
        try:
            row = run_case(**case)
            row["status"] = "decoded" if row["simulation_pass"] else "decode_failed"
            print(f'{mode:8} {profile:17} SNR={snr:5g} CFO={cfo:6g} '
                  f'seed={seed:3d} {row["status"]}', flush=True)
        except (ValueError, RuntimeError, ArithmeticError) as exc:
            row = {k:(list(v) if isinstance(v,tuple) else v) for k,v in case.items()}
            row.update(status="harness_error", error=f"{type(exc).__name__}: {exc}",
                       physical_tx=False, hardware_qualified=False)
            print(f'{mode:8} {profile:17} HARNESS ERROR: {exc}',flush=True)
        rows.append(row)

    report = {
        "kind": "Flux_offline_RFSim_channel_to_SoapyFang_host_decode",
        "physical_tx": False, "hardware_qualified": False,
        "channel_model": "RFSim ChannelSession",
        "rx_model": "SoapyFang SigMF RFBackend ChipModel",
        "receiver_limit": "Known fixture, timing and complex-reference alignment; not blind acquisition",
        "tx_limit": "Ideal IQ, unrestricted phase, or schematic K6 register path; no verified RF hardware",
        "cases": rows,
        "decoded_count": sum(x["status"] == "decoded" for x in rows),
        "decode_failed_count": sum(x["status"] == "decode_failed" for x in rows),
        "harness_error_count": sum(x["status"] == "harness_error" for x in rows),
    }
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(report, indent=2)+"\n")
    # Decoder failures are experiment outcomes, not execution failures.
    # A broken setup or invalid model must not be masked as a negative RF test.
    return 2 if report["harness_error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
