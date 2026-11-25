#!/usr/bin/env python3
"""Offline hypothesis model of API29 native FSK TX-done, PA and FIFO cutoffs.

Produces no RF, serial I/O, flash, or bench actions. Source-anchored only:
the FD6818B timing/IRQ semantics are deliberately *not* assumed known.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from modem import pager
HOST=ROOT/"firmware/k6-aura/sotto-api29-async-fsk.rs.inc"
LIVE=ROOT/"c/k6/mod/sotto_live_session.h"
GUARD=bytes.fromhex("5aa5")
BAUD=4800
BYTES=66
FULL_MS=BYTES*8*1000/BAUD
PREFIX=bytes.fromhex("55"*16+"076b3e36")


def source_contract():
    """Refuse to silently model changed production constants/order."""
    h=HOST.read_text()
    s=LIVE.read_text()
    expected=(
        "0x4100", "n!=33", "words.add(32).read()!=0xa55a",
        "for i in 0..33", "sotto_tx29_write(0x5f,words.add(i).read())",
        "mod_tx_pa(1)", "sotto_tx29_write(0x59,0x08f8)",
        "mod_fd_read(0x0c)&1==0", "app.radio.cw_carrier_off(syst)",
    )
    missing=[t for t in expected if t not in h]
    if missing:raise ValueError(f"API29 source contract changed: {missing}")
    if h.index("for i in 0..33")>h.index("mod_tx_pa(1)") or (
        h.index("mod_tx_pa(1)")>h.index('sotto_tx29_write(0x59,0x08f8)')):
        raise ValueError("API29 expected FIFO -> PA -> FSK enable order changed")
    for t in ("out[32]=DV_GUARD33_GUARD_WORD;", "raw[0]=0xa9u;", "SL_FIRST 112u"):
        if t not in s:raise ValueError(f"Live packet contract changed: {t}")
    return dict(host_sha256=hashlib.sha256(HOST.read_bytes()).hexdigest(),
                live_sha256=hashlib.sha256(LIVE.read_bytes()).hexdigest(),
                declared_length_register="0x4100",
                expected_fifo_words=33,guard_le="0xA55A",
                expected_physical_bytes=66)


def test_vector():
    """Public known-voice-slot fixture, deliberately NOT an actual mic recording."""
    codec=bytes(range(48))
    pkt=pager.profile("L","1200",depth=1,parity=False,short_last=True).pack(
        codec,msg_id=112)[0]
    if len(pkt)!=64:raise AssertionError("bad L64 fixture")
    return pkt+GUARD


def first_poll_after(t,period,phase=0.):
    if t is None:return None
    if period<=0 or not 0<=phase<period:raise ValueError("invalid poll schedule")
    return round(phase+max(0,math.ceil((t-phase)/period))*period,6)


def run_case(kind,*,poll_ms=5.,phase_ms=0.,irq_at_ms=None,
             pa_drop_at_ms=None,fifo_capacity=33,post_irq_hold_ms=0.):
    """Event-driven mathematical fault injection, *not* an FD6818B silicon emulator.

    Time zero is first payload bit after public preamble/sync.
    Synthetic early IRQ and PA drop are inputs, NOT actual measured events.
    """
    source_contract()
    physical=test_vector()
    if kind not in ("nominal","early_done","early_pa_drop","fifo_limit"):
        raise ValueError("unknown fault")
    if kind=="nominal":
        irq_at_ms=FULL_MS if irq_at_ms is None else irq_at_ms
    elif kind=="early_done":
        irq_at_ms=90. if irq_at_ms is None else irq_at_ms
    elif kind=="early_pa_drop":
        irq_at_ms=FULL_MS if irq_at_ms is None else irq_at_ms
        pa_drop_at_ms=92. if pa_drop_at_ms is None else pa_drop_at_ms
    else:
        irq_at_ms=FULL_MS if irq_at_ms is None else irq_at_ms
        fifo_capacity=32 if fifo_capacity==33 else fifo_capacity
    if not 1<=fifo_capacity<=33:raise ValueError("invalid FIFO capacity")
    if post_irq_hold_ms<0 or post_irq_hold_ms>50:raise ValueError("bounded hold")
    irq_seen=first_poll_after(irq_at_ms,poll_ms,phase_ms)
    cleanup=irq_seen+post_irq_hold_ms
    if pa_drop_at_ms is not None:cleanup=min(cleanup,pa_drop_at_ms)
    fifo_bytes=fifo_capacity*2
    # Count actual full RF bit periods preceding an externally injected cutoff.
    bit_end=min(BYTES*8, fifo_bytes*8, max(0,int(cleanup*BAUD/1000)))
    tx_bytes=bit_end//8
    first_incomplete=tx_bytes if tx_bytes<BYTES else None
    crc_complete=bit_end>=64*8
    guard_complete=bit_end>=66*8
    return dict(kind=kind, hypothetical=True,
                frame= dict(payload_sha256=hashlib.sha256(physical).hexdigest(),
                            length_bytes=len(physical),start_magic="A9",
                            msg_id=112,raw_guard=physical[-2:].hex(),
                            data_crc_valid=pager.profile("L","1200",depth=1).unpack(physical[:64]) is not None),
                events=dict(poll_ms=poll_ms,phase_ms=phase_ms,irq_at_ms=irq_at_ms,
                            irq_first_observed_ms=irq_seen,earliest_pa_or_fsk_off_ms=round(cleanup,4),
                            pa_drop_at_ms=pa_drop_at_ms,preloaded_fifo_words=fifo_capacity,
                            expected_fifo_words=33,model_hold_ms=post_irq_hold_ms),
                predicted=dict(first_missing_byte=first_incomplete,
                               transmitted_whole_bytes=tx_bytes,
                               transmitted_bits=bit_end,
                               air_done_ms=round(FULL_MS,4),
                               crc_bytes_fully_transmitted=crc_complete,
                               guard_bytes_fully_transmitted=guard_complete,
                               strict_delivery=crc_complete and guard_complete))


def grid():
    """A fixed small matrix, not a parameter optimizer against physical IQ."""
    return [run_case("nominal",poll_ms=5),
            run_case("early_done",irq_at_ms=90,poll_ms=5,phase_ms=0),
            run_case("early_done",irq_at_ms=90,poll_ms=7,phase_ms=3),
            run_case("early_done",irq_at_ms=100,poll_ms=7,phase_ms=3),
            run_case("early_pa_drop",pa_drop_at_ms=90,poll_ms=5),
            run_case("fifo_limit",fifo_capacity=32,poll_ms=5)]


def render_iq(case,fs=128000):
    """Synthetic 2FSK complex IQ with an imposed PA cutoff, not a capture."""
    import numpy as np
    payload=test_vector()
    all_bits=np.unpackbits(np.frombuffer(PREFIX+payload,dtype=np.uint8))
    cutoff=case['predicted']['transmitted_bits']
    cut_at=len(PREFIX)*8+cutoff
    lead_ms=25.
    total_s=lead_ms/1000+(len(all_bits)/BAUD)+.025
    t=np.arange(int(total_s*fs),dtype=np.float64)/fs
    indices=np.floor(np.maximum(0,t-lead_ms/1000)*BAUD).astype(np.int64)
    ix=np.minimum(indices,len(all_bits)-1)
    digital=indices<cut_at
    f=np.where(digital,(all_bits[ix].astype(np.int8)*2-1)*1200.,200.)
    f[t<lead_ms/1000]=0
    fade_start=lead_ms/1000+cut_at/BAUD
    amp=np.ones_like(f)
    amp[~digital]*=.55+.45*np.exp(-(t[~digital]-fade_start)/.008)
    carrier=np.exp(2j*np.pi*np.cumsum(f)/fs)
    return np.asarray(amp*carrier,dtype=np.complex64)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out",type=Path,help="new report JSON, refusing overwrite")
    p.add_argument("--scenario",choices=("all","nominal","early_done","early_pa_drop","fifo_limit"),default="all")
    p.add_argument("--iq",type=Path,help="new NPZ of **synthetic** IQ; only with one scenario")
    a=p.parse_args()
    cases=grid() if a.scenario=="all" else [run_case(a.scenario)]
    result=dict(model="source-anchored hypothetical native-FSK tail cutoff",
                source=source_contract(),baud=BAUD,air_payload_duration_ms=FULL_MS,
                historical_qualification="FSK-14 physically recovered 4/4 33-word frames; a fixed 32-word FIFO is contradicted by that control",
                safety="NO radio/SDR/bench/serial/flash actions",cases=cases)
    if a.iq:
        if len(cases)!=1:p.error("--iq requires a single --scenario")
        if a.iq.exists():p.error("IQ output exists")
        import numpy as np
        np.savez_compressed(a.iq,iq=render_iq(cases[0]),sample_rate=128000,
                            synthetic=np.array(True))
    if a.out:
        with a.out.open("x") as f:json.dump(result,f,indent=2);f.write("\n")
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()
