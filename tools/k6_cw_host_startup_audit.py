#!/usr/bin/env python3
"""Read-only, source-checked K6 CW host PA startup window model.

Reads a generated Aura Rust host checkout (NO build, no radio) and checked-in
C keyer to establish actual *call ordering*. Optional SPI durations are
hypothetical; PA RF leakage, physical TX onset and off-isolation are unknown.
A separate quiet-start candidate is informational, not a firmware patch.
"""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]


def extract_function(source: str,name: str,*,signature: str|None=None)->str:
    """Find unambiguous Rust fn with brace matching; fail rather than guess."""
    pat=re.compile(r'\bfn\s+'+re.escape(name)+r'\s*\(')
    matches=list(pat.finditer(source))
    if signature is not None:
        matches=[m for m in matches if source[m.start():source.find('{',m.start())].startswith(signature)]
    if len(matches)!=1:
        raise ValueError(f'expected exactly one Rust function {name}: got {len(matches)}')
    start=matches[0].start()
    op=source.find('{',start)
    if op<0:raise ValueError(f'function {name} has no body')
    depth=0
    for idx in range(op,len(source)):
        if source[idx]=='{':depth+=1
        elif source[idx]=='}':
            depth-=1
            if depth==0:return source[start:idx+1]
    raise ValueError(f'unclosed function {name}')


def _positions(haystack,signatures):
    out=[]
    for s in signatures:
        count=haystack.count(s)
        if count!=1:
            raise ValueError(f'source signature {s!r}: expected 1 found {count}')
        out.append(haystack.index(s))
    if out!=sorted(out) or len(out)!=len(set(out)):
        raise ValueError('unsafe changed/ambiguous PA startup ordering')
    return out


def examine(radio_src,pager_src,cw_src,driver_src,*,spi_write_us=117.):
    """Audit *source* and calculate conditional relative transaction spans."""
    if not math.isfinite(spi_write_us) or not 1<=spi_write_us<=100000:
        raise ValueError('write duration must be finite in 1..100000us')
    enter=extract_function(radio_src,'enter_tx')
    paoff=extract_function(radio_src,'cw_carrier_off')
    paon=extract_function(radio_src,'cw_carrier_on')
    host=extract_function(pager_src,'mod_tx_on',signature='fn mod_tx_on(max_ms: u32)')
    host_pa=extract_function(pager_src,'mod_tx_pa')
    driver_on=extract_function(driver_src,'pa_enable')
    driver_off=extract_function(driver_src,'pa_off')
    gpio=extract_function(driver_src,'set_gpio_bit')
    band=extract_function(driver_src,'set_tx_band_uhf')
    led=extract_function(driver_src,'set_tx_led')
    # These checks intentionally fail on an unrelated function body, absent
    # generated checkout, or host/driver changes; no static guess is accepted.
    _positions(enter,('self.fd6818.tx_on(syst);',
                      'self.fd6818.pa_enable(syst, self.cfg.power);',
                      'self.fd6818.set_tx_band_uhf(syst)',
                      'self.fd6818.set_tx_led(syst, true);'))
    _positions(host,('let ok = app.radio.enter_tx(syst);',
                     'app.radio.fd6818.reg_write(syst, 0x30, MOD_TX_STATE);',
                     'c.tx = true;'))
    if 'self.fd6818.pa_off(syst);' not in paoff or 'self.fd6818.pa_enable(syst, self.cfg.power);' not in paon:
        raise ValueError('unexpected radio CW carrier interface')
    if 'app.radio.cw_carrier_off(syst);' not in host_pa or 'app.radio.cw_carrier_on(syst);' not in host_pa:
        raise ValueError('module host PA callback changed')
    if 'api->tx_on(' not in cw_src or cw_src.find('api->tx_pa(0)',cw_src.find('api->tx_on(')) < 0:
        raise ValueError('CW module does not request PA off after tx_on')
    if driver_on.count('self.write_reg(syst, REG_PA, value);')!=1 or driver_off.count('self.write_reg(syst, REG_PA, PA_OFF_VALUE);')!=1:
        raise ValueError('PA register semantics changed')
    if gpio.count('self.read_reg(syst, REG_GPIO)')!=1 or gpio.count('self.write_reg(syst, REG_GPIO, val)')!=1:
        raise ValueError('GPIO bus read/write semantics changed')
    if band.count('self.set_gpio_bit(')!=2 or led.count('self.set_gpio_bit(')!=1:
        raise ValueError('expected two UHF band GPIO calls and one LED GPIO call')
    if 'PA_OFF_VALUE: u16 = 0x0022' not in driver_src:
        raise ValueError('off register baseline changed, must re-qualify')
    # After completing the PA enable write, UHF band routing takes 2 x
    # (read+write); the TX LED one (read+write); host MOD_TX_STATE one write;
    # then the CW module's first tx_pa(0) one write. Other CPU/timer/log
    # latencies and RF analog response are unknown. No exact RF window.
    events=[('pa_enable',0),('uhf_gpio3_deassert',2),('uhf_gpio2_assert',2),
            ('tx_led',2),('module_tx_state',1),('cw_first_pa_off',1)]
    boundaries=[];cursor=0
    for name,operations in events:
        cursor+=operations
        boundaries.append(dict(stage=name,bus_transactions_cumulative=cursor,
                               modeled_us_after_pa_enable=cursor*spi_write_us))
    post_band=cursor-4 # 2 led+1 state+1 off
    # Count this as 'modeled RF path active AND PA enabled *if* PA driver
    # energizes at gain write and UHF route is required'. No measured RF.
    return dict(kind='cw_host_startup_source_order_read_only',
                source_verified=True,physical_rf=False,
                rf_window_measured=False,prior_band_state_known=False,
                tx_on_asserts_pa_before_module_returns=True,
                existing_api3_quiet_start=False,
                current_module_immediate_off_request=True,
                proposed_api_modification='NONE',
                real_PA_glitch_possible_not_established=True,
                relative_sequence=boundaries,
                hypothetical_spi_transaction_us=spi_write_us,
                estimated_pa_enable_to_pa_off_bus_only_us=cursor*spi_write_us,
                estimated_after_uhf_band_route_bus_only_us=post_band*spi_write_us,
                unknowns=['RF path previous GPIO state','actual SPI rate/timing',
                          'driver RF voltage and PA isolation',
                          'CPU/logging/timer latency','PLL settle/phase chirp',
                          'physical meaning of PA_OFF_VALUE and register timing'],
                warning='Source ordering proven, neither conducted RF amplitude nor a measured glitch duration')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--radio',type=Path,required=True,help='generated upstream Aura src/device/radio.rs')
    p.add_argument('--pager',type=Path,required=True,help='generated upstream Aura src/app/pager.rs')
    p.add_argument('--driver',type=Path,required=True,help='generated upstream Aura src/drivers/fd6818b.rs')
    p.add_argument('--cw',type=Path,default=ROOT/'c/k6/mod/cwkey.c')
    p.add_argument('--spi-us',type=float,default=117.)
    p.add_argument('--json',type=Path)
    args=p.parse_args(argv)
    try:
        result=examine(args.radio.read_text(),args.pager.read_text(),
                       args.cw.read_text(),args.driver.read_text(),
                       spi_write_us=args.spi_us)
    except (OSError,ValueError) as exc:p.error(str(exc))
    print('CALL ORDER CONFIRMED: host enter_tx enables PA before CW module can request PA-off')
    print(f'Modeled bus-only conditional PA-enable→PA-off {result["estimated_pa_enable_to_pa_off_bus_only_us"]:.0f}µs; '
          f'after UHF route {result["estimated_after_uhf_band_route_bus_only_us"]:.0f}µs '
          'at hypothetical write interval (NOT measured RF leakage)')
    if args.json:
        args.json.parent.mkdir(parents=True,exist_ok=True)
        args.json.write_text(json.dumps(result,indent=2)+'\n')
    return 0

if __name__=='__main__':raise SystemExit(main())
