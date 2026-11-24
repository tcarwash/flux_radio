#!/usr/bin/env python3
"""Run real K6 ARM API29 bounded FSK TX in SoapyFang's virtual RF bench.

CRC32-only development cable ingest and normal ARM km_run call are real
compiled code, while all GPIO/RFIC/clock/PA behavior remains simulated.
No serial device, RF transmitter, physical SDR, flash, mic or bench lease.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
from pathlib import Path
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from sim.k6emu.rf.virtual_link import Channel, VirtualAir, VirtualTxBackend, AirSource
from sim.k6emu.rf.backend import RFBackend
from tools.k6_fsk_emulator_replay import receive
from tools.k6_fsk_virtual_link import HZ
from tools.k6_sotto_opt_bench import mixed_pcm
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher
from tools.k6_sotto_rx4800_firmware_gate import matching_image

SRC=ROOT/"tests/data/k6/sotto_async29_tx_call_emu.c"
def run(elf:Path,image:Path,tree:Path):
    with tempfile.TemporaryDirectory(prefix="flux-sotto-api29-arm-") as td:
        tmp=Path(td)
        sha,bytes_=matching_image(elf,image,tmp)
        art=prepare(tree,tmp,pcm=mixed_pcm())
        details=[]
        for case,flags,expect in (
            ("success",[],9),
            ("rx_tx_rx",["-DHALF_DUPLEX"],9),
            ("bad_guard",["-DBAD_GUARD"],-20),
            ("abort_early",["-DABORT_EARLY"],8),
            ("timeout_poll",["-DDELAY_PAST_DEADLINE"],-12),
        ):
            code,entry=k6mod.build(str(SRC),cflags=[*k6mod.CFLAGS,*flags])
            fw=Dispatcher(elf,image,art["names"])
            try:
                normal_rx_pa=fw.board.fd_regs.get(0x36)
                air=VirtualAir(Channel())
                rf=VirtualTxBackend(air,240_000,HZ)
                fw.board.rf_backend=rf
                fw.verified_cable_stage(code,entry)
                fw.execute(expected_return=expect,require_rf=False,require_radio_tx=True,
                           min_api31_calls=3 if case=="rx_tx_rx" else 0)
                stat=rf.tx_status()
                writes=fw.board.fd_write_events
                fifo=[i for i,(_,r,v) in enumerate(writes) if r==0x5f]
                pa_on=[i for i,(_,r,v) in enumerate(writes)
                       if r==0x36 and v!=normal_rx_pa]
                # Enforce PA mute while the module's FIFO is being loaded.
                # Ignore initial normal Aura boot PA writes; use the start of
                # the native 33-word FIFO transaction as a strict boundary.
                rx_verification=None
                if case in ("success","rx_tx_rx"):
                    if stat["completed"]!=1 or stat["aborted"]!=0 or stat["overflows"] or stat["rejected"] or len(air.bursts)!=1:
                        raise RuntimeError(f"linked ARM FSK TX did not complete: {stat}")
                    if len(fifo)!=33 or len(pa_on)!=1 or not fifo[-1]<pa_on[0]:
                        raise RuntimeError("PA turned on before all 33 FIFO words were loaded")
                    # Independent compiled M0/SoapyFang IQ receiver should
                    # recover exactly the RFIC byte stream, not an oracle
                    # passed straight from the transmitter module.
                    tune=air.bursts[0].hz
                    source=RFBackend(AirSource(air,240_000,tune))
                    try:
                        frames,rx=receive(
                            source,tune=tune,duration=air.bursts[-1].end+.14,
                            frame_bytes=66,threshold=4,poll_us=500)
                    finally:source.close()
                    expected=struct.pack(
                        "<33H",*(0x2134+i*137 for i in range(32)),0xa55a)
                    if frames!=[expected]:
                        raise RuntimeError(
                            f"Independent virtual IQ receiver mismatch: {len(frames)} frames "
                            f"first={frames[0].hex() if frames else None} expected={expected.hex()}")
                    rx_verification={"frames":len(frames),"match":True,
                                     "fsk":rx["fsk"]}
                else:
                    if case=="timeout_poll" and (stat["completed"]!=1 or stat["active"] or stat["overflows"]):
                        raise RuntimeError(f"Deadline cleanup did not release RF: {stat}")
                    if air.bursts and case=="bad_guard":
                        raise RuntimeError("Bad guard emitted RF")
                    if case=="abort_early" and (stat["aborted"]<1 or stat["completed"]):
                        raise RuntimeError(f"Early abort left RF active or completed unexpectedly: {stat}")
                if stat["active"] or fw.board.fd_regs.get(0x36)!=normal_rx_pa:
                    raise RuntimeError(f"Module left RF/PA active: {stat}, PA={fw.board.fd_regs.get(0x36)}")
                details.append(dict(case=case,module_return=fw.module_ret[0],
                                    api31_calls=fw.trace["api31"],
                                    virtual_fsk=stat,air_bursts=len(air.bursts),
                                    pa_off=True,words_written=len(fifo),
                                    radio_enter_tx_calls=fw.trace["radio_enter_tx"],
                                    independent_iq_receive=rx_verification))
            finally:fw.close()
        return dict(result="PASS",firmware_sha256=sha,firmware_bytes=bytes_,
                    arm_module_runner=True,cable_crc_development_only=True,
                    rf_simulated_only=True,hardware_used=False,
                    api29_implemented=True,api30_mic_implemented=False,
                    cases=details)
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--elf",type=Path,required=True)
    p.add_argument("--image",type=Path,required=True)
    p.add_argument("--sotto-tree",type=Path,required=True)
    p.add_argument("--json",type=Path)
    a=p.parse_args()
    result=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve())
    if a.json:a.json.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
