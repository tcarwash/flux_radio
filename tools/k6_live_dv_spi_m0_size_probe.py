#!/usr/bin/env python3
"""Build a NOT-FLASHABLE ARMv6-M size probe: Sotto slim + SPI + guard TX queue.

The *only* function binding the actual radio's SPI context is deliberately
a NULL stub. This ELF cannot read K6 SPI and MUST NOT be used as a firmware
or module image. It proves the ARM code linker size/ABI layout in isolation,
not IRQ-timed PA11 sampling, SPI transfer speed, RF or live DV.

No physical device, serial port, firmware, SDR or SPI flash is accessed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
SOTTO=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed')
KEEP=('slim_enc_bytes','slim_enc_init','slim_encode10','slim_flush',
      'dv_flash_configure','dv_flash_fault','dv_arm_begin','dv_arm_push',
      'dv_arm_finish','dv_arm_peek','dv_arm_confirm','dv_arm_queue_depth',
      'dv_arm_state_bytes')


def call(args):
    return subprocess.run(list(map(str,args)),check=True,timeout=45,
                          capture_output=True,text=True).stdout


def build(out:Path,sotto:Path=SOTTO,*,emulator_model=False):
    if out.exists():raise FileExistsError('Refuse overwrite of ARM size-probe output directory')
    out.mkdir(parents=True,mode=0o700)
    inc=call(['clang','-print-resource-dir']).strip()
    flags=['--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb',
           '-mfloat-abi=soft','-nostdinc',
           '-isystem',str(sotto/'cm4/inc'),'-isystem',str(Path(inc)/'include'),
           '-ffunction-sections','-fdata-sections','-std=c99',
           '-Wall','-Wextra','-Werror',
           '-DSOTTO_V36','-DSOTTO_PITCH_THIRD','-DFX_NARROW_MUL',
           '-I',str(sotto),'-Oz']
    generated=out/'NOT-FLASHABLE-null-context.c'
    if emulator_model:
        generated.write_text('''#include <stdint.h>
#include "dv_live_slim_spi.h"
/* UNICORN ONLY! No real K6 hardware register/SPI operations. */
dv_flash_ctx_t *dv_flash_active(void){return (dv_flash_ctx_t *)(uintptr_t)0x20003c00u;}
int dv_emulated_spi_read(uint32_t addr,void *buf,unsigned n,void *ctx){
  uint8_t *d=(uint8_t *)buf;
  const uint8_t *s=(const uint8_t *)(uintptr_t)addr;
  unsigned i;
  (void)ctx;
  for(i=0;i<n;i++)d[i]=s[i];
  return 0;
}
''')
    else:
        generated.write_text('''#include <stdint.h>
#include "dv_live_slim_spi.h"
/* Never link this placeholder into real firmware! */
dv_flash_ctx_t *dv_flash_active(void) { return (dv_flash_ctx_t *)0; }
''')
    sources={
        'codec':sotto/'sotto_fx_slim.c',
        'tables':sotto/'fx_tables.c',
        'shim':sotto/'cm4/shim.c',
        'runtime':sotto/'cm0/rt.c',
        'guard':ROOT/'c/k6/mod/dv_live_guard_m0_probe.c',
        'spi':ROOT/'c/k6/mod/dv_live_slim_spi.c',
        'null_context':generated,
    }
    objects=[]
    for name,source in sources.items():
        obj=out/(name+'.o')
        extra=['-include',str(ROOT/'c/k6/mod/dv_live_slim_spi.h')] if name=='codec' else []
        if name in ('shim','runtime'):
            # Upstream cm0_slim_build.sh explicitly prevents Clang from
            # rewriting __aeabi_memclr wrappers into recursive builtin
            # calls. Without this flag the ARM emulator stack underflows.
            extra.extend(['-ffreestanding','-fno-builtin'])
        call(['clang',*flags,'-I',str(ROOT/'c/k6/mod'),*extra,
              '-c',str(source),'-o',str(obj)])
        objects.append(obj)
    obj=out/'divmod.o'
    call(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0',
          '-c',str(sotto/'cm0/divmod.s'),'-o',str(obj)])
    objects.append(obj)
    elf=out/'NOT-FLASHABLE-live-dv-size-probe.elf'
    args=['ld.lld','-T',str(sotto/'cm4/link.ld'),'--gc-sections',
          '-e','slim_encode10']
    for symbol in KEEP:args.extend(['-u',symbol])
    if emulator_model:args.extend(['-u','dv_emulated_spi_read'])
    call([*args,*objects,'-o',elf])
    lines=call(['llvm-size',elf]).splitlines()
    text_bytes,data,bss=map(int,lines[1].split()[:3])
    if bss or data or not 5000<=text_bytes<=11000:
        raise RuntimeError(f'Unsafe ARM linked sections: {text_bytes} text, {data} data, {bss} bss')
    with elf.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    # The radio's 15408B arena is separately used for encoded voice
    # state, stack, triple 10ms PCM buffering, bounded RF packet state.
    # Conservatively use 848B measured from the combined real ARM
    # Unicorn codec+SPI+guard execution, not the earlier standalone 704B.
    remaining=15408-text_bytes-3148-848-3*160-204
    report={
        'artifact':'OFFLINE MEMORY PROBE ONLY - NOT FLASHABLE',
        'target':'thumbv6m-none-eabi Cortex-M0 -Oz',
        'elf_sha256':digest,
        'elf_bytes_disk':elf.stat().st_size,
        'linked_code_and_readonly_bytes':text_bytes,
        'linked_mutable_data_bytes':data,
        'linked_bss_bytes':bss,
        'caller_owned_codec_state_model_bytes':3148,
        'caller_owned_guard_queue_bytes':204,
        'modeled_codec_stack_highwater_bytes':848,
        'candidate_3x_10ms_pcm_buffers_bytes':480,
        'k6_module_arena_bytes':15408,
        'optimistic_remaining_arena_bytes':remaining,
        'spi_context':('Unicorn-only memory-mapped flash model, never real radio SPI' if emulator_model
                       else 'deliberate NULL stub, linker only; NOT runnable'),
        'unicorn_model_only':emulator_model,
        'runtime_proof':False,
        'flashable':False,
        'radio_hardware_access':False,
        'unsafe_to_deploy':True,
        'limitations':['No radio-specific SPI context binding; stub never touches radio.',
                       'Actual K6 PA11 8kHz timer IRQ and SPI transfer timing untested.',
                       'No RF FSK driver/PA snapshot included in this ELF.',
                       'Pointer arena and stack placement are modeled, not verified on chip.',
                       'Real sustained DV TX/RX and privacy-preserving EOT not implemented.'],
    }
    (out/'NOT-FLASHABLE-build-report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--sotto',type=Path,default=SOTTO)
    p.add_argument('--unicorn-flash-model',action='store_true')
    a=p.parse_args()
    x=build(a.out,a.sotto,emulator_model=a.unicorn_flash_model)
    print('ARMv6-M offline core:',x['linked_code_and_readonly_bytes'],
          'B code,',x['linked_bss_bytes'],'B bss; optimistic arena headroom',
          x['optimistic_remaining_arena_bytes'],'B; NOT FLASHABLE')


if __name__=='__main__':main()
