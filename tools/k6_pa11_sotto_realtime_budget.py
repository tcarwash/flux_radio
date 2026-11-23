#!/usr/bin/env python3
"""OFFLINE PA11 one-pin ADC + slim Sotto encoder CPU scheduling feasibility.

Anchors the PA11 turn-cycle estimate (44+5*HOLD, hold=3) in the *real*
recorded K6 MICVOICE capture's sample count, turns and elapsed 100us ticks.
The measured capture was a continuously running polling converter, not a
preemptible interrupt. Reduced-turn 8kHz operation and interrupt preemption
are HYPOTHESES, not measured RF/mic performance. No hardware I/O.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

MCU_HZ=96_000_000
PCM_HZ=8000
BLOCK_SAMPLES=80
HOLD=3
CODEC_WORST_HZ=29_000_000
CODEC_MEAN_HZ=27_000_000
SPI_VECTORS_PER_40MS=150
SPI_READ_US=34
ISR_OVERHEAD_CYCLES_PER_SAMPLE=300 # sensitivity assumption, NOT measured
OTHER_CPU_RESERVE_FRACTION=.05 # sensitivity assumption, NOT measured


def capture_params(meta):
    n=int(meta['samples']);turns=int(meta['turns']);ticks=int(meta['timing_ticks_100us'])
    if n<=0 or turns<=0 or ticks<=0:raise ValueError('Invalid real capture timing')
    hz=n/(ticks*0.0001)
    measured=float(meta['measured_sample_rate_hz'])
    if abs(hz-measured)>0.05:raise ValueError('Captured timing/rate inconsistent')
    # Equivalent per-turn cycle cost includes loop, conversion/IMA and API
    # overhead; scaling it down to lower W is an intentionally pessimistic
    # model IF per-sample fixed costs are nonnegative. Not a new benchmark.
    per_turn_equivalent=MCU_HZ/(hz*turns)
    return dict(capture_samples=n,capture_turns=turns,capture_ticks_100us=ticks,
                measured_sample_rate_hz=measured,rate_derived_from_ticks_hz=hz,
                documented_turn_cycles=44+5*HOLD,
                observed_equivalent_cycles_per_turn=per_turn_equivalent,
                observed_cpu_cycles_per_sample=MCU_HZ/hz)


def one_budget(turns,cycles_per_turn,*,codec_hz=CODEC_WORST_HZ,
               spi_read_us=SPI_READ_US,overhead=ISR_OVERHEAD_CYCLES_PER_SAMPLE,
               reserve=OTHER_CPU_RESERVE_FRACTION):
    if turns<=0 or cycles_per_turn<=0 or codec_hz<0 or spi_read_us<0 or overhead<0 or not 0<=reserve<1:
        raise ValueError('Invalid scheduling parameters')
    sample_cyc=turns*cycles_per_turn
    sample_hz=PCM_HZ*sample_cyc
    spi_hz=(SPI_VECTORS_PER_40MS*spi_read_us*1e-6/.04)*MCU_HZ
    irq_hz=PCM_HZ*overhead
    reserve_hz=reserve*MCU_HZ
    all_hz=sample_hz+codec_hz+spi_hz+irq_hz+reserve_hz
    return dict(turns=turns,adc_sample_active_us=sample_cyc/MCU_HZ*1e6,
       adc_sample_period_us=1e6/PCM_HZ,
       gap_between_adc_bursts_us=(MCU_HZ/PCM_HZ-sample_cyc)/MCU_HZ*1e6,
       adc_equivalent_cpu_mhz=sample_hz/1e6,
       slim_encoder_assumed_cpu_mhz=codec_hz/1e6,
       spi_codebook_assumed_cpu_mhz=spi_hz/1e6,
       sample_irq_overhead_assumed_cpu_mhz=irq_hz/1e6,
       other_cpu_reserve_assumed_mhz=reserve_hz/1e6,
       total_cpu_mhz=all_hz/1e6,
       nominal_margin_mhz=(MCU_HZ-all_hz)/1e6,
       nominal_margin_cycles_per_10ms=(MCU_HZ-all_hz)/100,
       nominal_fits=all_hz<=MCU_HZ)


def spi_irq_block_sensitivity(spi_read_us=SPI_READ_US,*,phase_us=0):
    """Hypothetical 150 equally spaced, NON-preemptible SPI transactions / 40ms.

    Not a physical trace or claim about the actual firmware's IRQ policy.
    Count sample deadlines falling inside SPI transactions and max delay.
    """
    if spi_read_us<=0 or phase_us<0:raise ValueError('Invalid hypothetical SPI timing')
    t_read=40_000/SPI_VECTORS_PER_40MS
    reads=[phase_us+i*t_read for i in range(SPI_VECTORS_PER_40MS)]
    affected=[]
    for j in range(320):
        deadline=j*125.
        # Each read has a unique start; no overlapping reads at 34us length.
        for start in reads:
            if start<=deadline<start+spi_read_us:
                affected.append((j,start+spi_read_us-deadline))
                break
    return dict(hypothetical_uninterruptible_spi=True,phase_us=phase_us,
                delayed_sample_deadlines=len(affected),
                max_sample_deadline_delay_us=max((x[1] for x in affected),default=0),
                jitter_not_a_measured_physical_result=True)


def model(meta,turn_options=(200,120,110,100,98,90,80)):
    cap=capture_params(meta)
    eq=cap['observed_equivalent_cycles_per_turn']
    scenarios=[one_budget(w,eq) for w in turn_options]
    max_turns=math.floor((MCU_HZ-CODEC_WORST_HZ-
        (SPI_VECTORS_PER_40MS*SPI_READ_US*1e-6/.04)*MCU_HZ-
        PCM_HZ*ISR_OVERHEAD_CYCLES_PER_SAMPLE-MCU_HZ*OTHER_CPU_RESERVE_FRACTION)/(PCM_HZ*eq))
    sweep=[spi_irq_block_sensitivity(phase_us=k) for k in range(0,125,5)]
    return dict(experiment='K6 PA11 one-pin bitbang ADC + slim Sotto realtime budget',
      proof_level='OFFLINE_SOURCE_AND_RECORDED_CAPTURE_MODEL_ONLY',
      physical_capture_is_prior_read_only=True,radio_access=False,
      clock_hz=MCU_HZ,required_pcm_rate_hz=PCM_HZ,
      capture=cap,slim_encoder_worst_assumed_mhz=CODEC_WORST_HZ/1e6,
      slim_encoder_mean_assumed_mhz=CODEC_MEAN_HZ/1e6,
      spi_reads_per_40ms_assumed=SPI_VECTORS_PER_40MS,
      spi_us_per_read_assumed=SPI_READ_US,
      isr_overhead_cycles_per_sample_assumed=ISR_OVERHEAD_CYCLES_PER_SAMPLE,
      other_cpu_reserve_fraction_assumed=OTHER_CPU_RESERVE_FRACTION,
      max_turns_at_nominal_budget=max_turns,
      scenarios=scenarios,
      spi_irq_blocking_hypothesis=dict(phase_sweep_count=len(sweep),
        cases_with_any_jitter=sum(x['delayed_sample_deadlines']>0 for x in sweep),
        maximum_jitter_us=max(x['max_sample_deadline_delay_us'] for x in sweep),
        warning='ONLY if each 34us SPI transaction masks sample interrupts; actual IRQ behavior unknown'),
      limitations=['The 7956Hz real capture used W=200 in a continuous polling loop, with no encoder running.',
        'The 44+5*hold turn cost is a source-comment approximation; 60.33 cycles/turn equivalent is inferred from total captured elapsed time.',
        'At W=90 and paced 8kHz, PA11 analog charge balance and sound fidelity are untested; W=90 was measured at a DIFFERENT sample rate.',
        'Sotto 27/29MHz and 150x34us codebook reads are upstream modeled numbers, not a K6 physical timing trace.',
        'ISR overhead 300 cycles/sample and 5% system reserve are sensitivity assumptions, not measurements.',
        'Average CPU fit is necessary, NOT sufficient for 125us sampling deadline or SPI/IRQ compatibility.',
        'No new microphone sampling, RF, GPIO, SPI or MCU emulation performed.'])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture-meta',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('Refusing to overwrite previous report')
    r=model(json.loads(a.capture_meta.read_text()))
    a.out.write_text(json.dumps(r,indent=2)+'\n')
    print('Measured capture Hz:',round(r['capture']['measured_sample_rate_hz'],3),
          'equiv cycles/turn:',round(r['capture']['observed_equivalent_cycles_per_turn'],3))
    print('Max turns with modeled codec/SPI/IRQ/reserve:',r['max_turns_at_nominal_budget'])
    for x in r['scenarios']:
        print('W',x['turns'],'ADC',round(x['adc_equivalent_cpu_mhz'],2),'MHz',
              'total',round(x['total_cpu_mhz'],2),'MHz',
              'margin',round(x['nominal_margin_mhz'],2),'MHz',
              'nominal fit',x['nominal_fits'])

if __name__=='__main__':main()
