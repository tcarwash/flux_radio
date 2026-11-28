#!/usr/bin/env python3
"""Real linked ARM firmware op7 RFIC mic route, no PA11 TIM17 ownership."""
from __future__ import annotations
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unicorn import UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from modem import k6mod
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher
from tools.k6_sotto_rx4800_firmware_gate import matching_image
from tools.k6_sotto_virtual_link import prepare
from tools.k6_sotto_opt_bench import mixed_pcm
SRC=ROOT/'tests/data/k6/sotto_mic_gp0_route_call_emu.c'


def run(elf:Path,image:Path,tree:Path)->dict:
    with tempfile.TemporaryDirectory(prefix='sotto-gp0-op7-arm-') as d:
        tmp=Path(d)
        sha,size=matching_image(elf,image,tmp)
        art=prepare(tree,tmp,pcm=mixed_pcm())
        nm=subprocess.check_output(['llvm-nm','--demangle',str(elf)],text=True)
        has_rx='aura::app::pager::mod_fsk4800_rx' in nm
        source=SRC if has_rx else ROOT/'tests/data/k6/sotto_mic_gp0_route_call_api30_emu.c'
        code,entry=k6mod.build(str(source))
        fw=Dispatcher(elf,image,art['names'],require_rx=has_rx)
        try:
            b=fw.board
            before_pa=b.fd_regs.get(0x36)
            irq_before=b.nvic_enabled & (1<<22)
            sym={}
            for line in subprocess.check_output(['llvm-nm','--demangle',str(elf)],text=True).splitlines():
                parts=line.split(maxsplit=2)
                if len(parts)==3 and parts[2] in ('aura::app::pager::mod_sotto_mic_stream',
                                                  'aura::hal::pcm_capture::RING'):
                    sym[parts[2]]=int(parts[0],16)
            assert len(sym)==2,sym
            mic=sym['aura::app::pager::mod_sotto_mic_stream']
            ring=sym['aura::hal::pcm_capture::RING']
            observations=[]
            def check_release(uc,addr,size,user):
                if uc.reg_read(UC_ARM_REG_R0)!=1:return
                mic_reg=b.fd_regs.get(0x30,0)
                active_ring=int.from_bytes(uc.mem_read(ring,4),'little')
                irq=b.nvic_enabled & (1<<22)
                observations.append({'reg30':mic_reg,'mic_adc_enabled':bool(mic_reg & 4),
                                     'ring':active_ring,'tim17_irq':bool(irq),
                                     'timer_cr1':b.regs.get(0x40014800,0),
                                     'pa':b.fd_regs.get(0x36)})
            b.uc.hook_add(UC_HOOK_CODE,check_release,begin=mic,end=mic)
            fw.verified_cable_stage(code,entry)
            fw.execute(expected_return=17,require_rf=False,require_radio_tx=True)
            assert len(observations)==1,observations
            o=observations[0]
            assert o['mic_adc_enabled'],o
            assert o['ring']==0 and not o['tim17_irq'] and not (o['timer_cr1']&1),o
            assert b.fd_regs.get(0x36)==before_pa
            assert b.nvic_enabled & (1<<22)==irq_before
            assert int.from_bytes(b.uc.mem_read(ring,4),'little')==0
            return {'result':'PASS','firmware_sha256':sha,'firmware_bytes':size,
                    'module_bytes':len(code),'real_ARM_firmware':True,
                    'route_only_op7_accepts_with_tx_lease':True,
                    'route_only_op7_rejects_without_tx':True,
                    'route_only_op7_rejects_reentry':True,
                    'no_TIM17_irq_or_pcm_ring':True,
                    'pa_restored':True,'module_return':fw.module_ret[0],
                    'observation_before_route_release':o,
                    'physical_gp0_tested':False,'simulated_only':True,
                    'api_level':31 if has_rx else 30}
        finally:fw.close()

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--image',type=Path,required=True)
    p.add_argument('--sotto-tree',type=Path,required=True)
    p.add_argument('--json',type=Path)
    a=p.parse_args()
    r=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve())
    if a.json:a.json.write_text(json.dumps(r,indent=2)+'\n')
    print(json.dumps(r,indent=2))
