#!/usr/bin/env python3
"""Execute the actual compiled Sotto live encoder in linked API28-31 ARM firmware.

Real compiled ARM loader, module, Sotto codec, TIM17 ISR and FSK callback;
only PTT, PA11 PDM, physical buses and RF air are simulated. No hardware IO.
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
from unicorn import UC_HOOK_CODE,UC_HOOK_MEM_WRITE
from unicorn.arm_const import UC_ARM_REG_LR,UC_ARM_REG_PC,UC_ARM_REG_SP,UC_ARM_REG_R0,UC_ARM_REG_R1
from modem import k6mod
from sim.k6emu.__main__ import RAM_BASE,SYSCFG_CFGR1
from sim.k6emu.rf.virtual_link import Channel,VirtualAir,VirtualTxBackend,AirSource
from sim.k6emu.rf.backend import RFBackend
from tools.k6_fsk_virtual_link import HZ
from tools.k6_fsk_emulator_replay import receive
from tools.k6_sotto_module import build as build_live
from tools.k6_sotto_rx4800_dispatch_gate import Dispatcher,ENTRY,STOP,SRC as RX_MODULE
from tools.k6_sotto_rx4800_firmware_gate import matching_image
from tools.k6_sotto_virtual_link import prepare,decode_session
from tools.k6_sotto_opt_bench import mixed_pcm
from tools.k6_user_store_migrate import BASE,END,compact


def run(elf:Path,image:Path,tree:Path,blocks:int=12,max_quanta:int=24000):
    with tempfile.TemporaryDirectory(prefix='flux-sotto-live-arm-') as td:
        temp=Path(td)
        sha,bytes_=matching_image(elf,image,temp)
        artifacts=prepare(tree,temp,pcm=mixed_pcm())
        code,entry=build_live(True,True)
        fw=Dispatcher(elf,image,artifacts['names'])
        try:
            b=fw.board
            # This gate jumps directly into the compiled pager from a booted
            # firmware, without constructing main.rs's LCD display closure.
            # Execute the real module LCD callback and separate-stack
            # trampoline, but terminate at the physical display backend;
            # otherwise its uninitialized SPI/GPIO closure faults at
            # 0xA5A5A5xx. Capture the actual MOD_SCREEN framebuffer at that
            # boundary to verify the startup and PTT UI was *flushed*.
            nm=subprocess.check_output(['llvm-nm','--demangle',str(elf)],text=True,timeout=20)
            lcd_addresses=[]
            for line in nm.splitlines():
                f=line.split(maxsplit=2)
                if len(f)==3 and f[2].startswith('aura::ui::mod_flush::'):
                    lcd_addresses.append(int(f[0],16))
            if len(lcd_addresses)!=1:
                raise RuntimeError(f'Expected one linked LCD hardware flush boundary: {lcd_addresses}')
            lcd_frames=[]
            screen=next((int(f[0],16) for line in nm.splitlines()
                         if (f:=line.split(maxsplit=2)) and len(f)==3
                         and f[2]=='aura::app::pager::MOD_SCREEN'),None)
            if screen is None:
                raise RuntimeError('No linked MOD_SCREEN buffer')
            def virtual_lcd(uc,addr,size,user):
                lcd_frames.append(bytes(uc.mem_read(screen,126)))
                uc.reg_write(UC_ARM_REG_R0,0) # successful physical LCD write
                uc.reg_write(UC_ARM_REG_PC,uc.reg_read(UC_ARM_REG_LR)|1)
            b.uc.hook_add(UC_HOOK_CODE,virtual_lcd,
                          begin=lcd_addresses[0],end=lcd_addresses[0])
            # Install exact read-only codec resources in virtual packed KUL2
            # storage, not a substitute data oracle for the module's sio_init.
            cb=artifacts['cb']
            deck=k6mod.image(artifacts['deck'],0,serial=77,key=None,level=31,lib=14,cards=True)
            packed,manifest=compact({14:deck})
            assert len(packed)==END-BASE and manifest[0][0]==14
            b.flash_data[0x100000:0x100000+len(cb)]=cb
            b.flash_data[BASE:END]=packed
            cb_hash=hashlib.sha256(b.flash_data[0x100000:0x100000+len(cb)]).hexdigest()
            deck_hash=hashlib.sha256(b.flash_data[BASE:END]).hexdigest()
            chunks,envelope=fw.verified_cable_stage(code,entry,max_call_quanta=4000)
            # UART programming cable is absent, and physical PTT is held (active LOW).
            b.set_gpio('A',10,0)
            air=VirtualAir(Channel())
            rf=VirtualTxBackend(air,240_000,HZ)
            b.rf_backend=rf
            baseline_pa=b.fd_regs.get(0x36)
            # A device's SRAM vector remap is live; keep the simulator alias
            # synchronized exactly as in the established API30 mic gate.
            def mirror(uc,access,addr,size,value,user):
                if (b.regs.get(SYSCFG_CFGR1,2)&3)==3 and b.memory_alias_mapped:
                    off=addr-RAM_BASE
                    if 0<=off and off+size<=0x1000:
                        uc.mem_write(off,value.to_bytes(size,'little'))
            b.uc.hook_add(UC_HOOK_MEM_WRITE,mirror,begin=RAM_BASE,end=RAM_BASE+0xfff)
            syms=fw.syms
            isr=syms.get('pcm_isr')
            ring_symbol=syms.get('pcm_ring')
            if isr is None or ring_symbol is None:
                for line in subprocess.check_output(['llvm-nm','--demangle',str(elf)],text=True).splitlines():
                    fields=line.split(maxsplit=2)
                    if len(fields)!=3:continue
                    if fields[2]=='aura::hal::pcm_capture::isr':isr=int(fields[0],16)
                    if fields[2]=='aura::hal::pcm_capture::RING':ring_symbol=int(fields[0],16)
            if isr is None or ring_symbol is None:
                raise RuntimeError('Missing linked ARM TIM17 ISR or ring pointer')
            trampoline=0x20010000
            b.uc.mem_map(trampoline,4096)
            b.uc.mem_write(trampoline,b'\x70\x47')
            completed=[False]
            def on_isr_return(uc,addr,size,user):
                completed[0]=True
                uc.emu_stop()
            b.uc.hook_add(UC_HOOK_CODE,on_isr_return,begin=trampoline,end=trampoline)
            stats=dict(samples=0,peak_isr_cycles=0,max_depth=0,lost=0,produced=0,consumed=0,
                       initial_tx_cycles=None,released_at=None)
            next_isr=None
            def fire_isr():
                saved=b.uc.context_save()
                start=b.cycles
                try:
                    b.gpio_external[('A',11)]=(stats['samples']//32)&1
                    b.uc.reg_write(UC_ARM_REG_SP,0x20005f00)
                    b.uc.reg_write(UC_ARM_REG_LR,trampoline|1)
                    b.uc.reg_write(UC_ARM_REG_PC,isr|1)
                    completed[0]=False
                    for _ in range(130):
                        b.run(512,512)
                        if completed[0] or b.fault:break
                    if not completed[0] or b.fault:
                        raise RuntimeError(f'Compiled ARM ISR failed to return: {b.fault}')
                finally:b.uc.context_restore(saved)
                stats['samples']+=1
                stats['peak_isr_cycles']=max(stats['peak_isr_cycles'],b.cycles-start)
                if stats['peak_isr_cycles']>12000:
                    raise RuntimeError('ISR exceeds modeled 125us period')
            fw.returned=False
            for reg,value in ((UC_ARM_REG_R0,syms['app']),
                              (UC_ARM_REG_R1,0x20005800),
                              (UC_ARM_REG_SP,0x20005fe0),
                              (UC_ARM_REG_LR,STOP|1),
                              (UC_ARM_REG_PC,fw.linked[ENTRY]|1)):
                b.uc.reg_write(reg,value)
            for quantum in range(max_quanta):
                b.run(2048,1024)
                if b.fault or fw.returned:break
                rp=int.from_bytes(b.uc.mem_read(ring_symbol,4),'little')
                if rp:
                    if next_isr is None:
                        next_isr=b.cycles+12000
                        stats['initial_tx_cycles']=b.cycles
                    while next_isr is not None and b.cycles>=next_isr:
                        fire_isr()
                        next_isr+=12000
                    produced,consumed,offset,lost=b.uc.mem_read(rp+640,4)
                    stats['produced']=max(stats['produced'],produced)
                    stats['consumed']=max(stats['consumed'],consumed)
                    stats['max_depth']=max(stats['max_depth'],(produced-consumed)&255)
                    stats['lost']=max(stats['lost'],lost)
                    if stats['released_at'] is None and stats['samples']>=blocks*80:
                        b.set_gpio('A',10,1)
                        stats['released_at']=b.cycles
                    if lost:break
                elif next_isr is not None:next_isr=None
            if b.fault:raise RuntimeError(f'Compiled ARM live encoder fault: {b.fault}')
            if not fw.returned:
                raise RuntimeError(f'ARM live module did not finish; quantum={quantum}; stats={stats}; RF={rf.tx_status()}')
            if len(lcd_frames)<1:
                raise RuntimeError('Live module did not invoke the physical LCD flush callback')
            status=rf.tx_status()
            if not fw.module_ret or fw.module_ret[0]<=0:
                raise RuntimeError(f'ARM live encoder failed: trace={fw.trace} module={fw.module_ret}; stats={stats}; RF={status}; UART={bytes(b.uart_tx)[-500:]!r} boardcycles={b.cycles} quantum={quantum}')
            if stats['lost'] or stats['samples']<blocks*80 or status['active']:
                raise RuntimeError(f'Incomplete live PCM or RF cleanup: stats={stats} RF={status}')
            if b.fd_regs.get(0x36)!=baseline_pa:
                raise RuntimeError('RF power amplifier not restored')
            if hashlib.sha256(b.flash_data[0x100000:0x100000+len(cb)]).hexdigest()!=cb_hash or hashlib.sha256(b.flash_data[BASE:END]).hexdigest()!=deck_hash:
                raise RuntimeError('Virtual codec resource flash modified')
            if not air.bursts:raise RuntimeError('Live ARM encoder emitted no virtual RF')
            virtual_rx=RFBackend(AirSource(air,240_000,air.bursts[0].hz))
            try:
                frames,decoder=receive(virtual_rx,tune=air.bursts[0].hz,
                                       duration=air.bursts[-1].end+0.2,frame_bytes=66,
                                       threshold=4,poll_us=500)
            finally:virtual_rx.close()
            codec,session=decode_session(frames)
            if not session['complete'] or not codec:
                raise RuntimeError(f'Independent virtual RF/codec did not close: frames={len(frames)} session={session}')
            # Boot a SECOND compiled K6 ARM firmware instance and run its
            # *actual* API31 4800-baud FSK receiver module through the normal
            # C cable ingress/dispatcher. Independent host IQ validation above
            # is kept as an additional RF oracle, not replaced by TX bytes.
            rx_code,rx_entry=k6mod.build(str(RX_MODULE))
            radio_rx=Dispatcher(elf,image,artifacts['names'])
            try:
                radio_rx.verified_cable_stage(rx_code,rx_entry)
                radio_rx.virtual_air(air)
                # A second booted radio needs receiver acquisition time before
                # the first packet; no virtual payload bytes are transferred.
                for burst in air.bursts:
                    burst.start+=0.8
                    burst.end+=0.8
                try:
                    rx_fsk=radio_rx.execute(expected_return=len(frames),
                        expected_rf_frames=len(frames))
                except Exception as exc:
                    raise RuntimeError(f'{exc}; RX stats={radio_rx.rf.status().get("fsk_rx")}, module={radio_rx.module_ret}, trace={radio_rx.trace}, timing={[(round(x.start,2),round(x.end,2),x.hz) for x in air.bursts]}') from exc
                arm_rx=dict(real_arm_module=True,real_arm_api31=True,
                    packets=rx_fsk['frames'],fifo_words=rx_fsk['words'],
                    overruns=rx_fsk['overruns'],underflows=rx_fsk['underflows'],
                    return_value=radio_rx.module_ret[0],
                    dispatcher_calls=radio_rx.trace['run_module'],
                    rx_api_calls=radio_rx.trace['api31'])
            finally:radio_rx.close()
            # Receiver's independent native Sotto decoder and 20-ms speaker
            # queue: validates real ARM microphone/codec output can become PCM.
            # This is NOT a radio CPU or speaker-driver latency measurement.
            import numpy as np
            from tools.k6_sotto_e2e_mock import native_decoder,decode_pcm
            from tools.k6_sotto_rx_stream import SottoRxPaced
            decoder_lib=native_decoder(tree,temp/'sotto-lr-decoder.so')
            receiver=SottoRxPaced(decoder_lib,capacity_frames=16)
            audio_blocks=[]
            try:
                for off in range(0,len(codec),48):
                    receiver.enqueue_packet(codec[off:off+48])
                    while (chunk:=receiver.speaker_next()) is not None:
                        audio_blocks.append(chunk)
                receiver.end()
                while (chunk:=receiver.speaker_next()) is not None:
                    audio_blocks.append(chunk)
                assert receiver.state=='finished'
                assert receiver.queued_encoded_frames==receiver.queued_pcm_blocks==0
                assert receiver.decoder.output_blocks==receiver.decoder.played_blocks
                audio=np.concatenate(audio_blocks)
                if not np.array_equal(audio,decode_pcm(decoder_lib,codec)):
                    raise RuntimeError('Speaker stream and independent native decoder PCM mismatch')
                playback=dict(verified=True,codec_frames=len(codec)//6,
                    pcm_samples=len(audio),speaker_blocks=len(audio_blocks),
                    peak_pcm_blocks=receiver.peak_pcm_blocks,
                    peak_encoded_frames=receiver.peak_encoded_frames,
                    pcm_sha256=hashlib.sha256(audio.tobytes()).hexdigest(),
                    peak_amplitude=int(np.max(np.abs(audio.astype('i4')))),
                    host_decoder_only=True,radio_speaker_driver_tested=False)
            finally:receiver.close()
            return dict(result='PASS',firmware_sha256=sha,firmware_bytes=bytes_,
                        live_module_bytes=len(code),envelope_bytes=envelope,cable_chunks=chunks,
                        real_arm_live_codec=True,real_arm_mic_isr=True,
                        physical_mic=False,physical_rf=False,real_radio=False,
                        radio_enter_tx_calls=fw.trace['radio_enter_tx'],
                        compiled_module_return=fw.module_ret[0],
                        pcm=stats,rf=status,frames=len(frames),session=session,
                        virtual_iq=decoder['fsk'],compiled_arm_receiver=arm_rx,playback=playback,
                        pa_restored=True,resources_read_only=True,
                        lcd_flushed_frames=len(lcd_frames),lcd_virtual_bus=True)
        finally:fw.close()

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',required=True,type=Path)
    p.add_argument('--image',required=True,type=Path)
    p.add_argument('--sotto-tree',required=True,type=Path)
    p.add_argument('--blocks',type=int,default=12)
    p.add_argument('--max-quanta',type=int,default=24000)
    p.add_argument('--json',type=Path)
    a=p.parse_args()
    o=run(a.elf.resolve(),a.image.resolve(),a.sotto_tree.resolve(),a.blocks,a.max_quanta)
    if a.json:a.json.write_text(json.dumps(o,indent=2)+'\n')
    print(json.dumps(o,indent=2))
