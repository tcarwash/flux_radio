#!/usr/bin/env python3
"""OFFLINE physical FSK-only IQ checkpoint against an independent waveform.

Reads archived real RTL-SDR U8 IQ from the old conducted K6 experiments;
performs pilot-only acquisition with the existing native-chip decoder and
then compares a fresh, independently specified ideal FSK target in the
pilot-frozen heldout complex-IQ scorer. Captures here did NOT have APC
amplitude modulation. NOT a simultaneous polar-IQ RF demonstration.

No connected SDR, live serial, radio, SPI, firmware, TX or network access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

from k6_fsk_pdm_chip_decode import RAW_FS, IQ_FS, decode, validate_manifest, _rf_to_baseband
from k6_iq_holdout_verify import verify
from k6_polar_known_target import make_target

FS = 96_000
PRE_ROLL_S = .002
POST_ROLL_S = .003
PILOT_BITS = 320


def _read_archive(path: Path) -> tuple[np.ndarray,dict,str]:
    raw = np.load(path, mmap_mode="r", allow_pickle=False)
    if (raw.ndim != 1 or raw.dtype != np.uint8 or len(raw) % 2 or
            not int(2 * 1_000_000) <= len(raw) <= int(2 * 7_000_000)):
        raise ValueError(f"invalid archived U8 IQ source: {path}")
    meta = json.loads(path.with_suffix(".json").read_text())
    tx = meta.get("module_result", {})
    rest = meta.get("restored", {})
    if (meta.get("sample_rate_hz") != RAW_FS or
            meta.get("target_hz") != 433_500_000 or
            meta.get("center_hz") != 433_750_000 or
            not meta.get("dummy_load_user_confirmed") or
            not meta.get("modem_frame_confirmed") or
            meta.get("tx_requests") != 1 or
            not meta.get("vfo_restored") or
            not tx.get("returned") or tx.get("result") != 64 or
            rest.get("transmitting") is not False):
        raise ValueError(f"capture metadata does not identify an archived restored 64-word bench frame: {path}")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    return raw,meta,digest


def score_capture(raw: np.ndarray, manifest: dict, *, max_cfo_hz=300.,
                  deviation_hz=1205.23, max_pilot_evm=.995) -> tuple[dict,dict]:
    """Score real FSK against the ideal binary command; NEVER joint FSK/APC."""
    validate_manifest(manifest)
    target,target_report = make_target(manifest, sample_rate=FS,
                                       deviation_hz=deviation_hz,
                                       amplitude_pattern="constant")
    acquisition = decode(raw, manifest)
    if (not acquisition["known_pilot_acquired"] or
            not acquisition["training_only_acquisition"] or
            acquisition["native_baud_estimate"] != 4800):
        raise ValueError("known native FSK pilot lock not independently established")
    # Existing decoder's downconversion is known for this *specific* archive
    # family. Resampling 128k->96k retains its same 0.68-s time origin.
    baseband, start_s = _rf_to_baseband(raw)
    at_96k = resample_poly(baseband,3,4,window=("kaiser",10))
    bit0_sample = (acquisition["first_fifo_bit_time_s"] - start_s) * FS
    slice_begin = int(round(bit0_sample - PRE_ROLL_S * FS))
    required = len(target) + round((PRE_ROLL_S + POST_ROLL_S)*FS)
    if slice_begin < 0 or slice_begin + required > len(at_96k):
        raise ValueError("nominal FSK payload outside archived SDR observation")
    observation = at_96k[slice_begin:slice_begin+required]
    # Integer sub-sample uncertainty is solely corrected by PILOT alignment.
    metrics,arrays = verify(target,observation,fs=FS,
                            pilot_samples=PILOT_BITS*(FS//4800),
                            max_lag_samples=int(round((PRE_ROLL_S+.001)*FS)),
                            max_cfo_hz=max_cfo_hz,
                            maximum_pilot_evm=max_pilot_evm)
    result = {
        "analysis": "FSK-only complex IQ input versus independent ideal FSK reference",
        "source_rf_provenance_verifiable_from_iq_alone": False,
        "physical_joint_fsk_apc_demonstrated": False,
        "apc_amplitude_modulation_in_this_capture": False,
        "requires_independent_physical_tx_metadata": True,
        "capture_metadata_cannot_attest_the_exact_transmitted_fifo_payload": True,
        "observed_known_pilot_match": acquisition["pilot_correlation"],
        "native_chip_heldout_errors": acquisition["native_heldout_bit_errors"],
        "native_chip_heldout_total": acquisition["native_heldout_bits"],
        "native_fifo_bit0_time_s": acquisition["first_fifo_bit_time_s"],
        "native_chip_clock_hz": acquisition["native_baud_estimate"],
        "pilot_half_separation_hz": acquisition["calibration_half_separation_hz"],
        "rx_preset_nominallo_hz": 280500,
        "target_definition": target_report,
        "downconversion": "archived RTL U8 IQ, +280500 Hz, 1.024 MS/s -> 128k -> 96k, receive FIR only",
        "segment_start_s": start_s + slice_begin/FS,
        "complex_iq_result": metrics,
        "warnings": [
            "The real archived transmitter emitted ONLY binary FSK; no APC modulation was present",
            "Target is an independent ideal instantaneous ±deviation model, not an experimentally calibrated radio RF model",
            "Pilot-constrained acquisition and one CFO/gain adjustment; no heldout optimization",
            "Capture provenance is archived experimental metadata, not cryptographic proof of wire payload or emissions calibration",
            "Amplitude and carrier effects include uncalibrated air-coupled RTL-SDR receive chain",
            "The existing FSK decoder's native-bit heldout score is different from a phase-coherent IQ fidelity score",
        ]
    }
    return result,arrays


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",type=Path,required=True)
    p.add_argument("--iq",type=Path,nargs="+",required=True)
    p.add_argument("--deviation-hz",type=float,default=1205.23)
    p.add_argument("--max-cfo-hz",type=float,default=300.)
    p.add_argument("--max-pilot-evm",type=float,default=.995)
    p.add_argument("--out",type=Path,required=True)
    args = p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite physical archive report")
    try:
        data = args.manifest.read_bytes()
        manifest=json.loads(data)
        validate_manifest(manifest)
        if manifest.get("kind") != "8fsk_pdm300":
            raise ValueError("this scorer only accepts the archived PDM-300 manifest")
        results=[]
        for path in args.iq:
            raw,meta,digest=_read_archive(path)
            case,_=score_capture(raw,manifest,max_cfo_hz=args.max_cfo_hz,
                                 deviation_hz=args.deviation_hz,
                                 max_pilot_evm=args.max_pilot_evm)
            case["source_iq"]=str(path)
            case["source_npy_sha256"]=digest
            case["source_firmware_id"]=meta.get("module_capabilities",{}).get("build_id")
            results.append(case)
        report={
            "project":"Flux", "study":"archived real FSK-only waveform fidelity",
            "no_radio_or_sdr_control":True,
            "rf_transmission_initiated_by_this_tool":False,
            "physical_simultaneous_fsk_apc_demonstrated":False,
            "source_manifest":str(args.manifest),
            "source_manifest_sha256":hashlib.sha256(data).hexdigest(),
            "cases":results,
        }
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(report,indent=2)+"\n")
        for row in results:
            m=row["complex_iq_result"]
            print(Path(row["source_iq"]).name,
                  "pilot EVM", round(m["pilot_metrics"]["normalized_evm"],4),
                  "heldout EVM", round(m["heldout_metrics"]["normalized_evm"],4),
                  "corr",round(m["heldout_metrics"]["coherent_correlation"],4))
        print("Saved",args.out)
    except (OSError,ValueError,TypeError,KeyError) as exc:
        p.error(str(exc))


if __name__=="__main__":
    main()
