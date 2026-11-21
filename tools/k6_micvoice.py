#!/usr/bin/env python3
"""Decode Flux experimental microphone IMA-ADPCM recording from a serial log.

The recorder is disabled by default and requires an explicitly compiled,
installed lab module. This parser never accesses or transmits on a radio.
"""
from __future__ import annotations

import argparse
import json
import re
import wave
from pathlib import Path

import numpy as np

STEPS = (
    7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,
    50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,
    253,279,307,337,371,408,449,494,544,598,658,724,796,876,
    963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,
    2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,
    7854,8630,9493,10442,11487,12635,13899,15289,16818,18500,
    20350,22385,24623,27086,29794,32767,
)
INDEX = (-1,-1,-1,-1,2,4,6,8)
ROW = re.compile(r"p ([0-9a-f]{4}) ((?:[0-9a-f]{2})+)$")
FOOT = re.compile(
    r"micrec p ticks=([0-9a-f]{4}) n=([0-9a-f]{4}) w=([0-9a-f]{2}) "
    r"rest=([0-9a-f]{4}) over=([0-9a-f]{4})$"
)
MIC_STATE = re.compile(r"mickrec keyed 30=([0-9a-f]{4}) 36=([0-9a-f]{4}) now=([0-9a-f]{4})$")


def decode_adpcm(data: bytes) -> np.ndarray:
    pred, index = 0, 0
    samples = np.empty(2 * len(data), dtype=np.int16)
    for n, byte in enumerate(data):
        for half, nibble in enumerate((byte & 15, byte >> 4)):
            step = STEPS[index]
            delta = (step >> 3)
            if nibble & 1:
                delta += step >> 2
            if nibble & 2:
                delta += step >> 1
            if nibble & 4:
                delta += step
            pred += -delta if nibble & 8 else delta
            pred = max(-32768,min(32767,pred))
            index = max(0,min(88,index + INDEX[nibble & 7]))
            samples[2*n + half] = pred
    return samples


def _lines(log: str) -> list[str]:
    out = []
    for raw in log.splitlines():
        line = raw.strip()
        marker = "K6 MOD say "
        if marker in line:
            line = line.split(marker,1)[1]
        out.append(line)
    return out


def parse_log(text: str) -> tuple[bytes, dict]:
    lines = _lines(text)
    starts = [i for i,v in enumerate(lines) if v.startswith("mickrec start ")]
    if len(starts) != 1:
        raise ValueError("expected exactly one microphone recording header")
    lines = lines[starts[0]:]
    ends = [i for i,v in enumerate(lines) if v=="mickrec done"]
    if len(ends) != 1:
        raise ValueError("missing or ambiguous recording end marker")
    lines = lines[:ends[0]+1]
    keys = [MIC_STATE.fullmatch(v) for v in lines if v.startswith("mickrec keyed ")]
    if len(keys) != 1 or not keys[0]:
        raise ValueError("missing transmit-mode microphone state")
    s30, r36, now = [int(v,16) for v in keys[0].groups()]
    if not now & 0x0204 == 0x0204:
        raise ValueError("microphone ADC/AF DAC bits were not confirmed")
    footer = [FOOT.fullmatch(v) for v in lines if v.startswith("micrec p ")]
    if len(footer) != 1 or not footer[0]:
        raise ValueError("invalid or missing capture footer")
    ticks,n,w,rest,over = [int(v,16) for v in footer[0].groups()]
    if n < 2 or n > 26000 or n % 2 or not ticks or w < 1:
        raise ValueError("invalid sample count or capture timing")
    data=bytearray()
    for line in lines:
        if not line.startswith("p "):
            continue
        match=ROW.fullmatch(line)
        if not match:
            raise ValueError("malformed sample row")
        if int(match.group(1),16)!=len(data):
            raise ValueError("missing, duplicate, or out-of-order ADPCM bytes")
        data.extend(bytes.fromhex(match.group(2)))
        if len(data)>n//2:
            raise ValueError("extra ADPCM data")
    if len(data) != n//2:
        raise ValueError("incomplete ADPCM capture")
    return bytes(data), dict(
        samples=n, bytes=len(data), measured_sample_rate_hz=n*10000/ticks,
        timing_ticks_100us=ticks, turns=w, baseline_counts=rest,
        clipping_events=over, reg30_before=s30, reg30_mic_enabled=now,
        reg36_after=r36, bitrate_bps=n*4*10000/ticks,
    )


def export_log(src: Path, out: Path) -> dict:
    data, meta = parse_log(src.read_text(encoding="utf-8",errors="replace"))
    samples=decode_adpcm(data)
    peak=int(np.max(np.abs(samples.astype(np.int32))))
    if peak:
        wav = np.round(samples.astype(np.float64) * (32767.0 * 0.8/peak)).astype(np.int16)
    else:
        wav = samples.copy()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".adpcm").write_bytes(data)
    np.savez_compressed(out.with_suffix(".npz"), adpcm=np.frombuffer(data,dtype=np.uint8),
                        decoded_unscaled=samples)
    with wave.open(str(out.with_suffix(".wav")),"wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(round(meta["measured_sample_rate_hz"]))
        w.writeframes(wav.tobytes())
    meta["decoded_peak"] = peak
    out.with_suffix(".json").write_text(json.dumps(meta,indent=2)+"\n")
    return meta


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log",type=Path)
    parser.add_argument("--out",type=Path,required=True,help="output prefix")
    args=parser.parse_args()
    print(json.dumps(export_log(args.log,args.out),indent=2))


if __name__=="__main__":
    main()
