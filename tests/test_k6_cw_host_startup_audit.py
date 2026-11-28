"""Source-order CW module TX startup gate; no RF/silicon assertion."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
from tools.k6_cw_host_startup_audit import examine,extract_function

RADIO='''
impl Radio {
 pub fn enter_tx(&mut self, syst: &mut SYST) -> bool {
  if !self.tx_allowed.allows(self.cfg.tx_freq_hz) || self.cfg.modulation != Modulation::Fm {return false;}
  self.fd6818.tx_on(syst);
  self.fd6818.pa_enable(syst, self.cfg.power);
  match Self::band_of(self.cfg.tx_freq_hz) {
     Band::Uhf => self.fd6818.set_tx_band_uhf(syst),
     Band::Vhf => self.fd6818.set_tx_band_vhf(syst),
  }
  self.fd6818.set_tx_led(syst, true);
  true
 }
 pub fn cw_carrier_off(&mut self, syst: &mut SYST) { self.fd6818.pa_off(syst); }
 pub fn cw_carrier_on(&mut self, syst: &mut SYST) { self.fd6818.pa_enable(syst, self.cfg.power); }
}
'''
PAGER='''
#[cfg(feature = "lean")]
unsafe extern "C" fn mod_tx_on(_max_ms: u32) -> i32 { -1 }
#[cfg(not(feature = "lean"))]
unsafe extern "C" fn mod_tx_on(max_ms: u32) -> i32 {
 let ok = app.radio.enter_tx(syst);
 if !ok { return -1; }
 app.radio.fd6818.reg_write(syst, 0x30, MOD_TX_STATE);
 c.tx = true;
 0
}
unsafe extern "C" fn mod_tx_pa(on: i32) -> i32 {
 if on != 0 {app.radio.cw_carrier_on(syst);} else {app.radio.cw_carrier_off(syst);}
 0
}
'''
DRIVER='''
const PA_OFF_VALUE: u16 = 0x0022;
impl FD {
 pub fn pa_enable(&mut self, syst: &mut SYST, _power: Power) {self.write_reg(syst, REG_PA, value);}
 pub fn pa_off(&mut self, syst: &mut SYST) { self.write_reg(syst, REG_PA, PA_OFF_VALUE);}
 fn set_gpio_bit(&mut self, syst: &mut SYST, gpiox: u16, high: bool) {
  let mut val = self.read_reg(syst, REG_GPIO);
  self.write_reg(syst, REG_GPIO, val);
 }
 pub fn set_tx_band_uhf(&mut self, syst: &mut SYST) {
  self.set_gpio_bit(syst, GPIO3, false);
  self.set_gpio_bit(syst, GPIO2, true);
 }
 pub fn set_tx_led(&mut self, syst: &mut SYST, on: bool) {self.set_gpio_bit(syst, GPIO5, on);}
}
'''
CW=(ROOT/'c/k6/mod/cwkey.c').read_text()


def test_known_host_api3_source_has_PA_on_then_off_hazard():
    report=examine(RADIO,PAGER,CW,DRIVER)
    assert report['source_verified']
    assert report['tx_on_asserts_pa_before_module_returns']
    assert report['existing_api3_quiet_start'] is False
    assert report['current_module_immediate_off_request']
    assert report['estimated_pa_enable_to_pa_off_bus_only_us']==pytest.approx(936)
    assert report['estimated_after_uhf_band_route_bus_only_us']==pytest.approx(468)
    assert report['relative_sequence'][-1]['stage']=='cw_first_pa_off'
    assert [e['stage'] for e in report['relative_sequence']]==[
        'pa_enable','uhf_gpio3_deassert','uhf_gpio2_assert','tx_led','module_tx_state','cw_first_pa_off']
    assert report['physical_rf'] is False and not report['rf_window_measured']
    assert 'NONE' in report['proposed_api_modification']
    assert report['real_PA_glitch_possible_not_established']


def test_spi_time_is_a_hypothesis_not_a_hardware_measurement():
    fast=examine(RADIO,PAGER,CW,DRIVER,spi_write_us=5)
    assert fast['estimated_pa_enable_to_pa_off_bus_only_us']==40
    assert fast['estimated_after_uhf_band_route_bus_only_us']==20
    assert fast['hypothetical_spi_transaction_us']==5
    assert not fast['rf_window_measured']


@pytest.mark.parametrize('radio,pager,driver,cw',[
    (RADIO.replace('self.fd6818.pa_enable(syst, self.cfg.power);\n  match',
                   'self.fd6818.pa_off(syst);\n  match'),PAGER,DRIVER,CW),
    (RADIO.replace('self.fd6818.tx_on(syst);',''),PAGER,DRIVER,CW),
    (RADIO,PAGER.replace('let ok = app.radio.enter_tx(syst);','let ok = app.radio.enter_tx_quiet(syst);'),DRIVER,CW),
    (RADIO,PAGER,DRIVER.replace('0x0022','0x0000'),CW),
    (RADIO,PAGER,DRIVER.replace('self.read_reg(syst, REG_GPIO)','self.get_cached_gpio()'),CW),
    (RADIO,PAGER,DRIVER,CW.replace('api->tx_pa(0)','api->tx_pa(1)')),
])
def test_drifted_source_is_rejected_instead_of_lying_about_pa_window(radio,pager,driver,cw):
    with pytest.raises(ValueError):examine(radio,pager,cw,driver)


def test_cfg_splitting_and_brace_depth_and_ambiguous_functions():
    body=extract_function(PAGER,'mod_tx_on',signature='fn mod_tx_on(max_ms: u32)')
    assert 'let ok = app.radio.enter_tx(syst);' in body
    assert 'return -1' in body
    with pytest.raises(ValueError):extract_function(PAGER,'mod_tx_on')
    with pytest.raises(ValueError):extract_function(PAGER,'missing')
    with pytest.raises(ValueError):extract_function('fn mod_tx_on(x: u32) {', 'mod_tx_on')


def test_bad_spi_values_fail_closed():
    for bad in (0,-1,float('nan'),float('inf'),100001):
        with pytest.raises(ValueError):examine(RADIO,PAGER,CW,DRIVER,spi_write_us=bad)


def test_cli_writes_provenance_but_not_qualifying_RF_when_given_generated_snippets(tmp_path):
    paths=[]
    for key,val in (('radio',RADIO),('pager',PAGER),('driver',DRIVER)):
        p=tmp_path/f'{key}.rs'
        p.write_text(val)
        paths.extend((f'--{key}',str(p)))
    output=tmp_path/'report.json'
    result=subprocess.run([sys.executable,str(ROOT/'tools/k6_cw_host_startup_audit.py'),
                           *paths,'--json',str(output)],capture_output=True,text=True,timeout=12)
    assert result.returncode==0,result.stderr
    info=json.loads(output.read_text())
    assert info['physical_rf'] is False
    assert info['rf_window_measured'] is False
    assert '936' in result.stdout
    result=subprocess.run([sys.executable,str(ROOT/'tools/k6_cw_host_startup_audit.py'),
                           '--radio',str(tmp_path/'missing.rs'),
                           *paths[2:]],capture_output=True,text=True,timeout=12)
    assert result.returncode!=0


def test_real_generated_aura_source_when_supplied_by_build_environment():
    root=os.getenv('K6_CW_AURA_SOURCE_ROOT')
    if not root:
        pytest.skip('generated upstream Aura source optional; pass K6_CW_AURA_SOURCE_ROOT')
    p=Path(root)
    report=examine((p/'device/radio.rs').read_text(),(p/'app/pager.rs').read_text(),
                   CW,(p/'drivers/fd6818b.rs').read_text())
    assert report['tx_on_asserts_pa_before_module_returns']
    assert not report['physical_rf']
