#!/usr/bin/env python3
"""Read-only empirical K6 TX/GP0 calibration report, no RF/serial access."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

if __package__ in (None,""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sim.k6emu.rf.physical_calibration import (
    FIXTURE,Gp0MicCalibration,PhysicalTxCalibration,TxCondition,load_calibration,
)


def report(fixture:Path=FIXTURE) -> dict:
    d=load_calibration(fixture)
    tx=PhysicalTxCalibration(d['transmit'])
    mic=Gp0MicCalibration(d['microphone'])
    groups={}
    for r in tx.rows:
        family=r['family']
        if family not in groups:
            groups[family]=tx.assess(TxCondition.from_case(r))
    return {
        "schema":1,"source_fixture":d['name'],
        "measurement_only":True,"is_validated_silicon_model":False,
        "physical_runs":{"transmit":len(d['transmit']),"microphone":len(d['microphone'])},
        "tx_profiles":groups,
        "holdouts":tx.leave_one_out(),
        "gp0_pcm":{k:mic.evidence('pcm',k) for k in ('off','1khz','2khz')},
        "gp0_dma":{k:mic.evidence('dma',k) for k in ('off','1khz','2khz')},
        "gp0_dma_ring":{"pcm":mic.dma_wrap_us(measurement='pcm'),
                        "dma":mic.dma_wrap_us(measurement='dma')},
        "limits":[
            "No absolute TX startup guard threshold inferred: a 0-ms trial succeeded once.",
            "USB monitor failures belong to acquisition, not transmitter corruption.",
            "GPIO/PB1 GP0 capture frequency response not calibrated; only three stimuli measured.",
            "PCM artifacts contain only 32-ms window summaries, not raw 8-kHz waveforms.",
            "Calibration annotations never silently modify the IQ or force CRC outcomes.",
            "This report does not model Sotto codec execution or concurrent DMA ownership.",
        ]}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path,default=FIXTURE)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    d=report(a.fixture)
    s=json.dumps(d,indent=2,sort_keys=True)+'\n'
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(s)
    else:
        print(s,end='')

if __name__=='__main__':main()
