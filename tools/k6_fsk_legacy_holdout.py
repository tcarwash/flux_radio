#!/usr/bin/env python3
"""Reproduce training-prefix / held-out-tail decode on archived K6 RF matrix.

Offline only; reads previous raw SDR IQ recordings and known FIFO payload
generation. Uses training prefix for acquisition but NEVER for tail testing.
No SDR device, radio, serial, hardware TX, or flashing.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from k6_rf_matrix_decode import payload
from k6_fsk_lab_decode import decode_archive

RECORDINGS = (
    (1200,128),
    (2400,128),
    (3600,64),
    (4800,64),
)


def make_legacy_truth(baud: int, nbytes: int) -> dict:
    if baud not in (1200,2400,3600,4800) or nbytes not in (64,128):
        raise ValueError("unsupported archived FSK matrix baud/length")
    truth=payload(nbytes)
    raw=np.packbits(truth).tobytes()
    return {
        "native_baud":baud,
        "payload_bits":int(truth.size),
        "payload_bytes_hex":raw.hex(),
        "payload_bits_msb_each_byte_lower_byte_first":
            "".join(map(str,truth.tolist())),
        "payload_sha256_le_words":hashlib.sha256(raw).hexdigest(),
        "source":"tools/k6_rf_matrix_decode.py payload() (legacy FSK matrix)",
    }


def replay(data_dir: Path, *, train_bits=256) -> dict:
    index_path=data_dir/"decoded.json"
    matrix={row["capture"]:row for row in json.loads(index_path.read_text())}
    results=[]
    for baud,nbytes in RECORDINGS:
        name=f"apc76-baud{baud}-len{nbytes}"
        old=matrix[name]
        if old["baud"]!=baud or old["nbytes"]!=nbytes:
            raise ValueError("matrix metadata mismatches requested capture")
        observed=decode_archive(data_dir/(name+".npy"),
                               make_legacy_truth(baud,nbytes),
                               train_bits=train_bits,duration=2.5)
        row={
            "capture":name,
            "baud":baud,
            "original_assisted_total_errors":old["errors"],
            "original_assisted_total_bits":old["bits"],
            "original_assisted_correlation":old["correlation"],
            "capture_sha256":observed["capture_sha256"],
            "train_prefix_bits":train_bits,
            "recovered_without_saved_frame_start_time":True,
            "decoded":observed["output"],
        }
        results.append(row)
    return {
        "project":"Flux native FSK research",
        "study":"FSK-05 decoder held-out validation using pre-existing hardware RF data",
        "radio_access":False,
        "transmission_initiated":False,
        "fully_blind_decode":False,
        "uses_known_training_bits_for_sync":True,
        "tail_reserved_from_sync_and_threshold":True,
        "data_directory":str(data_dir),
        "cases":results,
        "limitations":[
            "Original recordings were made antenna-to-antenna; not a calibrated conducted setup.",
            "Training-sequence acquisition uses known bits and a prior receiver tuning offset.",
            "A zero-error held-out segment is not a guaranteed BER or proof of RF phase continuity.",
            "RFIC output deviation, absolute power and spectral compliance remain unmeasured."
        ]
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir",required=True,type=Path)
    p.add_argument("--out",required=True,type=Path)
    p.add_argument("--training-bits",type=int,default=256)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite saved analysis")
    try:
        report=replay(args.data_dir,train_bits=args.training_bits)
    except (KeyError,ValueError,OSError,TypeError) as e:
        p.error(str(e))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    for r in report["cases"]:
        d=r["decoded"]
        print(f"{r['baud']} bit/s: lock={d['locked']}, "
              f"train={d.get('training_bit_errors')} / {d['training_bits']}, "
              f"heldout={d.get('held_out_tail_errors')} / "
              f"{d.get('held_out_tail_bits')}, "
              f"coherence={d['best_training_correlation']:.3f}")
    print("Saved offline report:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
