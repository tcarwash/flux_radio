#!/usr/bin/env python3
"""Offline linked ARM cooperative Sotto FSK host and IQ receive qualification.

Boots the real, compiled Cortex-M0 host, stages a level-30 C module through
its CRC32-only development cable ingress and executes its actual registered
API29 FSK service. RFIC, PA, timers and RF are emulated; no device is used.
This gate deliberately does not approve flashing or over-air TX.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import struct
import tempfile
from pathlib import Path

from modem import k6mod
from sim.k6emu.rf.virtual_link import Channel,VirtualAir,VirtualTxBackend,AirSource
from sim.k6emu.rf.backend import RFBackend
from tools.k6_fsk_emulator_replay import receive
from tools.k6_fsk_virtual_link import HZ
from tools.k6_sotto_opt_bench import mixed_pcm
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher
from tools.k6_sotto_rx4800_firmware_gate import matching_image

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'tests/data/k6/sotto_fsk_coop_arm_call_emu.c'
FCPU=96_000_000


def run(elf:Path,image:Path,tree:Path)->dict:
    with tempfile.TemporaryDirectory(prefix='k6-coop-fsk-linked-arm-') as d:
        tmp=Path(d)
        sha,size=matching_image(elf,image,tmp)
        if size>122880:raise ValueError('firmware flash limit exceeded')
        art=prepare(tree,tmp,pcm=mixed_pcm())
        details=[]
        for case,flags,expected_return in (
            ('success',[],9),
            ('bad_guard',['-DBAD_GUARD'],-20),
            ('abort_early',['-DABORT_EARLY'],8),
            ('timeout',['-DDELAY_PAST_DEADLINE'],-12),
        ):
            code,entry=k6mod.build(str(SRC),cflags=[*k6mod.CFLAGS,*flags])
            fw=Dispatcher(elf,image,art['names'],require_rx=False)
            try:
                original_pa=fw.board.fd_regs.get(0x36)
                air=VirtualAir(Channel())
                rf=VirtualTxBackend(air,240000,HZ)
                fw.board.rf_backend=rf
                chunks,_=fw.verified_cable_stage(code,entry)
                fw.execute(expected_return=expected_return,require_rf=False,
                           require_radio_tx=True)
                status=rf.tx_status()
                events=fw.board.fd_write_events
                fifo=[(t,v) for t,r,v in events if r==0x5f]
                payload=struct.pack('<33H',*(0x2134+137*i for i in range(32)),0xa55a)
                rx_ok=False
                frames_received=0
                ms_after_last_fifo=None
                fifo_groups=[]
                if case=='success':
                    if status['completed']!=1 or status['aborted'] or status['rejected'] or status['overflows'] or len(air.bursts)!=1:
                        raise RuntimeError(f'cooperative linked FSK failed: {status}')
                    if len(fifo)!=33 or [v for _,v in fifo]!=list(struct.unpack('<33H',payload)):
                        raise RuntimeError('real ARM host transmitted wrong FIFO sequence')
                    # Verify the ACTUAL linked ARM callback uses discrete
                    # guarded FIFO batches, not the default synchronous path.
                    # A conservative 2-ms separation splits the 5-ms polls;
                    # individual writes in a batch are much closer together.
                    for i,(tick,_) in enumerate(fifo):
                        if i==0 or tick-fifo[i-1][0]>FCPU*.002:
                            fifo_groups.append(0)
                        fifo_groups[-1]+=1
                    if fifo_groups!=[8,8,8,8,1]:
                        raise RuntimeError(f'cooperative FIFO staging absent: {fifo_groups}')
                    if any(r==0x59 and v==0x08f8 and t<=fifo[-1][0]
                           for t,r,v in events):
                        raise RuntimeError('FSK enabled before final FIFO guard')
                    before_fifo=[t for t,r,v in events if r==0x36 and v!=original_pa and t<fifo[0][0]]
                    if not before_fifo:raise RuntimeError('PA was not enabled before FIFO preload')
                    starts=[t for t,r,v in events if r==0x59 and v==0x08f8 and t>fifo[-1][0]]
                    if len(starts)!=1:raise RuntimeError('FSK started without full FIFO or restarted')
                    ms_after_last_fifo=round((starts[0]-fifo[-1][0])/FCPU*1000,2)
                    if ms_after_last_fifo<20.0:
                        raise RuntimeError('missing 20 ms settling before RF')
                    source=RFBackend(AirSource(air,240000,air.bursts[0].hz))
                    try:
                        frames,_=receive(source,tune=air.bursts[0].hz,
                                         duration=air.bursts[-1].end+.14,
                                         frame_bytes=66,threshold=4,poll_us=500)
                    finally:source.close()
                    frames_received=len(frames)
                    rx_ok=frames==[payload]
                    if not rx_ok:raise RuntimeError('independent IQ receiver failed to recover 66-byte FIFO packet')
                else:
                    if status['completed'] or air.bursts or fifo or status['active']:
                        raise RuntimeError(f'{case} allowed RF or FIFO writes: {status}')
                if status['active'] or fw.board.fd_regs.get(0x36)!=original_pa:
                    raise RuntimeError('linked ARM host left PA or RF active')
                details.append(dict(case=case,module_return=fw.module_ret[0],
                                    cable_chunks=chunks,real_arm_dispatcher=True,
                                    words_written=len(fifo),fifo_batch_sizes=fifo_groups,
                                    rf_bursts=len(air.bursts),
                                    virtual_status=status,independent_iq_packet_exact=rx_ok,
                                    independent_iq_frames=frames_received,
                                    milliseconds_from_fifo_guard_to_rf=ms_after_last_fifo,
                                    tx_ended_inactive=True))
            finally:fw.close()
        return dict(result='PASS',firmware_sha256=sha,firmware_bytes=size,
                    linked_arm_host=True,api_level=30,real_crc32_development_cable=True,
                    production_siphash_not_tested=True,hardware_used=False,
                    physical_dma_timing_tested=False,physical_rf_tested=False,
                    rf_simulated_only=True, cases=details)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--image',type=Path,required=True)
    p.add_argument('--sotto-tree',type=Path,default=ROOT)
    p.add_argument('--json',type=Path)
    a=p.parse_args()
    out=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve())
    if a.json:a.json.write_text(json.dumps(out,indent=2)+'\n')
    print(json.dumps(out,indent=2))

if __name__=='__main__':main()
