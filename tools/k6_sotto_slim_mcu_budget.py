#!/usr/bin/env python3
"""Static ARM Cortex-M0 slim Sotto memory feasibility accounting.

Uses a cross-compiled read-only ELF to inspect linked .text/.rodata, then
subtracts the explicitly externalized 30,720-byte shape codebook. State
3148B and stack 676B are *documented estimates* from upstream `sotto-slim.md`
(2026-11-19), NOT hardware stack measurements. This script performs NO radio
access and NO CPU cycle emulation. Model the 15,408B K6 module arena and
variant audio-ring buffers/packet queues; caution that other runtime memory,
SPI tables and alignment can consume the remaining headroom.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

CODEBOOK_BYTES=30720
ARENA_BYTES=15408
MODEL_STATE_BYTES=3148
MODEL_STACK_BYTES=676


def read_elf_sections(elf:Path):
    txt=subprocess.run(['llvm-size','-A',str(elf)],capture_output=True,text=True,check=True).stdout
    secs={}
    for line in txt.splitlines():
        m=re.match(r'^\s*(\.\S+)\s+(\d+)\s+(?:\d+)\s*$',line)
        if m:secs[m.group(1)]=int(m.group(2))
    if '.text' not in secs or '.rodata' not in secs:raise ValueError('Expected ARM .text and .rodata sections missing')
    if secs['.rodata']<CODEBOOK_BYTES:raise ValueError('Embedded codebook bytes not accounted for')
    return secs


def budget(elf:Path,*,codebook_externalized=CODEBOOK_BYTES,
           buffer_counts=(1,2),packet_queues=(0,1,2)):
    if codebook_externalized>CODEBOOK_BYTES or codebook_externalized<0:
        raise ValueError('Invalid codebook exclusion')
    sections=read_elf_sections(elf)
    rom_total=sections['.text']+sections['.rodata']
    residency=rom_total-codebook_externalized
    basic=residency+MODEL_STATE_BYTES+MODEL_STACK_BYTES
    scenarios=[]
    for buffers in buffer_counts:
        for queue_packets in packet_queues:
            pcm_bytes=buffers*640 # 320 samples x signed 16-bit, 40ms each
            queue_bytes=queue_packets*64
            estimate=basic+pcm_bytes+queue_bytes
            scenarios.append(dict(audio_40ms_buffers=buffers,l64_packets_queued=queue_packets,
                                  audio_buffer_bytes=pcm_bytes,packet_queue_bytes=queue_bytes,
                                  lower_bound_bytes=estimate,
                                  remaining_before_unknown_runtime_and_alignment=ARENA_BYTES-estimate,
                                  nominal_fits=estimate<=ARENA_BYTES))
    return dict(artifact=str(elf),artifact_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
                model='Sotto slim Cortex-M0 -Oz static code plus upstream state/stack ESTIMATES',
                elf_sections_bytes=sections,rom_text_rodata_bytes=rom_total,
                codebook_bytes_assumed_externalized=codebook_externalized,
                resident_code_and_tables_bytes=residency,
                upstream_estimated_encoder_state_bytes=MODEL_STATE_BYTES,
                upstream_estimated_stack_bytes=MODEL_STACK_BYTES,
                k6_module_arena_bytes=ARENA_BYTES,
                base_model_bytes=basic,
                room_before_buffers_and_runtime=ARENA_BYTES-basic,
                scenarios=scenarios,
                proof_level='STATIC_MODEL_ONLY',
                notes=['ELF size does not measure RAM allocations/cycles or actual linked K6 module.',
                       'Assumes 30,720B of shape codebook in external SPI, not MCU RAM.',
                       'Model excludes interrupt stack, sampler timers/ISR, call-site stack, packet parser, ABI data and alignment.',
                       'Encoder documented model: 27MHz mean, 29MHz worst plus ~5ms SPI codebook reads per 40ms.',
                       'Unicorn/actual MCU cycle profiling has not been performed.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists():p.error('Refusing to overwrite prior budget report')
    x=budget(args.elf)
    args.out.write_text(json.dumps(x,indent=2)+'\n')
    print('resident code/table bytes',x['resident_code_and_tables_bytes'],
          'arena',x['k6_module_arena_bytes'],'base room',x['room_before_buffers_and_runtime'])
    for s in x['scenarios']:
        print('pcm buffers',s['audio_40ms_buffers'],'queued packets',s['l64_packets_queued'],
              'remaining',s['remaining_before_unknown_runtime_and_alignment'],'nominal fit?',s['nominal_fits'])

if __name__=='__main__':main()
