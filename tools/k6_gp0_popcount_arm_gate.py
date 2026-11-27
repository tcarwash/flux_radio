#!/usr/bin/env python3
"""Real linked ARM raw PDM group capture gate, 4096 exact popcount bytes.

OFFLINE ONLY. Executes the *firmware's compiled* thumbv6m ISR in Unicorn
with the real GP0 state / DMA ring layout and 8 PDM count groups per half.
Unlike a static symbol-size estimate, these counts include the path taken
through pre-decimation, fractional PCM generation and ring writes.

Unicorn instruction counts are **not** Cortex-M0 cycles. This tool does not
emulate peripheral bus waits, IRQ nesting, variable flash/SRAM wait states,
actual PDM analog capture, or Sotto encoder foreground contention. It does
not touch serial, SDR, radio, SPI flash, or bench reservation.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import statistics
import struct
import subprocess
import tempfile

from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC,
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3,
)

FLASH_BASE=0x08002000
FLASH_MAP=0x08000000
SRAM_MAP=0x20000000
DMA_MAP=0x40020000
TIM_MAP=0x40014000
DMA_ISR=0x40020000
DMA_IFCR=0x40020004
DMA_CCR1=0x40020008
TIM17_CNT=0x40014824
RETURN=0x2000ff00
STACK=0x2000fe00
RAW=0x20009000
PCM=0x2000a000
SAMPLE_RATE=87261
STATE_MODULE="aura::hal::gp0_capture::STATE"
EXPECTED_SERIALIZED={"sotto_gp0_dma_irq","SOTTO_GP0_PCM_RING",
                     "SOTTO_GP0_RAW_RING","SOTTO_GP0_IRQ_COUNT"}


def symbols(elf:Path)->dict:
    nm=subprocess.check_output(["llvm-nm","-S","--demangle",str(elf)],text=True)
    syms={}
    for line in nm.splitlines():
        cols=line.split()
        if len(cols)<4:continue
        name=" ".join(cols[3:])
        if name in EXPECTED_SERIALIZED or name==STATE_MODULE:
            syms[name]=(int(cols[0],16),int(cols[1],16))
    required=EXPECTED_SERIALIZED|{STATE_MODULE}
    if set(syms)!=required:
        raise ValueError(f"linked GP0 IRQ symbol mismatch: {sorted(required-set(syms))}")
    return syms


def profile(elf:Path,image:Path,*,halves=96,groups_pattern="tone")->dict:
    if halves!=512:raise ValueError("raw-group capture gate requires 512 DMA half-buffers")
    image_bytes=image.read_bytes()
    if not (110000 <= len(image_bytes) <= 122880):
        raise ValueError("not a valid experimental K6 MCU image size")
    # Never profile stale symbols from a different firmware image.
    with tempfile.TemporaryDirectory(prefix="gp0-irq-elf-match-") as scratch:
        rebuilt=Path(scratch)/"linked.bin"
        subprocess.run(["llvm-objcopy","-O","binary",str(elf),str(rebuilt)],
                       check=True,capture_output=True,timeout=20)
        if rebuilt.read_bytes()!=image_bytes:
            raise ValueError("ELF and linked MCU image differ")
    syms=symbols(elf)
    if syms["sotto_gp0_dma_irq"][0] not in range(FLASH_BASE,FLASH_BASE+len(image_bytes)):
        raise ValueError("IRQ address outside firmware image")
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
    uc.ctl_set_cpu_model(__import__("unicorn").arm_const.UC_CPU_ARM_CORTEX_M0)
    uc.mem_map(FLASH_MAP,0x40000)
    uc.mem_map(SRAM_MAP,0x20000)
    uc.mem_map(DMA_MAP,0x1000)
    uc.mem_map(TIM_MAP,0x1000)
    uc.mem_write(FLASH_BASE,image_bytes)
    uc.mem_write(RETURN,b"\x70\x47")
    def write32(a,v):uc.mem_write(a,struct.pack("<I",v))
    def read32(a):return struct.unpack("<I",uc.mem_read(a,4))[0]
    addr=lambda name:syms[name][0]
    state=addr(STATE_MODULE)
    write32(addr("SOTTO_GP0_PCM_RING"),PCM)
    write32(addr("SOTTO_GP0_RAW_RING"),RAW)
    # Rust State repr(C): armed, expect_tc, failed, reason/pad;
    # old_priority + _pad[3], old_ahb, old_apb2, Decimator {fs,...}.
    uc.mem_write(state,b"\x01\x00\x00\x00")
    write32(state+16,SAMPLE_RATE)
    write32(DMA_CCR1,0x30af)
    write32(TIM17_CNT,0) # fixed for profile: no cycle simulation claimed
    peak_instructions=0
    instructions=[]
    irq=addr("sotto_gp0_dma_irq")
    count=[0]
    def on_code(uc,pc,size,user):
        if pc==RETURN:uc.emu_stop();return
        count[0]+=1
        if count[0]>30000:raise RuntimeError("ISR exceeded bound, wrong linked image?")
    uc.hook_add(UC_HOOK_CODE,on_code)
    for j in range(halves):
        # 8x64 PDM bits per DMA half, one bit at bit1 of each byte;
        # deterministic 32/64 population varies with phase/tone.
        offset=(j%2)*512
        half=bytearray(512)
        for group in range(8):
            g=j*8+group
            if groups_pattern=="tone":high=32+int(round(5*__import__("math").sin(2*__import__("math").pi*g*1000/87260)))
            elif groups_pattern=="silence":high=32
            else:raise ValueError("unknown PDM fixture")
            for i in range(64):
                half[group*64+i]=2 if (i*17+g*11)%64<high else 0
        uc.mem_write(RAW+offset,bytes(half))
        write32(DMA_ISR,4 if j%2==0 else 2)
        write32(TIM17_CNT,0)
        uc.reg_write(UC_ARM_REG_SP,STACK)
        uc.reg_write(UC_ARM_REG_LR,RETURN|1)
        uc.reg_write(UC_ARM_REG_PC,irq|1)
        for reg in (UC_ARM_REG_R0,UC_ARM_REG_R1,UC_ARM_REG_R2,UC_ARM_REG_R3):
            uc.reg_write(reg,0)
        count[0]=0
        uc.emu_start(irq|1,0,count=30000)
        assert read32(addr("SOTTO_GP0_IRQ_COUNT"))==j+1,(j,count[0])
        instructions.append(count[0])
        if read32(DMA_CCR1)==0:raise RuntimeError("GP0 IRQ unexpectedly dekeyed DMA")
    produced,consumed,off,lost=uc.mem_read(PCM+640,4)
    count_groups=read32(state+32)
    captured=bytes(uc.mem_read(PCM+644,4096))
    expected=bytes(32+int(round(5*__import__('math').sin(2*__import__('math').pi*g*1000/87260))) for g in range(4096))
    if captured!=expected:raise RuntimeError('raw group capture differs from GPIO bitpopcount oracle')
    if (produced,consumed,off,lost)!=(1,0,0,0):
        raise RuntimeError(f'raw capture completion flag invalid {produced,consumed,off,lost}')
    if count_groups!=4096:raise RuntimeError(f'raw group count differs {count_groups}')
    return dict(offline_only=True,actual_linked_rust_arm=True,
        not_cycle_accurate=True,source='synthetic GPIO DMA half-buffer bit patterns',
        samples=4096,raw_popcounts_sha256=sha256(captured).hexdigest(),
        firmware_sha256=sha256(image_bytes).hexdigest(),
        firmware_bytes=len(image_bytes),
        all_counts_0_to_64=all(x<=64 for x in captured),
        irq_count=read32(addr('SOTTO_GP0_IRQ_COUNT')),irq_halves=halves,
        raw_buffer_exact=True,stop_needed_in_module=True,
        produced=produced,consumed=consumed,lost=lost,
        mean_instructions=round(statistics.mean(instructions),2),
        max_instructions=max(instructions),
        first_irq_instructions=instructions[:8],last_irq_instructions=instructions[-8:])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--elf",type=Path,required=True)
    p.add_argument("--image",type=Path,required=True)
    p.add_argument("--halves",type=int,default=512)
    p.add_argument("--pattern",choices=("tone","silence"),default="tone")
    p.add_argument("--json",type=Path)
    a=p.parse_args()
    r=profile(a.elf.resolve(),a.image.resolve(),halves=a.halves,groups_pattern=a.pattern)
    if a.json:
        with a.json.open("x") as f:f.write(json.dumps(r,indent=2,sort_keys=True)+"\n")
    print(json.dumps(r,indent=2,sort_keys=True))
if __name__=="__main__":main()
