#!/usr/bin/env python3
"""Decode recorded, preexisting SDR IQ into private FDV1 guard/voice JSON.

OFFLINE file processing ONLY: no SDR device opening, TX, PTT, SPI, flash or
serial port. This is the proposed V1 session-control PHY, not RF qualified.
Raw packet bytes and captured speech MUST remain private mode 0600.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.k6_dv_guard33_session import receive_iq
from tools.k6_fsk_guardword_rx import read_iq
from tools.k6_fsk_guard33_mic_playout import private_write


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--iq",required=True,type=Path,
                   help="existing private IQ file (.npy or SDR capture)")
    p.add_argument("--out-private-dir",required=True,type=Path)
    p.add_argument("--prefix",required=True)
    p.add_argument("--duration",type=float,default=2.4)
    p.add_argument("--offset-hz",type=float,default=280500.)
    a=p.parse_args()
    folder=a.out_private_dir
    if not folder.is_dir() or folder.stat().st_mode & 0o077:
        p.error("an existing mode-0700 private directory is required")
    if not a.prefix or "/" in a.prefix or a.prefix in (".",".."):
        p.error("prefix must be a plain nonempty filename")
    out=folder/(a.prefix+".json")
    if out.exists():
        p.error("refuse overwrite of original RX evidence")
    with a.iq.open("rb") as f:
        digest=hashlib.file_digest(f,"sha256").hexdigest()
    result=receive_iq(read_iq(a.iq,duration=a.duration,offset=a.offset_hz))
    result.update(iq_sha256=digest,rf_hardware_access=False,
                  codec_packet_bytes_private=True,
                  not_a_real_transmit_or_receive_on_K6=True)
    private_write(out,(json.dumps(result,indent=2)+"\n").encode())
    good=[r for r in result["frames"] if r["valid"]]
    print("SDR IQ OFFLINE receiver: frames",result["frames_detected"],
          "CRC-qualified",len(good),
          "voice",sum(x["packet_type"]=="voice" for x in good),
          "FDV1 controls",sum(x["packet_type"]=="control" for x in good),
          "private output:",out)


if __name__=="__main__":
    main()
