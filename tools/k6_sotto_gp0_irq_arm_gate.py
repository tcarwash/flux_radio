#!/usr/bin/env python3
"""Execute real Cortex-M0 PIC GP0 IRQ9 handler through its SRAM trampoline.

Offline only. This validates handler relocation, saved context pointer,
register/data accesses, DMA half ordering, PCM writes and cleanup behavior.
It is not a physical DMA timing or FSK/codec concurrency qualification.
"""
from __future__ import annotations
import argparse
import json
import struct
import sys
import subprocess
import tempfile
from pathlib import Path
from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_CODE, UC_HOOK_MEM_READ
from unicorn.arm_const import UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_R0
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
SRC=ROOT/'c/k6/mod/sotto_mic_gp0_irq.c'
CODE=0x20002000
CTX=0x20004000
PCM=0x20004300
RAW=0x20004700
STACK=0x20005e00
RETURN=0x20005f00
DMA_ISR=0x40020000
DMA_IFCR=0x40020004
DMA_CCR=0x40020008
TIM_CNT=0x40014824


def build():
    with tempfile.TemporaryDirectory(prefix='k6-gp0-irq-arm-') as tmp:
        obj=Path(tmp)/'test.o';elf=Path(tmp)/'test.elf';image=Path(tmp)/'test.bin'
        flags=[('-O'+k6mod.source_opt(str(SRC)) if x=='-Os' else x) for x in k6mod.CFLAGS]
        subprocess.run(['clang',*flags,'-c',str(SRC),'-o',str(obj)],check=True)
        subprocess.run(['ld.lld','-T',str(k6mod.LINK),'--defsym','MODULE_BASE=0',
                        '--gc-sections','--no-undefined',str(obj),'-o',str(elf)],check=True)
        subprocess.run(['llvm-objcopy','-O','binary','--only-section=.text',str(elf),str(image)],check=True)
        symbols={}
        for line in subprocess.check_output(['llvm-nm',str(elf)],text=True).splitlines():
            cells=line.split()
            if len(cells)>=3 and cells[-1] in ('gp0_irq_entry','gp0_irq_context_word','gp0_irq_body','gp0_irq_store_context'):
                symbols[cells[-1]]=int(cells[0],16)
        assert len(symbols)==4,symbols
        code=image.read_bytes()
    expected,entry=k6mod.build(str(SRC))
    assert code==expected and entry==16
    return code,symbols


def emulate(fault:bool=False):
    blob,sym=build()
    uc=Uc(UC_ARCH_ARM,UC_MODE_THUMB|UC_MODE_MCLASS)
    uc.mem_map(0x20000000,0x10000)
    uc.mem_map(0x40020000,0x1000)
    uc.mem_map(0x40014000,0x1000)
    uc.mem_write(CODE,blob)
    uc.mem_write(RETURN,b'\x70\x47') # bx lr (arrival intercepted)
    uc.mem_write(CTX,struct.pack('<III',PCM,RAW,87770))
    # dec fs at ctx+8; phase/area/dc/samples/clipped all initially zero
    uc.mem_write(CTX+56,bytes([0,1,0,0]))  # expect HT=0, active=1
    uc.mem_write(CODE+sym['gp0_irq_context_word'],struct.pack('<I',CTX))
    uc.mem_write(0x20000064,struct.pack('<I',CODE+sym['gp0_irq_entry']+1))
    for j in range(16):
        bitcount=32 if not (j&1) else 33
        data=bytes((2 if ((i*bitcount)&63)<bitcount else 0) for i in range(64))
        uc.mem_write(RAW+j*64,data)
    uc.mem_write(DMA_ISR,struct.pack('<I',4 if not fault else 6))
    uc.mem_write(DMA_CCR,struct.pack('<I',0x30af))
    instructions=[];returns=[];clock=[0]
    def on_code(_uc,addr,size,_):
        instructions.append(addr)
        if len(instructions)>30000:raise AssertionError('ISR exceeded 30,000 emulated instructions')
        if addr==RETURN:
            returns.append(True)
            _uc.emu_stop()
    def on_mem_read(_uc,access,addr,size,value,_):
        if addr==TIM_CNT:
            clock[0]+=1750
            _uc.mem_write(TIM_CNT,struct.pack('<I',clock[0]))
    uc.hook_add(UC_HOOK_CODE,on_code)
    uc.hook_add(UC_HOOK_MEM_READ,on_mem_read,begin=TIM_CNT,end=TIM_CNT+3)
    uc.reg_write(UC_ARM_REG_SP,STACK)
    uc.reg_write(UC_ARM_REG_LR,RETURN|1)
    uc.reg_write(UC_ARM_REG_PC,CODE+sym['gp0_irq_entry']|1)
    uc.emu_start(CODE+sym['gp0_irq_entry']|1,0,count=30000)
    assert returns,'module SRAM IRQ trampoline did not return'
    count,peak,work,missed,halves=struct.unpack_from('<IIIII',uc.mem_read(CTX+36,20))
    error=uc.mem_read(CTX+58,1)[0]
    active=uc.mem_read(CTX+57,1)[0]
    ccr=struct.unpack('<I',uc.mem_read(DMA_CCR,4))[0]
    pcm_samples=struct.unpack('<I',uc.mem_read(CTX+24,4))[0] # dec.samples at 8+16
    if fault:
        assert error==2 and active==0 and ccr==0 and missed==1,(error,active,ccr,missed)
    else:
        assert error==0 and active==1 and count==1 and halves==1,(error,count,halves)
    assert len(instructions)<9000,len(instructions)
    return {'result':'PASS','compiled_cortex_m0':True,'module_bytes':len(blob),
            'irq_entry_offset':sym['gp0_irq_entry'],
            'context_literal_offset':sym['gp0_irq_context_word'],
            'irq_body_offset':sym['gp0_irq_body'],
            'executed_isr_instructions':len(instructions),
            'correct_pc_relative_context':True,'normal_half':not fault,
            'irq_error':error,'half_interrupts':count,'processed_halves':halves,
            'peak_mock_timer_cycles':peak,'pcm_samples':pcm_samples,
            'faulted_dma_disabled':fault and ccr==0,
            'real_hardware_timing_tested':False,'physical_rf_tested':False}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--error-path',action='store_true')
    p.add_argument('--json',type=Path)
    a=p.parse_args()
    r=emulate(a.error_path)
    if a.json:a.json.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))
if __name__=='__main__':main()
