#!/usr/bin/env python3
"""Build reproducible BLIND receiver-audio listening trials from saved previews.

No radio or audio driver use. Labels A-E conceal variants for the listener;
the private answer key is written separately and must stay unread until scored.
Speech fidelity and intelligibility need human judgments, not noise-RMS claims.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.io import wavfile
from tools.k6_sotto_fidelity_previews import FS,VARIANTS


def build(source_dir:Path,dest:Path,*,start_s:float=5.0,seconds:float=8.0,seed:int=20261127):
    if not dest.is_dir():raise ValueError("destination directory absent")
    if not 0<=start_s<=20 or not .5<=seconds<=20 or not 0<=seed<=2**32-1:
        raise ValueError("invalid bounded excerpt or seed")
    files=[source_dir/(v+".wav") for v in VARIANTS]
    original=None
    for f in files:
        fs,arr=wavfile.read(f)
        if fs!=FS or arr.dtype!=np.int16 or arr.ndim!=1:
            raise ValueError(f"invalid source preview {f.name}")
        if original is None:original=len(arr)
        if len(arr)!=original:raise ValueError("preview length mismatch")
    a=round(start_s*FS);b=a+round(seconds*FS)
    if b>original:raise ValueError("excerpt exceeds source length")
    for letter in "ABCDE":
        if (dest/(letter+".wav")).exists():
            raise FileExistsError("blind label exists")
    for file in ("answer_key.json","listening_instructions.txt"):
        if (dest/file).exists():raise FileExistsError("blind session metadata exists")
    random=np.random.default_rng(seed)
    order=[VARIANTS[int(i)] for i in random.permutation(len(VARIANTS))]
    key={"blind_trial":True,"source_preview_directory":source_dir.name,
        "source_manifest_sha256":hashlib.sha256((source_dir/"manifest.json").read_bytes()).hexdigest(),
        "excerpt_start_s":start_s,"excerpt_duration_s":seconds,"mapping":{}}
    for i,variant in enumerate(order):
        label="ABCDE"[i]
        rate,data=wavfile.read(source_dir/(variant+".wav"))
        p=dest/(label+".wav")
        with p.open("xb") as f:
            from io import BytesIO
            buffer=BytesIO()
            wavfile.write(buffer,FS,data[a:b].copy())
            f.write(buffer.getvalue())
        key["mapping"][label]=variant
    with (dest/"answer_key.json").open("x") as f:
        f.write(json.dumps(key,indent=2,sort_keys=True)+"\n")
    with (dest/"listening_instructions.txt").open("x") as f:
        f.write("BLIND SOTTO AUDIO COMPARISON, same speech excerpt, RMS-level matched\n"
                "Listen to A.wav through E.wav at the SAME playback volume.\n"
                "Rank them most to least intelligible; optionally rate background hiss,\n"
                "consonant clarity, naturalness, and musical artifacts (1..5).\n"
                "Do not read answer_key.json until scores are fixed.\n"
                "There is NO ground-truth clean speech reference, so rankings are subjective.\n")
    return {"letters":list("ABCDE"),"excerpt_duration_s":seconds,
            "answer_key_file":"answer_key.json", "blind":True}


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir",type=Path,required=True)
    ap.add_argument("--output-dir",type=Path,required=True)
    ap.add_argument("--start-s",type=float,default=5)
    ap.add_argument("--seconds",type=float,default=8)
    args=ap.parse_args()
    print(json.dumps(build(args.source_dir,args.output_dir,start_s=args.start_s,
                           seconds=args.seconds),indent=2))

if __name__=="__main__":main()
