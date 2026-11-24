#!/usr/bin/env python3
"""Actual Cortex-M0 API30 mic start/stop callback under a virtual K6 board.

Only validates linked callback invocation, TX lease, PA-mute and timer/vector
cleanup. The simulator does not yet clock 8kHz TIM17 interrupts or model
real PA11 one-wire sigma-delta voltage; do not claim 8kHz audio qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
import subprocess
from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_R0,UC_ARM_REG_LR,UC_ARM_REG_PC,UC_ARM_REG_SP
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sim.k6emu.__main__ import RAM_BASE,SYSCFG_CFGR1
from modem import k6mod
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher
from tools.k6_sotto_rx4800_firmware_gate import matching_image
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_opt_bench import mixed_pcm

SRC=ROOT/"tests/data/k6/sotto_mic30_call_emu.c"

def run(elf:Path,image:Path,tree:Path,*,leave_active:bool=False):
    with tempfile.TemporaryDirectory(prefix="flux-sotto-mic30-") as td:
        tmp=Path(td)
        sha,size=matching_image(elf,image,tmp)
        art=prepare(tree,tmp,pcm=mixed_pcm())
        code,entry=k6mod.build(str(SRC),cflags=[*k6mod.CFLAGS,*(['-DLEAVE_ACTIVE'] if leave_active else [])])
        fw=Dispatcher(elf,image,art["names"])
        try:
            b=fw.board
            # K6Board snapshots the zero-page alias at MEM_MODE changes,
            # but the *hardware* SRAM remap is live: writing SRAM vector
            # 0x20000098 must immediately update alias 0x00000098 while
            # MEM_MODE=3. Keep the firmware's vector read-back guard intact.
            mirror_count=[0]
            def mirror_ram0(uc,access,addr,size,value,user):
                if (b.regs.get(SYSCFG_CFGR1,2)&3)==3 and b.memory_alias_mapped:
                    offset=addr-RAM_BASE
                    if offset>=0 and offset+size<=0x1000:
                        uc.mem_write(offset,value.to_bytes(size,'little'))
                        mirror_count[0]+=1
            b.uc.hook_add(UC_HOOK_MEM_WRITE,mirror_ram0,
                          begin=RAM_BASE,end=RAM_BASE+0xFFF)
            # Unmodified baseline before a module borrows a PA11/TIM17.
            pa_before=b.fd_regs.get(0x36)
            timer_enabled_before=b.nvic_enabled & (1<<22)
            fw.verified_cable_stage(code,entry)
            linked_symbols={}
            for line in subprocess.check_output(['llvm-nm','--demangle',str(elf)],
                                                 text=True).splitlines():
                parts=line.split(maxsplit=2)
                if len(parts)!=3:continue
                if parts[2] in (
                    "aura::app::pager::mod_sotto_mic_stream",
                    "aura::hal::pcm_capture::isr",
                    "aura::hal::pcm_capture::RING"):
                    linked_symbols[parts[2]]=int(parts[0],16)
            assert len(linked_symbols)==3
            mic=linked_symbols["aura::app::pager::mod_sotto_mic_stream"]
            isr=linked_symbols["aura::hal::pcm_capture::isr"]
            ring_var=linked_symbols["aura::hal::pcm_capture::RING"]
            paused=[False]
            fired=[False]
            virtual_cycles=[]
            ring_stats={}
            def pause_at_mic_stop(uc,addr,size,user):
                if fired[0] or uc.reg_read(UC_ARM_REG_R0)!=1:return
                rp=int.from_bytes(uc.mem_read(ring_var,4),'little')
                if not 0x20001900<=rp<=0x20004500:return
                paused[0]=True
                uc.emu_stop()
            b.uc.hook_add(UC_HOOK_CODE,pause_at_mic_stop,begin=mic,end=mic)
            # Isolated simulator-only return trampoline, never executed by
            # firmware on physical SRAM or injected into app scratch.
            trampoline=0x20010000
            b.uc.mem_map(trampoline,4096)
            b.uc.mem_write(trampoline,b"\x70\x47") # bx lr, intercepted
            completed=[False]
            def isr_return(uc,addr,size,user):
                completed[0]=True
                uc.emu_stop()
            b.uc.hook_add(UC_HOOK_CODE,isr_return,begin=trampoline,end=trampoline)
            def inject_samples(_fw):
                if not paused[0]:return
                paused[0]=False
                fired[0]=True
                rp=int.from_bytes(b.uc.mem_read(ring_var,4),'little')
                assert rp!=0
                vector=int.from_bytes(b.uc.mem_read(0x20000098,4),'little')
                assert vector==isr|1,(hex(vector),hex(isr))
                ctx=b.uc.context_save()
                for i in range(95):
                    # 8 kHz TIM17 interrupt cadence, no external SDR or mic.
                    # Switch binary PDM source every 20 samples so the
                    # 4th-order CIC + DC filter has a nonconstant stimulus.
                    b.gpio_external[("A",11)]=(i//20)&1
                    b.cycles+=12_000
                    b.uc.reg_write(UC_ARM_REG_SP,0x20005f00)
                    b.uc.reg_write(UC_ARM_REG_LR,trampoline|1)
                    b.uc.reg_write(UC_ARM_REG_PC,isr|1)
                    completed[0]=False
                    begin_cycles=b.cycles
                    for _ in range(130):
                        b.run(512,512)
                        if completed[0] or b.fault:break
                    if not completed[0] or b.fault:
                        raise RuntimeError(f"ARM TIM17 ISR could not return: {b.fault}")
                    virtual_cycles.append(b.cycles-begin_cycles)
                b.uc.context_restore(ctx)
                data=b.uc.mem_read(rp+640,4)
                import struct
                samples=struct.unpack('<80h',b.uc.mem_read(rp,160))
                ring_stats.update(pcm_nonzero_samples=sum(x!=0 for x in samples),
                                  pcm_min=min(samples),pcm_max=max(samples),
                                  produced=data[0],consumed=data[1],
                                  offset=data[2],lost=data[3],ring=hex(rp),
                                  peak_virtual_cycles=max(virtual_cycles),
                                  sample_invocations=len(virtual_cycles))
                if data[0]<1 or data[3] or ring_stats['pcm_nonzero_samples']<30:
                    raise RuntimeError(f"ARM mic 644-byte ring did not produce PCM: {ring_stats}")
                if max(virtual_cycles)>12000:
                    raise RuntimeError(f"ARM mic ISR exceeded modeled 125us period: {ring_stats}")
            fw.execute(expected_return=11 if leave_active else 10,
                       require_rf=False,require_radio_tx=True,
                       on_quantum=inject_samples)
            if b.fd_regs.get(0x36)!=pa_before:
                raise RuntimeError("API30 mic left PA altered")
            if b.nvic_enabled & (1<<22)!=timer_enabled_before:
                raise RuntimeError("API30 mic left TIM17 interrupt enabled")
            if int.from_bytes(b.uc.mem_read(ring_var,4),'little')!=0:
                raise RuntimeError("API30 mic left a live ring pointer after module return")
            if b.regs.get(0x40014800,0)&1:
                raise RuntimeError("API30 mic left TIM17 peripheral clock running")
            # Ensure normal K6 UART remains usable, no radio active TX.
            return dict(result="PASS",firmware_sha256=sha,firmware_bytes=size,
                        module_bytes=len(code),real_ARM_module=True,
                        module_return=fw.module_ret[0],
                        abandoned_mic_on_module_return=leave_active,
                        radio_enter_tx_calls=fw.trace["radio_enter_tx"],
                        uart_bytes=len(b.uart_tx),pa_restored=True,
                        ram0_live_mirrors=mirror_count[0],
                        tim17_irq_restored=True,tim17_timer_stopped=True,
                        mic_ring_ptr_cleared=True,
                        arm_tim17_isr_invoked=fired[0],
                        modeled_8khz_samples=ring_stats,
                        real_audio_8khz_tested=False,
                        physical_pa11_tested=False,simulated_only=True)
        finally:fw.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--elf",type=Path,required=True)
    p.add_argument("--image",type=Path,required=True)
    p.add_argument("--sotto-tree",type=Path,required=True)
    p.add_argument("--json",type=Path)
    p.add_argument("--abandon-mic",action="store_true",help="test module-return cleanup of an active sampler")
    a=p.parse_args()
    r=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve(),leave_active=a.abandon_mic)
    if a.json:a.json.write_text(json.dumps(r,indent=2)+"\n")
    print(json.dumps(r,indent=2))

if __name__=="__main__":main()
