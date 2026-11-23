#!/usr/bin/env python3
"""Generate an INDEPENDENT IDEAL complex-IQ target for a future Flux FSK+APC trial.

This only compiles a mathematical sample waveform from the documented native
FIFO payload plus an independently timed 80%/100% APC pattern. In particular
it does NOT transmit, make a B-chip register sequence, approximate the real
PLL/PA response, include hardware-generated FIFO framing, or prove the radio
can drive both controls together.

The target exists before an observation and must NOT be replaced by a
phase extracted from received RF when computing full-complex EVM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def make_target(manifest: dict, *, sample_rate=96_000,
                deviation_hz=1200., apc_halfstep_ms=20.6) -> tuple[np.ndarray,dict]:
    data = bytes.fromhex(manifest.get("payload_bytes_hex", ""))
    bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
    bit_truth = manifest.get("payload_bits_msb_each_byte_lower_byte_first")
    if (len(bits) < 512 or len(bits) > 1024 or len(bits) % 16 or
            len(bits) != manifest.get("payload_bits") or
            bit_truth != "".join(str(int(v)) for v in bits) or
            hashlib.sha256(data).hexdigest() != manifest.get("payload_sha256_le_words")):
        raise ValueError("manifest FIFO bit truth / hash inconsistency")
    baud = manifest.get("native_baud")
    if not isinstance(baud,int) or baud not in (1200,2400,3600,4800):
        raise ValueError("unsupported native FSK bit rate")
    if not isinstance(sample_rate,int) or not 12 <= sample_rate//baud <= 500 or sample_rate % baud:
        raise ValueError("sample rate must be exactly divisible by baud, >=12 samples/bit")
    if not np.isfinite(deviation_hz) or deviation_hz <= 0 or deviation_hz >= baud*4:
        raise ValueError("nominal deviation outside offline study bounds")
    if not np.isfinite(apc_halfstep_ms) or not 5 <= apc_halfstep_ms <= 100:
        raise ValueError("unsupported offline envelope timing")
    sps = sample_rate // baud
    frequency = np.repeat((2 * bits.astype(np.float64) - 1) * deviation_hz, sps)
    phase = 2 * np.pi * np.cumsum(frequency, dtype=np.float64) / sample_rate
    t = np.arange(len(frequency)) / sample_rate
    apc_level = np.where((np.floor(1000*t / apc_halfstep_ms).astype(int) % 2) == 0,
                         1.0, 0.8)
    iq = (apc_level * np.exp(1j*phase)).astype(np.complex64)
    report = {
        "analysis": "independently specified ideal FSK+APC baseband TARGET",
        "physical_tx_performed": False,
        "physical_joint_fsk_apc_demonstrated": False,
        "idealized_instantaneous_binary_frequency_hopping": True,
        "source_payload_sha256": hashlib.sha256(data).hexdigest(),
        "source_bit_order": "MSB per byte, little-endian 16-bit FIFO words",
        "source_fifo_words": len(data)//2,
        "source_fifo_bits": int(len(bits)),
        "source_baud": baud,
        "sample_rate_hz": sample_rate,
        "samples_per_bit": sps,
        "complex_samples": len(iq),
        "nominal_payload_duration_ms": round(1000*len(iq)/sample_rate, 5),
        "assumed_frequency_offsets_hz": [-deviation_hz, deviation_hz],
        "assumed_apc_levels_of_full_power": [0.8,1.0],
        "assumed_apc_halfstep_ms": apc_halfstep_ms,
        "nominal_amplitude_transition_count": int(np.count_nonzero(np.diff(apc_level))),
        "caveats": [
            "Target is mathematical; actual RF deviation, PLL settling and APC envelope response differ",
            "FD6818B autonomous preamble, sync and FIFO framing are NOT included",
            "Native FSK physical phase responses are measured but not modeled here",
            "No phase disturbances from APC transitions, PA nonlinearities or spurious spectra are modeled",
            "A successful comparison requires real, separately documented joint RF capture and independent calibration",
        ]
    }
    return iq, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out-prefix", type=Path, required=True)
    parser.add_argument("--sample-rate", type=int, default=96000)
    parser.add_argument("--deviation-hz", type=float, default=1200.)
    parser.add_argument("--apc-halfstep-ms", type=float, default=20.6)
    args = parser.parse_args(argv)
    try:
        raw_manifest = args.manifest.read_bytes()
        manifest = json.loads(raw_manifest)
        iq, report = make_target(manifest, sample_rate=args.sample_rate,
                                 deviation_hz=args.deviation_hz,
                                 apc_halfstep_ms=args.apc_halfstep_ms)
        j, n = args.out_prefix.with_suffix(".json"), args.out_prefix.with_suffix(".npy")
        if j.exists() or n.exists():
            raise ValueError("refusing to overwrite existing independent target")
        j.parent.mkdir(parents=True, exist_ok=True)
        np.save(n, iq, allow_pickle=False)
        report["source_manifest_path"] = str(args.manifest)
        report["source_manifest_sha256"] = hashlib.sha256(raw_manifest).hexdigest()
        report["iq_path"] = str(n)
        report["iq_sha256"] = hashlib.sha256(n.read_bytes()).hexdigest()
        j.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    except (ValueError, OSError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
