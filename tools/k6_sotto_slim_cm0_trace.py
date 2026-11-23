#!/usr/bin/env python3
"""Trace actual Sotto slim_encode10() calls in upstream Cortex-M0 Unicorn cycle model.

Reads a cross-compiled ARMv6-M slim.elf and 8kHz s16 PCM. Calls the unmodified
slim_encode10 entrypoint on each 80-sample block in Unicorn, records cycles,
and checks compressed bytes against an optional host baseline. Optional
SLIM_TUNE host read-count CSV supplies independent codebook fetch counts.
All actual audio/bitstream and per-call trace are PRIVATE under --out-dir.
No radio, SPI flash, GPIO, UART or actual MCU cycle measurement occurs.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.k6_pa11_sotto_queue_sim import read_counts


def run(elf:Path,pcm_file:Path,upstream_cm0:Path,*,baseline=None,reads_csv=None):
    pcm=pcm_file.read_bytes()
    if not pcm or len(pcm)%160:raise ValueError('PCM must be full 80-sample/10ms chunks')
    spec=importlib.util.spec_from_file_location('flux_sotto_cm0_model',str(upstream_cm0))
    cm0=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cm0)
    cm0.WS=0 # code in RAM (actual K6 module), not flash wait-state execution
    m=cm0.M0(str(elf))
    st,io=cm0.cm4.IO+0x10000,cm0.cm4.IO
    nbytes,_=m.call('slim_enc_bytes')
    m.call('slim_enc_init',st)
    m.paint_stack()
    cycles=[];bitstream=b'';outputs=[]
    for i in range(len(pcm)//160):
        m.mu.mem_write(io,pcm[160*i:160*(i+1)])
        emitted,cyc=m.call('slim_encode10',st,io,io+0x1000)
        cycles.append(cyc)
        if emitted:
            bitstream+=bytes(m.mu.mem_read(io+0x1000,6))
            outputs.append(i)
    flush_cycles=[]
    while True:
        emitted,cyc=m.call('slim_flush',st,io+0x1000)
        flush_cycles.append(cyc)
        if not emitted:break
        bitstream+=bytes(m.mu.mem_read(io+0x1000,6))
    if baseline is not None and bitstream!=baseline.read_bytes():
        raise ValueError('Emulated Cortex-M0 codec output differs from host baseline')
    reads=read_counts(reads_csv) if reads_csv is not None else [0]*len(cycles)
    if len(reads)!=len(cycles):raise ValueError('Codebook read trace and CPU cycles count mismatch')
    rows=[]
    for i,(cyc,nread) in enumerate(zip(cycles,reads)):
        rows.append(dict(block=i,codec_cycles=cyc,codebook_vector_reads=nread,
                         estimated_spi_us=nread*34,
                         total_codec_plus_spi_equiv_cycles=cyc+nread*34*96))
    frames=[sum(cycles[i:i+4]) for i in range(0,len(cycles),4)]
    stats=dict(proof_level='REAL_CORTEX_M0_INSTRUCTION_EMULATOR_NOT_HARDWARE',
       radio_access=False,external_spi_not_emulated=True,
       input_pcm_sha256=hashlib.sha256(pcm).hexdigest(),
       elf_sha256=hashlib.sha256(elf.read_bytes()).hexdigest(),
       encoded_sha256=hashlib.sha256(bitstream).hexdigest(),
       encoded_frames=len(bitstream)//6,
       codec_state_bytes=nbytes,emulator_measured_stack_bytes=m.stack_used(),
       calls_10ms=len(cycles),codec_cycles_sum=sum(cycles),
       codec_mean_mhz=sum(cycles)/len(cycles)*100/1e6,
       codec_worst_10ms_cycles=max(cycles),
       codec_worst_10ms_equiv_mhz=max(cycles)*100/1e6,
       codec_worst_40ms_equiv_mhz=max(frames)*25/1e6,
       total_codebook_reads=sum(reads),max_codebook_reads_per_10ms=max(reads),
       max_codec_plus_spi_10ms_cycles=max(x['total_codec_plus_spi_equiv_cycles'] for x in rows),
       max_codec_plus_spi_10ms_us=max(x['total_codec_plus_spi_equiv_cycles'] for x in rows)/96,
       output_10ms_call_indices=outputs,
       emulator='Unicorn ARMv6-M with upstream Cortex-M0 instruction timing model (WS=0)',
       assumptions=['Codebook vectors in ELF RAM for emulator; each host-instrumented read separately charged 34us estimated SPI',
                    'No interrupt preemption or GPIO sample cycles are included in codec cycles',
                    'Real K6 SPI flash access, memory/interrupt contention, and PCM audio fidelity not verified',
                    'Input real microphone samples and encoded bits remain private local artifacts'])
    return bitstream,rows,stats


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--pcm',type=Path,required=True)
    p.add_argument('--cm0-run',type=Path,required=True)
    p.add_argument('--baseline',type=Path)
    p.add_argument('--reads',type=Path)
    p.add_argument('--out-dir',type=Path,required=True)
    a=p.parse_args()
    if a.out_dir.exists():p.error('Refusing to overwrite previous private bench output')
    bits,rows,report=run(a.elf,a.pcm,a.cm0_run,baseline=a.baseline,reads_csv=a.reads)
    a.out_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    (a.out_dir/'arm-cm0-encoded-private.bit').write_bytes(bits)
    with (a.out_dir/'arm-cm0-10ms-private-trace.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    (a.out_dir/'arm-cm0-summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print('Cortex-M0 modeled calls',report['calls_10ms'],
          'mean MHz',round(report['codec_mean_mhz'],2),
          'max 10ms equivalent MHz',round(report['codec_worst_10ms_equiv_mhz'],2),
          'max SPI reads',report['max_codebook_reads_per_10ms'],
          'stack',report['emulator_measured_stack_bytes'],
          'compressed frames',report['encoded_frames'],'NO RF')

if __name__=='__main__':main()
