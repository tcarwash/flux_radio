#!/usr/bin/env python3
"""Execute the ACTUAL pa11adc.h GPIO converter in a Cortex-M0 emulator.

Only GPIOA register memory is modeled; IDR bit11 is deterministic constant 0 or 1.
No analog capacitor, real pin, interrupt, SPI or physical radio is simulated.
Cycle counts are from the upstream Cortex-M0 instruction timing model (WS=0).
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path


def run(elf:Path,cm0_run:Path,turns=(80,90,98,100,110,120,200)):
    spec=importlib.util.spec_from_file_location('flux_pa11_cm0',str(cm0_run))
    cm0=importlib.util.module_from_spec(spec);spec.loader.exec_module(cm0)
    cm0.WS=0
    m=cm0.M0(str(elf))
    m.mu.mem_map(0x48000000,4096)
    rows=[]
    for level in (0,1):
        m.mu.mem_write(0x48000010,(level<<11).to_bytes(4,'little'))
        for w in turns:
            v,cycles=m.call('pa11_probe',w,3,0)
            rows.append(dict(idr_pa11=level,turns=w,cycles=cycles,
                cycles_per_turn=cycles/w,active_us=cycles/96,
                remaining_us_in_125us=125-cycles/96,
                nominal_8khz_adc_mhz=cycles*8000/1e6,
                adc_return_value=v))
    return dict(proof_level='ACTUAL_ARMV6M_PA11ADC_CODE_UNICORN_GPIO_MEMORY_ONLY',
        radio_access=False,source='c/k6/mod/pa11adc.h',
        gpio_model='PA11 IDR bit11 fixed 0/1, GPIO writes memory only, no RC analog circuit',
        cpu_model='Cortex-M0 upstream instruction cycle timing WS=0, no real MCU bus stalls',
        warnings=['No real analog charge balancing or PA11 audio quality modeled',
            'ISR exception entry/exit, TIM14 watchdog, UART, SPI and competing tasks not modeled',
            'Cortex-M0 has no VTOR; any interrupt route needs SRAM vector remap or firmware support',
            'RX remap may disable flash prefetch and slow SPI firmware operations'],
        scenarios=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--elf',type=Path,required=True)
    p.add_argument('--cm0-run',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('Refusing overwrite')
    r=run(a.elf,a.cm0_run)
    a.out.write_text(json.dumps(r,indent=2)+'\n')
    for x in r['scenarios']:
        print('IDR',x['idr_pa11'],'W',x['turns'],'cycles',x['cycles'],
            'cycles/turn',round(x['cycles_per_turn'],2),
            'ADC MHz',round(x['nominal_8khz_adc_mhz'],2),
            'gap us',round(x['remaining_us_in_125us'],2))

if __name__=='__main__':main()
