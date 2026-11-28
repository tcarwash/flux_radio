#!/usr/bin/env python3
"""Run two compiled Cortex-M0 FSK fixtures over a virtual RF channel; sweep parameters."""
import argparse
from collections import Counter
import csv
from dataclasses import asdict
from itertools import product
import json
import math
from pathlib import Path
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/"tests")]
import numpy as np
from k6_live_emu import Emu
from modem import pager
from sim.k6emu.rf.backend import RFBackend
from sim.k6emu.rf.virtual_link import Channel, VirtualAir, AirSource, VirtualTxBackend
from tools.k6_fsk_emulator_replay import fixture, receive

HZ = 433500000
PROFILE = pager.profile("L","1200",depth=1,parity=False,short_last=True)


def transmit(air, frames, *, rate, tune, gap_us, lead_us=30000):
    n = len(frames[0]) if frames else 0
    if (not 2<=n<=128 or n%2 or any(len(x)!=n for x in frames)
            or len(frames)*n>4080 or not 0<=gap_us<=1000000 or not 1000<=lead_us<=1000000):
        raise ValueError("invalid TX frame count, length or timing")
    code,entry=fixture("tx")
    b=VirtualTxBackend(air,rate,tune)
    try:
        duration=lead_us/1e6+len(frames)*((20+n)*8/(4800*(1+air.channel.clock_ppm/1e6))+gap_us/1e6+.02)+.5
        emu=Emu(code,entry,np.zeros(1,dtype=np.uint8),0,1,max_s=duration,
                rf_backend=b,rf_hz=tune,level=2)
        emu.mu.mem_write(emu.scr,struct.pack("<4I",len(frames),n,gap_us,lead_us)+b"".join(frames))
        result=emu.run()
        if result is None or result[0]!=len(frames) or b.completed!=len(frames):
            raise RuntimeError(f"TX fixture failed: {result}, {b.tx_status()}")
        return dict(module_return=result[0],stack_bytes=result[1],
                    virtual_ms=round(emu.cyc/96000,3),**b.tx_status())
    finally:
        b.close()


def score(expected, received, guard):
    # The only recovery permitted is a CRC-valid L64 window at offset 0 or
    # (explicit guarded profile only) 2. Never feed expected bytes into RX.
    valid=[]
    offsets=Counter()
    for frame in received:
        candidates=[(o,frame[o:o+64]) for o in ((0,2) if guard else (0,))]
        good=[(o,x) for o,x in candidates if PROFILE.unpack(x) is not None]
        if len(good)==1:
            o,x=good[0]; valid.append(x);offsets[o]+=1
    original=[x[:64] for x in expected]
    exact=sum((Counter(original)&Counter(valid)).values())
    raw_exact=sum((Counter(expected)&Counter(received)).values())
    aligned_errors=(sum(sum((a^b).bit_count() for a,b in zip(x,y)) for x,y in zip(expected,received))
                    if len(expected)==len(received) else None)
    return dict(received=len(received),crc_valid=len(valid),exact_packets=exact,
                raw_exact_frames=raw_exact,raw_aligned_bit_errors=aligned_errors,
                recovered_offset_0=offsets[0],recovered_offset_2=offsets[2],
                packet_delivery=exact/len(expected))


def trial(channel=Channel(), *, packets=4, rate=240000, threshold=4, poll_us=1000,
          gap_us=12000, lead_us=30000, guard=False, tx_hz=HZ, rx_hz=HZ):
    if not 1<=packets<=32 or not 48000<=rate<=2000000:
        raise ValueError("packets 1–32, rate 48k–2M")
    if abs(tx_hz-rx_hz+channel.offset_hz)+channel.deviation_hz>=rate/2:
        raise ValueError("signal outside sampled Nyquist window")
    # Real L64 framing/whitening/CRC, synthetic codec bytes (not valid speech).
    rng=np.random.default_rng(20261124)
    payloads=PROFILE.pack(rng.bytes(packets*8*6),msg_id=73)
    expected=[p+(b"\x00\x00" if guard else b"") for p in payloads]
    air=VirtualAir(channel)
    start=time.perf_counter()
    tx=transmit(air,expected,rate=rate,tune=tx_hz,gap_us=gap_us,lead_us=lead_us)
    duration=air.bursts[-1].end+max(.04,poll_us/1e6+.02)
    b=RFBackend(AirSource(air,rate,rx_hz))
    try:
        frames,rx=receive(b,tune=rx_hz,duration=duration,frame_bytes=len(expected[0]),
                          threshold=threshold,poll_us=poll_us)
    finally:
        b.close()
    return dict(**asdict(channel),packets=packets,rate=rate,threshold=threshold,
                poll_us=poll_us,gap_us=gap_us,lead_us=lead_us,guard=guard,tx_hz=tx_hz,rx_hz=rx_hz,
                **score(expected,frames,guard),rx_overruns=rx["fsk"]["overruns"],
                rx_underflows=rx["fsk"]["underflows"],tx_completed=tx["completed"],
                tx_stack_bytes=tx["stack_bytes"],rx_stack_bytes=rx["stack_bytes"],
                virtual_seconds=round(duration,6),wall_seconds=round(time.perf_counter()-start,4))


def summarize(rows):
    """Rank configurations by worst-case delivery across noise seeds/start phases."""
    groups={}
    axes=("deviation_hz","offset_hz","clock_ppm","cn_db","signal_dbm","prefix_every",
          "threshold","poll_us","gap_us","guard","rate","packets","tx_hz","rx_hz")
    for row in rows:
        key=tuple(row[k] for k in axes)
        groups.setdefault(key,[]).append(row)
    result=[]
    for key,group in groups.items():
        deliveries=[r["packet_delivery"] for r in group]
        result.append(dict(zip(axes,key),trials=len(group),
                           worst_delivery=min(deliveries),mean_delivery=sum(deliveries)/len(deliveries),
                           max_rx_overruns=max(r["rx_overruns"] for r in group),
                           max_rx_underflows=max(r["rx_underflows"] for r in group)))
    return sorted(result,key=lambda r:(-r["worst_delivery"],-r["mean_delivery"],
                                      r["max_rx_overruns"],r["max_rx_underflows"]))


def values(kind):
    def parse(s):
        try:
            out=[kind(x) for x in s.split(",")]
            if not out or not all(math.isfinite(x) for x in out):
                raise ValueError
            return out
        except ValueError:
            raise argparse.ArgumentTypeError("expected comma-separated finite numbers")
    return parse


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name,kind,default in [
        ("deviation",float,[1800]),("offset",float,[0]),("clock-ppm",float,[0]),
        ("cn",float,[25]),("signal-dbm",float,[-70]),("seed",int,[1]),
        ("threshold",int,[4]),("poll-us",int,[1000]),("gap-us",int,[12000]),
        ("prefix-every",int,[0]),("lead-us",int,[30000])]:
        p.add_argument("--"+name,type=values(kind),default=default,help="comma-separated sweep values")
    p.add_argument("--packets",type=int,default=4)
    p.add_argument("--rate",type=int,default=240000)
    p.add_argument("--guard",choices=("off","on","both"),default="off")
    p.add_argument("--json",type=Path,help="save full results")
    p.add_argument("--csv",type=Path,help="save flat sweep table")
    p.add_argument("--summary-json",type=Path,help="rank groups by worst delivery across seeds/start phases")
    p.add_argument("--max-trials",type=int,default=256)
    a=p.parse_args()
    guards=[False,True] if a.guard=="both" else [a.guard=="on"]
    axes=[a.deviation,a.offset,a.clock_ppm,a.cn,a.signal_dbm,a.seed,a.threshold,a.poll_us,a.gap_us,a.prefix_every,a.lead_us,guards]
    n=math.prod(map(len,axes))
    if n>min(a.max_trials,4096):
        p.error(f"{n} trials exceed limit; narrow sweep or raise --max-trials")
    rows=[]
    for dev,off,ppm,cn,dbm,seed,threshold,poll,gap,prefix,lead,guard in product(*axes):
        row=trial(Channel(dev,off,ppm,cn,dbm,seed,prefix),packets=a.packets,rate=a.rate,
                  threshold=threshold,poll_us=poll,gap_us=gap,lead_us=lead,guard=guard)
        rows.append(row)
        print(f"{len(rows):3}/{n} dev={dev:g} off={off:g} ppm={ppm:g} C/N={cn:g} "
              f"dBm={dbm:g} seed={seed} threshold={threshold} poll={poll} gap={gap} "
              f"lead={lead} prefix={prefix} guard={int(guard)} delivery={row['exact_packets']}/{a.packets} "
              f"raw={row['raw_exact_frames']} overrun={row['rx_overruns']} ({row['wall_seconds']:.2f}s)",flush=True)
    if a.summary_json:
        a.summary_json.write_text(json.dumps(summarize(rows),indent=2)+"\n")
    if a.json:
        a.json.write_text(json.dumps(rows,indent=2)+"\n")
    if a.csv:
        with a.csv.open("w",newline="") as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


if __name__=="__main__":
    main()
