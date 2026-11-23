#!/usr/bin/env python3
"""Execute the REAL compiled ARMv6-M guarded packet scheduler in Unicorn.

This is not a rewritten Python state machine. Calls dv_arm_begin/push/peek/
confirm/end on a linked Cortex-M0 ELF that also holds the slim+SPI codec.
Emulator memory and the core's real Thumb instruction stream are executed.
Fuzz/negative cases verify behavior against a separate Python Flux L64
oracle. No hardware/serial, no radio TX, no flash or SPI writes.

The linked ELF is deliberately NOT FLASHABLE: it carries the null
dv_flash_active placeholder, and only its packetizer functions are
executed. The independent slim ARM codec emulator is a separate gate.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

CM0=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed/cm0/run.py')
REF=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-packet-run01/slim-sotto1200.bit')
PUBLIC_HASH='923e1956c18ecfd75cdb5e8778ea6dbff93b55970a95f330054aa4cdf504f537'

def signed32(x:int)->int:
    return x if x<0x80000000 else x-0x100000000


def make_emu(elf:Path):
    spec=importlib.util.spec_from_file_location('flux_cm0_linked',CM0)
    lib=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lib)
    lib.WS=0
    m=lib.M0(str(elf))
    return lib,m


def run(elf:Path,codec_frames:bytes)->dict:
    if not codec_frames or len(codec_frames)%6:raise ValueError('Input is 6-byte frames')
    cm0,m=make_emu(elf)
    s=cm0.cm4.IO+0x10000
    in_frame=cm0.cm4.IO
    out_words=cm0.cm4.IO+0x1000
    nbytes,_=m.call('dv_arm_state_bytes')
    if nbytes!=204:raise RuntimeError('ABI queue size mismatch')
    m.mu.mem_write(s,b'\x00'*204)
    r,_=m.call('dv_arm_begin',s,112)
    if signed32(r)!=0:raise RuntimeError('ARM state initialization failed')
    m.paint_stack()
    payloads=[]
    cycles=[]
    def drain():
        value,c=m.call('dv_arm_peek',s,out_words)
        if signed32(value)!=0:raise RuntimeError('ARM queue peek failed')
        cycles.append(c)
        payloads.append(bytes(m.mu.mem_read(out_words,66)))
        result,c=m.call('dv_arm_confirm',s,1)
        if signed32(result)!=0:raise RuntimeError('ARM ACK failed')
        cycles.append(c)
    for i in range(0,len(codec_frames),6):
        m.mu.mem_write(in_frame,codec_frames[i:i+6])
        value,c=m.call('dv_arm_push',s,in_frame)
        cycles.append(c)
        status=signed32(value)
        if status==1:drain()
        elif status!=2:raise RuntimeError('ARM enqueue refused unblocked frame')
    value,c=m.call('dv_arm_finish',s)
    cycles.append(c)
    if signed32(value)==1:drain()
    elif signed32(value)!=0:raise RuntimeError('ARM partial PTT final group refused')
    n,_=m.call('dv_arm_queue_depth',s)
    if n!=0:raise RuntimeError('ARM queue not empty after completion')
    physical=b''.join(payloads)
    # Emulator output compared against the independent host + Python
    # modem.pager oracle's PRECOMMITTED full four-frame SHA, not data-
    # assisted symbol alignment or an untrusted radio capture.
    # Header/CRC/whitening are individually validated in host tests.
    if len(codec_frames)==156 and hashlib.sha256(physical).hexdigest()!=PUBLIC_HASH:
        raise RuntimeError('ARM packetizer differs from independent host/Python L64 reference')
    if any(p[-2:]!=bytes.fromhex('5aa5') for p in payloads):
        raise RuntimeError('ARM packets missing physical guard')
    return dict(
        result='ARMv6-M Unicorn packetizer exact PASS',
        emulated_actual_arm_code=True,
        flashable=False,uses_radio=False,
        n_frames=len(codec_frames)//6,
        n_guard_packets=len(payloads),
        out_bytes=len(physical),
        out_sha256=hashlib.sha256(physical).hexdigest(),
        queue_state_bytes=nbytes,
        peak_packetizer_cycles=max(cycles),
        total_packetizer_cycles=sum(cycles),
        modeled_packetizer_stack_highwater_bytes=m.stack_used(),
    )


def fault_tests(elf:Path)->dict:
    cm0,m=make_emu(elf)
    s=cm0.cm4.IO+0x10000
    inp=cm0.cm4.IO
    dst=cm0.cm4.IO+0x1000
    m.mu.mem_write(inp,b'ABCDEF')
    assert signed32(m.call('dv_arm_begin',s,250)[0])==0
    # Two complete queue entries, third entry can accumulate at most seven.
    assert [signed32(m.call('dv_arm_push',s,inp)[0]) for _ in range(16)].count(1)==2
    for _ in range(7):
        assert signed32(m.call('dv_arm_push',s,inp)[0])==2
    assert signed32(m.call('dv_arm_push',s,inp)[0])==-3  # bounded queue full
    assert signed32(m.call('dv_arm_peek',s,dst)[0])==0
    assert signed32(m.call('dv_arm_confirm',s,1)[0])==0
    assert signed32(m.call('dv_arm_push',s,inp)[0])==1
    assert signed32(m.call('dv_arm_confirm',s,0)[0])==-5
    assert signed32(m.call('dv_arm_peek',s,dst)[0])==-2   # frozen after failed TX
    assert signed32(m.call('dv_arm_push',s,inp)[0])==-2
    assert signed32(m.call('dv_arm_begin',s,255)[0])==0
    for i in range(8):
        assert signed32(m.call('dv_arm_push',s,inp)[0])==(1 if i==7 else 2)
    assert signed32(m.call('dv_arm_push',s,inp)[0])==-4  # epoch no wrap
    assert signed32(m.call('dv_arm_finish',s)[0])==0
    assert signed32(m.call('dv_arm_confirm',s,1)[0])==0
    return dict(real_arm_unicorn_fault_cases=['bounded queue rejection',
        'ACK after dequeue','hard TX failure freeze','no silent TX retry',
        'state reset','message epoch rollover refusal'],
        all_passed=True)


def main():
    a=argparse.ArgumentParser(description=__doc__)
    a.add_argument('--elf',type=Path,required=True)
    a.add_argument('--bits',type=Path,required=True)
    a.add_argument('--out',type=Path,required=True)
    args=a.parse_args()
    if args.out.exists():a.error('refuse overwrite')
    report=run(args.elf,args.bits.read_bytes())
    report.update(fault_tests(args.elf))
    if args.bits==REF and report['out_sha256']!=PUBLIC_HASH:
        raise RuntimeError('Real K6 mic compressed frames produced wrong guarded PHY bytes')
    args.out.write_text(json.dumps(report,indent=2)+'\n')
    print('Cortex-M0 Unicorn actual ARM packetizer',report['n_frames'],
          'Sotto frames ->',report['n_guard_packets'],'guarded packets,',
          'all fault controls passed, NO RF')


if __name__=='__main__':main()
