#!/usr/bin/env python3
"""Real linked Cortex-M0 GP0 IRQ9 firmware service start/cleanup, no RF.

Tests the exact 120KiB image with RX4800 omitted, API30 present. This is a
software emulator, not a cycle-accurate DMA capture or physical RF test.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher
from tools.k6_sotto_rx4800_firmware_gate import matching_image
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_opt_bench import mixed_pcm
SRC=ROOT/'tests/data/k6/sotto_gp0_irq_service_call_emu.c'


def run(elf:Path,image:Path,tree:Path)->dict:
    with tempfile.TemporaryDirectory(prefix='sotto-gp0-irq30-arm-') as d:
        tmp=Path(d)
        sha,size=matching_image(elf,image,tmp)
        art=prepare(tree,tmp,pcm=mixed_pcm())
        code,entry=k6mod.build(str(SRC))
        fw=Dispatcher(elf,image,art['names'],require_rx=False)
        try:
            b=fw.board
            # SRAM remap aliases the first 4KiB at address zero on real K6.
            # Unicorn does not automatically mirror writes to the SRAM vector.
            def mirror(uc,access,addr,size,value,user):
                if (b.regs.get(0x40010000,2)&3)==3 and b.memory_alias_mapped:
                    off=addr-0x20000000
                    if 0<=off and off+size<=0x1000:
                        uc.mem_write(off,value.to_bytes(size,'little'))
            b.uc.hook_add(UC_HOOK_MEM_WRITE,mirror,
                          begin=0x20000000,end=0x20000fff)
            nm=subprocess.check_output(['llvm-nm','--demangle',str(elf)],text=True)
            # The opt-in GPF1/GPT1 timing overlay replaces API30's normal
            # microphone callback with a tracing wrapper. Resolve the exact
            # live API-table callback, and require it to match a linked
            # known implementation, rather than assume the untraced symbol.
            ring_symbols=('SOTTO_GP0_PCM_RING','SOTTO_GP0_RAW_RING')
            mic_symbols=('aura::app::pager::mod_sotto_mic_stream',
                         'aura::app::pager::mod_sotto_mic_stream_traced')
            sym={}
            for l in nm.splitlines():
                f=l.split(maxsplit=2)
                if len(f)==3 and f[2] in (*ring_symbols,*mic_symbols):
                    sym[f[2]]=int(f[0],16)
            assert all(k in sym for k in ring_symbols),sym
            assert any(k in sym for k in mic_symbols),sym
            names=art['names']
            assert names.count('mic_stream')==1,names
            slot=names.index('mic_stream')
            registered=int.from_bytes(b.uc.mem_read(fw.syms['api']+4+4*slot,4),'little')
            assert registered & 1,hex(registered)
            mic=registered & ~1
            assert mic in (sym[k] for k in mic_symbols if k in sym),(
                'API30 mic callback does not match a compiled host service',hex(registered),sym)
            observations=[]
            def observe(uc,addr,sz,user):
                if uc.reg_read(UC_ARM_REG_R0)!=1:return
                def word(a):return int.from_bytes(uc.mem_read(a,4),'little')
                observations.append(dict(
                    route_before_cleanup=b.fd_regs.get(0x34,0)&15,
                    mic_adc_enabled=bool(b.fd_regs.get(0x30,0)&4),
                    pcm_ptr=word(sym[ring_symbols[0]]),
                    raw_ptr=word(sym[ring_symbols[1]]),
                    dma_ccr=b.regs.get(0x40020008,0),
                    dma_ndtr=b.regs.get(0x4002000c,0),
                    vector=word(0x20000064),
                    tim17_cr1=b.regs.get(0x40014800,0),
                    pa=b.fd_regs.get(0x36)))
            b.uc.hook_add(UC_HOOK_CODE,observe,begin=mic,end=mic)
            before_pa=b.fd_regs.get(0x36)
            before_vector=int.from_bytes(b.uc.mem_read(0x20000064,4),'little')
            fw.verified_cable_stage(code,entry)
            fw.execute(expected_return=23,require_rf=False,require_radio_tx=True)
            assert len(observations)==1,observations
            o=observations[0]
            assert o['mic_adc_enabled'] and o['route_before_cleanup']==12,o
            assert o['pcm_ptr'] and o['raw_ptr'] and o['raw_ptr']!=o['pcm_ptr'],o
            assert o['dma_ccr']&1 and o['dma_ndtr']==1024,o
            assert o['tim17_cr1']&1 and o['vector']!=before_vector,o
            assert b.fd_regs.get(0x36)==before_pa,'PA restore failed'
            assert int.from_bytes(b.uc.mem_read(0x20000064,4),'little')==before_vector,'IRQ9 vector leak'
            assert int.from_bytes(b.uc.mem_read(sym[ring_symbols[0]],4),'little')==0,'PCM pointer leak'
            assert int.from_bytes(b.uc.mem_read(sym[ring_symbols[1]],4),'little')==0,'Raw DMA pointer leak'
            assert not b.regs.get(0x40020008,0)&1,'DMA channel left enabled'
            return {'result':'PASS','firmware_sha256':sha,'firmware_bytes':size,
                    'module_bytes':len(code),'api_level':30,'real_arm_firmware':True,
                    'successful_irq_service_start_and_stop':True,
                    'route_restored':True,'vector_restored':True,
                    'pcm_and_dma_pointers_cleared':True,
                    'observation_before_stop':o,'physical_dma_timing_tested':False,
                    'actual_sotto_codec_tested':False}
        finally:fw.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--image',type=Path,required=True)
    p.add_argument('--sotto-tree',type=Path,required=True)
    p.add_argument('--json',type=Path)
    a=p.parse_args()
    r=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve())
    if a.json:a.json.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))
