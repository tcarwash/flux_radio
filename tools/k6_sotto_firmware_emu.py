#!/usr/bin/env python3
"""Reproducible OFFLINE Sotto-1200 Cortex-M0 decoder timing gate.

Executes actual compiled ARM Thumb instructions in Unicorn and uses previously
encoded .bit frames; no radio/SDR, serial, PTT, or host mic required. The input
can be private: logs show only counts, CPU/memory numbers, and no codec bytes.
The model does not include real SPI peripheral stalls, display/IRQ contention,
RFIC RX scheduling or physical PWM/audio timing.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import pager


def run(elf: Path, codec: bytes):
    if not codec or len(codec)%6:
        raise ValueError("Sotto input must contain complete six-byte frames")
    path=ROOT/"c/fixed/cm0/run.py"
    spec=importlib.util.spec_from_file_location("flux_sotto_cm0",path)
    cm0=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cm0)
    cm0.WS=2
    m=cm0.M0(str(elf))
    store=cm0.cm4.IO+0x10000
    pkt=cm0.cm4.IO+0x100
    pwm=cm0.cm4.IO+0x2000
    required=("sp_cb_setup","sp_dec_need","sp_dec_pool","sp_init","sp_packet",
              "sp_play_start","sp_play_pwm","sp_static_bytes")
    if any(symbol not in m.sym for symbol in required):
        raise RuntimeError("Actual linked Sotto decoder symbols missing")
    valid,setup_cycles=m.call("sp_cb_setup")
    if valid!=1:
        raise RuntimeError("SPI codebook emulator validation failed")
    state_bytes,_=m.call("sp_dec_need")
    m.call("sp_dec_pool",store+6144,state_bytes)
    nmax,_=m.call("sp_init",store,6144)
    if nmax<=0:
        raise RuntimeError("Sotto message store initialization failed")
    packets=pager.profile("L","1200",depth=1,parity=False,
                          short_last=True).pack(codec,msg_id=93)
    received=[]
    for p in packets:
        m.mu.mem_write(pkt,p)
        rc,cy=m.call("sp_packet",pkt,64)
        if rc<0:raise RuntimeError("Cortex-M0 decoder rejected CRC-good original audio")
        received.append(cy)
    m.call("sp_play_start")
    m.paint_stack()
    frame_cycles=[]
    pwm_digest=hashlib.sha256()
    max_frames=2*len(codec)//6+10
    for i in range(max_frames):
        ok,cycles=m.call("sp_play_pwm",pwm,2000,256,1)
        if not ok:break
        frame_cycles.append(cycles)
        pwm_digest.update(m.mu.mem_read(pwm,320))
    if len(frame_cycles)!=2*(len(codec)//6):
        raise RuntimeError("Unexpected Sotto-1200 20ms playback half-frame count")
    mhz=lambda c: round(c/1e6/.020,2)
    return {
        "qualification":"Actual Cortex-M0 Thumb + two-wait-state Unicorn CPU model; no physical radio",
        "codec_superframes":len(codec)//6,
        "on_mcu_playout_blocks_20ms":len(frame_cycles),
        "rf_crc_packets_accepted":len(packets),
        "mean_cpu_mhz_equivalent":mhz(sum(frame_cycles)/len(frame_cycles)),
        "worst_cpu_mhz_equivalent":mhz(max(frame_cycles)),
        "worst_playout_block_index":frame_cycles.index(max(frame_cycles)),
        "cpu_budget_mhz":96,
        "model_meets_cpu_budget_before_irq_spi_display":max(frame_cycles)<96e6*.020,
        "decoded_pwm_sha256":pwm_digest.hexdigest(),
        "stack_highwater_bytes":m.stack_used(),
        "decoder_state_bytes":state_bytes,
        "static_ram_bytes":m.call("sp_static_bytes")[0],
        "spi_codebook_setup_cycles":setup_cycles,
        "limitations":["SPI flash bytes mocked in emulator, real read latency not measured",
                       "No 8kHz microphone ISR, audio DMA/PWM IRQ, screen work or TX contention",
                       "Bench uses completed packets, not streaming RF jitter"],
    }


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elf",required=True,type=Path)
    ap.add_argument("--codec",required=True,type=Path)
    args=ap.parse_args()
    report=run(args.elf,args.codec.read_bytes())
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
