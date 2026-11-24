#!/usr/bin/env python3
"""Offline *linked ARM firmware* API31 FSK4800 receive qualification.

Runs actual ARM Sotto TX in Unicorn to produce IQ via VirtualAir, then boots
the independently compiled real K6 firmware image in K6Board and calls the
firmware's own registered FSK4800 RX function in Thumb mode. Reads physical
FD6818B registers over emulated three-wire GPIO into SoapyFang's sampled
model, validates raw 66-byte frames against an independently clocked RX oracle,
tests RX restoration and PTT cleanup. No physical hardware, serial, SDR TX or
flash operations. The API31 callback is called by a simulator debug harness
after injecting a valid initialized MOD_CTX; this is *not* a km_run module
dispatcher or a validated on-device VFO session.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from sim.k6emu.__main__ import K6Board, RAM_BASE, RAM_BYTES
from sim.k6emu.rf.backend import RFBackend
from sim.k6emu.rf.virtual_link import AirSource, Channel
from tools.k6_fsk_virtual_link import HZ
from tools.k6_sotto_virtual_link import prepare, trial, decode_session, wire_decode, wire_records_session
from tools.k6_sotto_opt_bench import mixed_pcm

REGS=(0x3f,0x59,0x58,0x72,0x5a,0x5b,0x5c,0x5d,0x5e)
PA_REGS=frozenset((0x30,0x33,0x36,0x37))
FCPU=96_000_000
STOP=0x08001f00


def symbols(elf:Path):
    lines=subprocess.check_output(["llvm-nm","--demangle",str(elf)],
                                  text=True,timeout=20).splitlines()
    values={}
    for line in lines:
        fields=line.split(maxsplit=2)
        if len(fields)==3:
            try:
                values[fields[2]]=int(fields[0],16)
            except ValueError:
                pass
    names={
        "fn":"aura::app::pager::mod_fsk4800_rx",
        "api":"aura::app::pager::MOD_API",
        "app":"aura::APP_SLOT",
        "ctx_app":"aura::app::pager::MOD_CTX (.0)",
        "ctx_syst":"aura::app::pager::MOD_CTX (.1)",
        "ctx_scratch":"aura::app::pager::MOD_CTX (.4)",
        "arena":"aura::hal::arena::ARENA",
    }
    missing=[name for name in names.values() if name not in values]
    if missing:
        raise RuntimeError(f"ELF missing exact debug symbols: {missing}")
    return {label:values[name] for label,name in names.items()}


def matching_image(elf:Path,image:Path,tmp:Path):
    rebuilt=tmp/"linked-firmware.bin"
    subprocess.run(["llvm-objcopy","-O","binary",str(elf),str(rebuilt)],
                   check=True,timeout=20)
    data=image.read_bytes()
    if data!=rebuilt.read_bytes():
        raise RuntimeError("ELF and firmware image mismatch; refusing stale callback gate")
    if len(data)>122880:
        raise RuntimeError("K6 application image exceeds 122880-byte limit")
    return hashlib.sha256(data).hexdigest(),len(data)


class FirmwareRx31:
    def __init__(self,image:Path,elf:Path,api_names:list[str]):
        from unicorn import UC_HOOK_CODE
        self.syms=symbols(elf)
        self.board=K6Board(image,collect_mmio=False)
        self.board.flash_data[0xB000:0xB004]=b"AURA" # simulator-only store-format sentinel
        self.board.run(6_000_000,1024)
        if self.board.fault:
            raise RuntimeError(f"K6 firmware boot fault: {self.board.fault}")
        initialized=bytes(self.board.uc.mem_read(self.syms["app"],512))
        if sum(bool(byte) for byte in initialized)<10:
            raise RuntimeError("K6 App was not initialized; RX callback cannot use real radio")
        self.board.fd_regs[0]=0x4829  # simulated B-chip ID; no real RFIC attached
        self.scratch=self.syms["arena"]+0x1000
        if not RAM_BASE <= self.scratch <RAM_BASE+RAM_BYTES-128:
            raise RuntimeError("RX module scratch outside RAM")
        for location,value in (
            (self.syms["ctx_app"],self.syms["app"]),
            (self.syms["ctx_syst"],0x20005800), # SYST is a zero-sized peripheral token
            (self.syms["ctx_scratch"],self.scratch),
        ):
            self.board.uc.mem_write(location,struct.pack("<I",value))
        # Use the *actual* level-31 firmware API dispatch table pointer,
        # not merely a discovered symbol; verify it refers to the function.
        api=int.from_bytes(self.board.uc.mem_read(self.syms["api"],4),"little")
        if (api&0xffff,api>>16)!=(1,31):
            raise RuntimeError(f"Compiled K6 module API version is not 1/31: {api:#x}")
        self._returned=False
        def on_return(uc,_,__,___):
            self._returned=True
            uc.emu_stop()
        self.board.uc.hook_add(UC_HOOK_CODE,on_return,begin=STOP,end=STOP)
        self.callback=self.syms["fn"]|1
        if not api_names or api_names[-1]!="fsk4800_rx" or api_names.count("mb_version")!=1:
            raise RuntimeError("API31 C header tail or scalar mailbox ABI unexpected")
        registered=int.from_bytes(self.board.uc.mem_read(
            self.syms["api"]+4+4*(len(api_names)-1),4),"little")
        if registered!=self.callback:
            raise RuntimeError(
                f"Firmware API31 table tail is not the actual linked callback: {registered:#x}"
            )
        self.stats=[]
        self.initial=tuple(self.board.fd_regs.get(r,0) for r in REGS)
        self.rf=None

    def call(self,op,ptr=0,cfg=0):
        from unicorn.arm_const import (
            UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
            UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
        )
        b=self.board
        self._returned=False
        for reg,val in ((UC_ARM_REG_R0,op),(UC_ARM_REG_R1,ptr),(UC_ARM_REG_R2,cfg),
                        (UC_ARM_REG_SP,0x20005fe0),(UC_ARM_REG_LR,STOP|1),
                        (UC_ARM_REG_PC,self.callback)):
            b.uc.reg_write(reg,val)
        before=b.cycles
        for iteration in range(512):
            b.run(512,512)
            if self._returned or b.fault:
                break
        if b.fault:
            raise RuntimeError(f"Fault in ARM RX31: {b.fault}")
        if not self._returned:
            raise RuntimeError(f"ARM RX31 callback did not return op={op}")
        value=b.uc.reg_read(UC_ARM_REG_R0)
        if value>=2**31:
            value-=2**32
        elapsed_ms=1000*(b.cycles-before)/FCPU
        self.stats.append((op,value,round(elapsed_ms,3)))
        return value

    def set_air(self,air):
        if not air.bursts or self.rf is not None:
            raise ValueError("One finite real-TX virtual-air session required")
        # Transmitter and receiver are half-duplex and separately simulated:
        # align start with 100ms RX lead time, not the firmware's boot epoch.
        delta=self.board.cycles/FCPU+.1-air.bursts[0].start
        for burst in air.bursts:
            burst.start+=delta
            burst.end+=delta
        self.rf=RFBackend(AirSource(air,240000,HZ))
        self.board.rf_backend=self.rf
        tune=HZ//10
        self.board.fd_regs[0x38]=tune&0xffff
        self.board.fd_regs[0x39]=tune>>16

    def receive(self,expected_frames):
        b=self.board
        start_writes=len(b.fd_writes)
        frames=[]
        rearms_ms=[]
        if self.call(0,0,4)!=0:
            raise RuntimeError("Firmware API31 could not arm native 4800 RX")
        try:
            for poll in range(7000):
                rc=self.call(1,self.scratch,0)
                if rc==33:
                    frames.append(bytes(b.uc.mem_read(self.scratch,66)))
                    if len(frames)==len(expected_frames):
                        break
                    t=b.cycles
                    if self.call(0,0,4)!=0:
                        raise RuntimeError("Firmware API31 fast rearm failed")
                    rearms_ms.append(round(1000*(b.cycles-t)/FCPU,4))
                elif rc<0:
                    raise RuntimeError(f"Firmware API31 RX error {rc} after {len(frames)} packets")
                else:
                    # Model 1ms scheduled RX service. Not claimed real
                    # interrupt timing or silicon packet-to-packet latency.
                    b.cycles+=FCPU//1000
        finally:
            assert self.call(2,0,0)==0
        if len(frames)!=len(expected_frames):
            raise RuntimeError(f"ARM RX recovered {len(frames)}/{len(expected_frames)}")
        if frames!=expected_frames:
            raise RuntimeError("ARM firmware frames disagree with independent IQ decoder")
        self.assert_restore(start_writes)
        state=self.rf.status()["fsk_rx"]
        if state["frames"]!=len(expected_frames) or state["overruns"] or state["underflows"]:
            raise RuntimeError(f"Native FSK FIFO model status rejected: {state}")
        return frames,rearms_ms,state

    def assert_restore(self,from_write):
        if tuple(self.board.fd_regs.get(r,0) for r in REGS)!=self.initial:
            raise RuntimeError("ARM receiver did not restore FSK register snapshot")
        writes=self.board.fd_writes[from_write:]
        if any(r in PA_REGS for r,_ in writes):
            raise RuntimeError("Receive callback wrote PA/GPIO/TX control registers")

    def cancel_tests(self):
        b=self.board
        prior=len(b.fd_writes)
        assert self.call(0,0,4)==0
        b.set_gpio("A",10,0)  # simulated physical PTT pressed (active low)
        if self.call(1,self.scratch,0)!=-2:
            raise RuntimeError("ARM API31 did not abort RX on PTT")
        self.assert_restore(prior)
        if self.call(0,0,4)!=-2:
            raise RuntimeError("ARM API31 armed RX while PTT was pressed")
        b.set_gpio("A",10,1)
        assert self.call(0,0,4)==0
        assert self.call(2,0,0)==0
        self.assert_restore(prior)
        prior=len(b.fd_writes)
        b.fd_regs[0]=0
        try:
            if self.call(0,0,4)!=-2:
                raise RuntimeError("ARM API31 armed with wrong RFIC ID")
        finally:
            b.fd_regs[0]=0x4829
        self.assert_restore(prior)
        assert self.call(0,0,4)==0
        b.cycles+=int(5.1*FCPU)
        if self.call(1,self.scratch,0)!=-2:
            raise RuntimeError("ARM API31 failed to stop at finite RX deadline")
        self.assert_restore(prior)
        return True

    def close(self):
        self.board.close_rf()


def run(image:Path,elf:Path,sotto_tree:Path):
    with tempfile.TemporaryDirectory(prefix="flux-sotto-rx31-fw-") as d:
        src=Path(d)
        sha,bytes_=matching_image(elf,image,src)
        art=prepare(sotto_tree,src,pcm=mixed_pcm())
        proof=trial(art,Channel(prefix_every=2),adc_cycles=4800,
                    spi_us=10,capture_rf_frames=True,capture_codec=True,
                    capture_air=True)
        if (not proof["session"]["complete"] or proof["received"]!=6
                or not proof["codec_exact"] or not proof["wire_decoder_agrees"]):
            raise RuntimeError("Independent Sotto TX->IQ->RX baseline failed")
        gate=FirmwareRx31(image,elf,art["names"])
        try:
            gate.set_air(proof["virtual_air"])
            raw,rearm,state=gate.receive(proof["received_physical_frames"])
            bits,session=decode_session(raw)
            records=wire_decode(art,raw)
            c_codec,c_complete=wire_records_session(records)
            if not session["complete"] or not c_complete or bits!=c_codec:
                raise RuntimeError("ARM firmware RX packets failed strict FDV1 validation")
            if bits!=proof["received_codec_bytes"]:
                raise RuntimeError("ARM RX codec differs from other independent RX path")
            gate.cancel_tests()
            return dict(
                result="PASS",firmware_sha256=sha,firmware_bytes=bytes_,
                firmware_api=31,compiled_arm_callback_address=hex(gate.syms["fn"]),
                actual_api_table_verified=True,real_arm_tx=True,
                real_arm_firmware_rx=True,physical_rf=False,
                standalone_native_host=False,uses_debug_context_injection=True,
                expected_rf_packets=len(proof["received_physical_frames"]),
                received_rf_packets=len(raw),rx_words=state["words"],
                rx_overruns=state["overruns"],rx_underflows=state["underflows"],
                fast_rearm_ms=rearm,
                session_complete=session["complete"],crc_valid=session["valid"],
                codec_frames=len(bits)//6,pcm_samples=len(art["pcm"]),
                ptt_timeout_chipid_cleanup=True,
                rf_pa_writes=0,
                notes="Full-board simulated FD6818B; direct ARM API callback, not km_run dispatcher or RF silicon",
            )
        finally:
            gate.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image",type=Path,required=True)
    parser.add_argument("--elf",type=Path,required=True)
    parser.add_argument("--sotto-tree",type=Path,required=True)
    parser.add_argument("--json",type=Path)
    a=parser.parse_args()
    report=run(a.image.resolve(),a.elf.resolve(),a.sotto_tree.resolve())
    if a.json:a.json.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":
    main()
