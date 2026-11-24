#!/usr/bin/env python3
"""Offline real K6 cable loader -> C module runner -> ARM API31 -> Sotto IQ.

Unlike k6_sotto_rx4800_firmware_gate.py, this does NOT inject a ModCtx or
directly call the API31 callback. Booted Aura's own C cable packet verifier
accepts a CRC32-tagged, 26-chunk level-31 module image, then compiled Rust
run_module creates a real ModCtx and invokes km_run_on_stack / km_run. The
compiled C module parses the received FDV1 session from SoapyFang virtual IQ.

The cable packets enter at the real compiled km_cable_packet function from a
CPU test harness, NOT through a full UART/CPS serial session, physical radio,
SPI library slot, or flash installation. This uses the cable-only CRC32
development image, NOT the production keyed SipHash authorization path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from modem import k6mod
from sim.k6emu.__main__ import K6Board
from sim.k6emu.rf.backend import RFBackend
from sim.k6emu.rf.virtual_link import AirSource,Channel
from tools.k6_fsk_virtual_link import HZ
from tools.k6_sotto_opt_bench import mixed_pcm
from tools.k6_sotto_rx4800_firmware_gate import STOP,FCPU,matching_image,symbols
from tools.k6_sotto_virtual_link import prepare,trial

CALLBACK="aura::app::pager::mod_fsk4800_rx"
ENTRY="aura::app::pager::run_module"
CABLE="km_cable_packet"
STACK="km_run_on_stack"
RUN="km_run"
ENTER_TX="<aura::device::radio::Radio>::enter_tx"
SRC=ROOT/"tests/data/k6/sotto_rx31_call_emu.c"


def linked_symbols(elf:Path):
    names=(CALLBACK,ENTRY,CABLE,STACK,RUN,ENTER_TX)
    rows=subprocess.check_output(["llvm-nm","--demangle",str(elf)],
                                 text=True,timeout=20).splitlines()
    found={}
    for row in rows:
        f=row.split(maxsplit=2)
        if len(f)!=3:
            continue
        try:
            v=int(f[0],16)
        except ValueError:
            continue
        if f[2] in names:
            found[f[2]]=v
    if len(found)!=len(names):
        raise RuntimeError(f"Missing linked ARM symbols: {sorted(set(names)-set(found))}")
    return found


class Dispatcher:
    def __init__(self,elf:Path,image:Path,api_names:list[str]):
        from unicorn import UC_HOOK_CODE
        from unicorn.arm_const import UC_ARM_REG_R0
        self.syms=symbols(elf)
        self.linked=linked_symbols(elf)
        self.board=K6Board(image,collect_mmio=False,capture_tx_events=True)
        self.board.flash_data[0xB000:0xB004]=b"AURA"  # virtual flash-map sentinel
        self.board.run(6_000_000,1024)
        if self.board.fault:
            raise RuntimeError(f"Firmware boot fault: {self.board.fault}")
        app=bytes(self.board.uc.mem_read(self.syms["app"],512))
        if sum(bool(x) for x in app)<10:
            raise RuntimeError("Booted firmware App not initialized")
        self.board.fd_regs[0]=0x4829  # explicit virtual B-chip
        self.returned=False
        self.trace={name:0 for name in ("run_module","km_run","km_run_on_stack",
                                        "consumer","api31","radio_enter_tx")}
        self.module_ret=[]
        self.expected_module_entry=None

        def on_stop(uc,_,__,___):
            self.returned=True
            uc.emu_stop()
        self.board.uc.hook_add(UC_HOOK_CODE,on_stop,begin=STOP,end=STOP)
        def on_entry(key):
            def record(*args):
                self.trace[key]+=1
            return record
        for symbol,key in ((ENTRY,"run_module"),(STACK,"km_run_on_stack"),
                           (RUN,"km_run"),(CALLBACK,"api31"),(ENTER_TX,"radio_enter_tx")):
            addr=self.linked[symbol]
            self.board.uc.hook_add(UC_HOOK_CODE,on_entry(key),begin=addr,end=addr)
        # km_run_on_stack is a fixed naked ARM/Thumb ABI trampoline.
        # +0x14 is the first instruction after its bl km_run, with module
        # result in R0; asserted against compiled code in this exact image.
        km_return=self.linked[STACK]+0x14
        if self.board.uc.mem_read(km_return,2)!=b"\x08\xbc": # pop {r3}
            raise RuntimeError("km_run_on_stack ABI trampoline layout changed")
        def on_module_result(uc,_,__,___):
            v=uc.reg_read(UC_ARM_REG_R0)
            self.module_ret.append(v if v<2**31 else v-2**32)
        self.board.uc.hook_add(UC_HOOK_CODE,on_module_result,
                               begin=km_return,end=km_return)
        api=int.from_bytes(self.board.uc.mem_read(self.syms["api"],4),"little")
        if (api&0xffff,api>>16)!=(1,31):
            raise RuntimeError("Compiled Rust host does not expose API31")
        if not api_names or api_names[-1]!="fsk4800_rx" or api_names.count("mb_version")!=1:
            raise RuntimeError("Wrong level-31 C API layout")
        fn=int.from_bytes(self.board.uc.mem_read(
            self.syms["api"]+4+4*(len(api_names)-1),4),"little")
        if fn!=self.linked[CALLBACK]|1:
            raise RuntimeError("Rust API31 table does not point at compiled callback")
        self.rf=None

    def call(self,address:int,*args:int,limit:int=500):
        from unicorn.arm_const import (
            UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3,
            UC_ARM_REG_SP,UC_ARM_REG_LR,UC_ARM_REG_PC,
        )
        b=self.board
        self.returned=False
        params=list(args)+[0]*4
        for reg,value in zip((UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3),
                             params[:4]):
            b.uc.reg_write(reg,value)
        b.uc.reg_write(UC_ARM_REG_SP,0x20005fe0)
        b.uc.reg_write(UC_ARM_REG_LR,STOP|1)
        b.uc.reg_write(UC_ARM_REG_PC,address|1)
        for _ in range(limit):
            b.run(512,512)
            if self.returned or b.fault:
                break
        if b.fault:
            raise RuntimeError(f"Linked ARM fault: {b.fault}")
        if not self.returned:
            raise RuntimeError(f"Linked ARM entrypoint timed out: {address:#x}")
        v=b.uc.reg_read(UC_ARM_REG_R0)
        return v if v<2**31 else v-2**32

    def verified_cable_stage(self,code:bytes,entry:int,expected_chunks:int|None=None):
        from unicorn import UC_HOOK_CODE
        envelope=k6mod.image(code,entry,serial=42,key=None,level=31)
        # CPU-side byte input to the real C cable ingress (CRC + replay checks).
        packets=k6mod.packets(envelope,xfer=91,length=64)
        if expected_chunks is not None and len(packets)!=expected_chunks:
            raise RuntimeError("Test fixture upload unexpectedly changed chunk count")
        if not packets or len(packets)>255:
            raise RuntimeError("Invalid cable upload chunk count")
        arena=self.syms["arena"]
        pkt_addr=0x20005000
        self.board.uc.mem_write(pkt_addr,bytes(packets[0][:11])+bytes([packets[0][11]^0x40])+packets[0][12:])
        assert self.call(self.linked[CABLE],pkt_addr,64,arena,11776)==-1  # bad CRC
        for index,pkt in enumerate(packets):
            self.board.uc.mem_write(pkt_addr,pkt)
            actual=self.call(self.linked[CABLE],pkt_addr,64,arena,11776)
            expected=3 if index==len(packets)-1 else 1
            if actual!=expected:
                raise RuntimeError(
                    f"Compiled cable loader refused valid API31 module: chunk={index+1} "
                    f"actual={actual}, expected={expected}"
                )
        if bytes(self.board.uc.mem_read(arena,len(envelope)))!=envelope:
            raise RuntimeError("Loader mutated or incompletely staged code")
        self.expected_module_entry=arena+16+entry
        def on_consumer(*args):
            self.trace["consumer"]+=1
        self.board.uc.hook_add(UC_HOOK_CODE,on_consumer,
                               begin=self.expected_module_entry,
                               end=self.expected_module_entry)
        return len(packets),len(envelope)

    def virtual_air(self,air):
        if not air.bursts:
            raise RuntimeError("No real ARM Sotto waveform")
        delta=self.board.cycles/FCPU+.1-air.bursts[0].start
        for burst in air.bursts:
            burst.start+=delta
            burst.end+=delta
        self.rf=RFBackend(AirSource(air,240000,HZ))
        self.board.rf_backend=self.rf
        hz=HZ//10
        self.board.fd_regs[0x38]=hz&0xffff
        self.board.fd_regs[0x39]=hz>>16

    def execute(self,expected_return:int=6,require_rf:bool=True):
        from unicorn.arm_const import (
            UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_SP,
            UC_ARM_REG_LR,UC_ARM_REG_PC,
        )
        b=self.board
        self.returned=False
        for reg,value in ((UC_ARM_REG_R0,self.syms["app"]),
                          (UC_ARM_REG_R1,0x20005800),  # zero-sized SYST token
                          (UC_ARM_REG_SP,0x20005fe0),
                          (UC_ARM_REG_LR,STOP|1),
                          (UC_ARM_REG_PC,self.linked[ENTRY]|1)):
            b.uc.reg_write(reg,value)
        for _ in range(16000):
            b.run(2048,1024)
            if self.returned or b.fault:
                break
        if b.fault:
            raise RuntimeError(f"Normal K6 dispatcher crashed: {b.fault}")
        if not self.returned:
            raise RuntimeError("Normal K6 module dispatcher hung")
        if not (self.trace["run_module"]==1
                and self.trace["km_run"]==1
                and self.trace["km_run_on_stack"]==1
                and self.trace["consumer"]==1
                and (self.trace["api31"]>100 if require_rf else self.trace["api31"]==0)
                and self.trace["radio_enter_tx"]==0):
            raise RuntimeError(f"Normal execution/PA trace unexpected: {self.trace}")
        if self.module_ret!=[expected_return]:
            raise RuntimeError(f"Real compiled C module returned unexpected value: {self.module_ret}")
        expected_line=f"K6 MOD ret={expected_return} (0x{expected_return&0xffffffff:x})".encode()
        if expected_line not in b.uart_tx:
            raise RuntimeError("Host did not observe successful module return")
        if int.from_bytes(b.uc.mem_read(self.syms["ctx_app"],4),"little")!=0:
            raise RuntimeError("Host leaked module context on return")
        if not require_rf:
            return {}
        fsk=self.rf.status()["fsk_rx"]
        if fsk["frames"]!=6 or fsk["words"]!=198 or fsk["overruns"] or fsk["underflows"]:
            raise RuntimeError(f"Normal dispatcher consumed wrong RF frames: {fsk}")
        return fsk

    def close(self):
        self.board.close_rf()


def run(elf:Path,image:Path,tree:Path):
    with tempfile.TemporaryDirectory(prefix="flux-sotto-rx31-dispatch-") as td:
        sha,bytes_=matching_image(elf,image,Path(td))
        art=prepare(tree,Path(td),pcm=mixed_pcm())
        proof=trial(art,Channel(prefix_every=2),adc_cycles=4800,
                    spi_us=10,capture_rf_frames=True,capture_codec=True,
                    capture_air=True)
        if not proof["session"]["complete"] or proof["received"]!=6:
            raise RuntimeError("Independent ARM TX->IQ baseline failed")
        code,entry=k6mod.build(str(SRC))
        if len(code)+24>10240:
            raise RuntimeError("API31 Cortex-M0 module exceeds arena/code cap")
        dispatch=Dispatcher(elf,image,art["names"])
        try:
            n,envelope=dispatch.verified_cable_stage(code,entry,expected_chunks=26)
            dispatch.virtual_air(proof["virtual_air"])
            fsk=dispatch.execute()
            return dict(
                result="PASS",
                firmware_sha256=sha,firmware_bytes=bytes_,api_level=31,
                module_bytes=len(code),module_envelope_bytes=envelope,
                cable_chunks=n,corrupt_cable_crc_rejected=True,
                cable_crc32_only=True,production_siphash_tested=False,
                real_arm_module_loader=True,real_arm_stack_switch=True,
                real_arm_module_parser=True,
                module_return=dispatch.module_ret[0],
                firmware_run_module_calls=dispatch.trace["run_module"],
                firmware_km_run_calls=dispatch.trace["km_run"],
                firmware_rx31_callbacks=dispatch.trace["api31"],
                firmware_radio_enter_tx_calls=dispatch.trace["radio_enter_tx"],
                rf_packets_expected=proof["received"],
                rf_packets_received=fsk["frames"],
                rf_words_received=fsk["words"],
                fifo_overruns=fsk["overruns"],fifo_underflows=fsk["underflows"],
                module_context_cleared=True,
                source_revision=art["revision"],
                simulated_only=True,physical_hardware_used=False,
                full_uart_cps_session_tested=False,spi_library_install_tested=False,
                receiver_pcm_service_tested=False,
            )
        finally:
            dispatch.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--elf",required=True,type=Path)
    p.add_argument("--image",required=True,type=Path)
    p.add_argument("--sotto-tree",type=Path,required=True)
    p.add_argument("--json",type=Path)
    args=p.parse_args()
    r=run(args.elf.resolve(),args.image.resolve(),args.sotto_tree.resolve())
    if args.json:
        args.json.write_text(json.dumps(r,indent=2)+"\n")
    print(json.dumps(r,indent=2))


if __name__=="__main__":
    main()
