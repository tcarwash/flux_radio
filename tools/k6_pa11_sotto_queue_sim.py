#!/usr/bin/env python3
"""Deterministic OFFLINE ping-pong PCM buffer feasibility sensitivity.

Assume 80 new 8kHz PA11 PCM samples are ready every 10ms. Sampling ISR
preempts a codec task. Available background CPU cycles are the remaining
96MHz budget after per-sample ADC, ISR overhead and a 5% system reserve.
Model the slim encoder's 29MHz worst *40ms average* as either evenly
spread across four 10ms calls or concentrated into the fourth call.
The codebook's 150x34us/40ms SPI cost is similarly distributed or bursty.

This is NOT an MCU emulator or measured per-call cycles; a real K6 must
measure actual slim_encode10 burst times, ADC jitter and IRQ/SPI behavior.
"""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.k6_pa11_sotto_realtime_budget import (MCU_HZ,PCM_HZ,BLOCK_SAMPLES,
   SPI_VECTORS_PER_40MS,SPI_READ_US,CODEC_WORST_HZ,
   ISR_OVERHEAD_CYCLES_PER_SAMPLE,OTHER_CPU_RESERVE_FRACTION,capture_params)

PATTERNS={
 'even': ((1.,1.,1.,1.),(1.,1.,1.,1.)),
 'bursty': ((.75,.75,.75,1.75),(0.,0.,0.,4.)),
 'severe': ((.5,.5,.5,2.5),(0.,0.,0.,4.)),
}


def simulate(meta,*,turns=90,buffer_count=2,pattern='even',blocks=100,codebook_reads=None,codec_cycles=None,adc_cycles_per_sample=None):
    if turns<=0 or buffer_count<2 or blocks<4 or pattern not in PATTERNS:
        raise ValueError('Invalid offline queue simulation parameters')
    if codebook_reads is not None and (len(codebook_reads)<blocks or any(v<0 for v in codebook_reads)):
        raise ValueError('Incomplete/invalid codebook read trace')
    if codec_cycles is not None and (len(codec_cycles)<blocks or any(v<0 for v in codec_cycles)):
        raise ValueError('Incomplete/invalid Cortex-M0 cycle trace')
    eq=capture_params(meta)['observed_equivalent_cycles_per_turn']
    period=MCU_HZ/PCM_HZ
    sample_cycles=(turns*eq if adc_cycles_per_sample is None else adc_cycles_per_sample)+ISR_OVERHEAD_CYCLES_PER_SAMPLE
    background_cycles_per_10ms=BLOCK_SAMPLES*(period-sample_cycles)-MCU_HZ*OTHER_CPU_RESERVE_FRACTION/100
    # Each weight pattern sums to 4, so 40ms totals are conserved.
    codec_pattern,spi_pattern=PATTERNS[pattern]
    codec_per_10ms=CODEC_WORST_HZ/100
    spi_per_10ms=SPI_VECTORS_PER_40MS*SPI_READ_US*1e-6*MCU_HZ/4
    tasks=[]
    completed=0
    max_pending=0
    max_wait_cycles=0.
    overflow=None
    events=[]
    for index in range(blocks):
        weight=index%4
        spi_work=(codebook_reads[index]*SPI_READ_US*1e-6*MCU_HZ
                  if codebook_reads is not None else spi_per_10ms*spi_pattern[weight])
        work=(codec_cycles[index] if codec_cycles is not None else codec_per_10ms*codec_pattern[weight])+spi_work
        tasks.append([index,work])
        if len(tasks)>=buffer_count and overflow is None:
            # A separate buffer is required for the next producer block.
            overflow=index
        max_pending=max(max_pending,len(tasks))
        capacity=max(0.,background_cycles_per_10ms)
        while capacity>0 and tasks:
            k,remaining=tasks[0]
            used=min(capacity,remaining)
            capacity-=used
            remaining-=used
            if remaining<1e-7:
                tasks.pop(0)
                completed+=1
            else:tasks[0][1]=remaining
        backlog=sum(x[1] for x in tasks)
        max_wait_cycles=max(max_wait_cycles,backlog)
        if index<12:events.append(dict(ready_block=index,remaining_tasks=len(tasks),
            remaining_work_cycles=round(backlog),work_added_cycles=round(work)))
    return dict(pattern=pattern,turns=turns,buffer_count=buffer_count,
       adc_workload_source=('real_pa11_gpio_armv6m_instruction_emulator' if adc_cycles_per_sample is not None else 'real_capture_scaled_equivalent_cycles_per_turn'),
       adc_cycles_per_sample=round(sample_cycles-ISR_OVERHEAD_CYCLES_PER_SAMPLE),
       codec_workload_source=('real_mic_slim_encode10_cortex_m0_instruction_emulator' if codec_cycles is not None else 'assumed_uniform_or_bursty_29MHz'),
       spi_read_workload_source=('actual_recorded_mic_slim_encode10_read_counts'
            if codebook_reads is not None else 'synthetic_assumed_distribution'),
       simulated_10ms_blocks=blocks,nominal_available_background_cycles_per_10ms=round(background_cycles_per_10ms),
       nominal_background_time_us=round(background_cycles_per_10ms/MCU_HZ*1e6,2),
       first_buffer_overrun_block=overflow,
       no_buffer_overrun=overflow is None,
       peak_pending_10ms_chunks=max_pending,
       max_backlog_cycles=round(max_wait_cycles),
       completed_10ms_blocks=completed,
       final_pending_10ms_chunks=len(tasks),
       first_12_events=events,
       proof_level='OFFLINE_BURST_SENSITIVITY_NOT_MCU_MEASUREMENT',
       warning='Per-10ms slim codec and SPI workload distribution is ASSUMED; must profile real MCU before claiming deadline safety.')


def read_counts(path:Path):
    with path.open(newline='') as fd:
        rows=list(csv.DictReader(fd))
    counts=[]
    for index,row in enumerate(rows):
        if int(row['10ms_block_index'])!=index:
            raise ValueError('Out-of-order codebook read event')
        counts.append(int(row['codebook_vectors_read_this_call']))
    if not counts or any(x<0 for x in counts):
        raise ValueError('Invalid codebook read trace')
    return counts


def read_pa11_cycles(path:Path):
    data=json.loads(path.read_text())
    if data['proof_level']!='ACTUAL_ARMV6M_PA11ADC_CODE_UNICORN_GPIO_MEMORY_ONLY':
        raise ValueError('Not a Cortex-M0 PA11 emulator trace')
    rows={}
    for r in data['scenarios']:
        if r['idr_pa11']==0:
            rows[r['turns']]=r['cycles']
        elif r['turns'] in rows and rows[r['turns']]!=r['cycles']:
            raise ValueError('Data-dependent ADC cycles')
    return rows


def read_cm0_trace(path:Path):
    with path.open(newline='') as fd:rows=list(csv.DictReader(fd))
    if not rows:raise ValueError('Empty Cortex-M0 cycle trace')
    cycles=[];reads=[]
    for i,r in enumerate(rows):
        if int(r['block'])!=i:raise ValueError('Out-of-order Cortex-M0 cycle trace')
        cycles.append(int(r['codec_cycles']))
        reads.append(int(r['codebook_vector_reads']))
    if any(v<0 for v in cycles+reads):raise ValueError('Negative Cortex-M0 trace values')
    return cycles,reads


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture-meta',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--reads',type=Path,default=None,help='Optional host-instrumented genuine slim per-call SPI read counts')
    p.add_argument('--cm0-trace',type=Path,default=None,help='Optional real mic slim Cortex-M0 modeled cycles and host read counts')
    p.add_argument('--pa11-trace',type=Path,default=None,help='Optional actual PA11 C GPIO loop Cortex-M0 emulated cycles')
    a=p.parse_args()
    if a.out.exists():p.error('Refusing overwrite')
    meta=json.loads(a.capture_meta.read_text())
    scenarios=[simulate(meta,turns=w,buffer_count=n,pattern=pattern)
       for w in (200,100,90,80) for n in (2,3,4) for pattern in PATTERNS]
    if a.reads is not None:
        reads=read_counts(a.reads)
        scenarios.extend(simulate(meta,turns=w,buffer_count=n,pattern=pattern,blocks=len(reads),codebook_reads=reads)
            for w in (90,80) for n in (2,3,4,5) for pattern in ('even','bursty'))
    if a.cm0_trace is not None:
        cycles,reads=read_cm0_trace(a.cm0_trace)
        scenarios.extend(simulate(meta,turns=w,buffer_count=n,pattern='even',blocks=len(cycles),
                        codebook_reads=reads,codec_cycles=cycles)
            for w in (90,80,70,60) for n in (2,3,4,5,6))
    if a.pa11_trace is not None:
        if a.cm0_trace is None: p.error('--pa11-trace requires --cm0-trace')
        adc=read_pa11_cycles(a.pa11_trace)
        scenarios.extend(simulate(meta,turns=w,buffer_count=n,pattern='even',blocks=len(cycles),
                        codebook_reads=reads,codec_cycles=cycles,adc_cycles_per_sample=adc[w])
            for w in (80,90,100,120,200) for n in (2,3,4,5,6))
    r=dict(experiment='offline 10ms PA11 slim Sotto double-buffer and burst sensitivity',
      no_hardware_access=True,results=scenarios)
    a.out.write_text(json.dumps(r,indent=2)+'\n')
    for x in scenarios:
        if (x['adc_workload_source']=='real_pa11_gpio_armv6m_instruction_emulator'
            or x['codec_workload_source']=='real_mic_slim_encode10_cortex_m0_instruction_emulator'
            or x['spi_read_workload_source']=='actual_recorded_mic_slim_encode10_read_counts'
            or x['turns'] in (90,100) and x['buffer_count'] in (2,3)):
            print('W',x['turns'],'buffers',x['buffer_count'],x['pattern'],
                  x['spi_read_workload_source'],
                  'overrun',x['first_buffer_overrun_block'],'completed',x['completed_10ms_blocks'])

if __name__=='__main__':main()
