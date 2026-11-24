"""Opt-in host-generated-source patch, never touches hardware or defaults."""
from __future__ import annotations
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
PATH=ROOT/'firmware/k6-aura/apply-cw-pa-band-order.py'
spec=importlib.util.spec_from_file_location('cw_pa_order',PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def source(method):
    return f'''
impl Radio {{
 pub fn {method}(&mut self, syst: &mut SYST) -> bool {{
  self.fd6818.tx_on(syst);
  self.fd6818.pa_enable(syst, self.cfg.power);
  match Self::band_of(self.cfg.tx_freq_hz) {{
     Band::Uhf => self.fd6818.set_tx_band_uhf(syst),
     Band::Vhf => self.fd6818.set_tx_band_vhf(syst),
  }}
  self.fd6818.set_tx_led(syst, true);
  true
 }}
}}
'''


def test_move_pa_enable_after_routing_for_both_normal_and_native_cw():
    original=source('enter_tx')+source('enter_tx_keyed')
    after,edits=mod.relocate_PA_after_band_route(original)
    assert edits==2
    for name in mod.TARGETS:
        begin,end=mod._function_span(after,name)
        part=after[begin:end]
        assert part.index(mod.TX)<part.index(mod.BAND)<part.index(mod.PA_LINE)<part.index(mod.LED)
    assert mod.relocate_PA_after_band_route(after)==(after,0)
    assert after.count(mod.PA_LINE)==2


@pytest.mark.parametrize('wrong',[
    lambda s:s.replace('self.fd6818.pa_enable(syst, self.cfg.power);','self.fd6818.pa_off(syst);'),
    lambda s:s.replace('self.fd6818.tx_on(syst);','self.fd6818.tx_off(syst);'),
    lambda s:s.replace('self.fd6818.set_tx_led(syst, true);',''),
    lambda s:s.replace('Band::Vhf => self.fd6818.set_tx_band_vhf(syst),',''),
    lambda s:s.replace('self.fd6818.pa_enable(syst, self.cfg.power);',
                       'self.fd6818.pa_enable(syst, self.cfg.power);\n  self.fd6818.pa_enable(syst, self.cfg.power);'),
])
def test_patch_fails_closed_on_unexpected_driver_source(wrong):
    with pytest.raises(ValueError):mod.relocate_PA_after_band_route(wrong(source('enter_tx')+source('enter_tx_keyed')))


def test_command_dry_run_vs_opt_in_apply(tmp_path):
    path=tmp_path/'radio.rs';original=source('enter_tx')+source('enter_tx_keyed')
    path.write_text(original)
    command=[sys.executable,str(PATH),'--radio',str(path)]
    dry=subprocess.run(command+['--check'],capture_output=True,text=True,timeout=8)
    assert dry.returncode==0,dry.stderr
    assert path.read_text()==original
    assert 'CHECKED_NO_WRITES' in dry.stdout
    written=subprocess.run(command+['--apply'],capture_output=True,text=True,timeout=8)
    assert written.returncode==0,written.stderr
    assert path.read_text()!=original
    assert '2 methods' in written.stdout
    unchanged=subprocess.run(command+['--apply'],capture_output=True,text=True,timeout=8)
    assert unchanged.returncode==0 and '0 methods' in unchanged.stdout


def test_build_does_not_enable_experimental_pa_patch_by_default():
    build=(ROOT/'firmware/k6-aura/build-fd6818b.sh').read_text()
    assert 'apply-cw-pa-band-order.py' not in build


def test_generated_pinned_source_safely_rewrites_in_own_test_copy(tmp_path):
    root=os.getenv('K6_CW_AURA_SOURCE_ROOT')
    if not root:pytest.skip('optional generated Aura source root not provided')
    original=(Path(root)/'device/radio.rs').read_text()
    transformed,edits=mod.relocate_PA_after_band_route(original)
    assert edits==2
    assert len(transformed)-len(original)==0  # pure statement move only
    for name in mod.TARGETS:
        a,b=mod._function_span(transformed,name)
        sec=transformed[a:b]
        assert sec.index(mod.BAND)<sec.index(mod.PA_LINE)
    assert mod.relocate_PA_after_band_route(transformed)==(transformed,0)
    assert (Path(root)/'device/radio.rs').read_text()==original


def test_actual_radio_methods_compiled_in_rust_host_shim_prove_stale_band_gain(tmp_path):
    """Compile extracted REAL generated Aura methods, no RF/ARM/hardware.

    The original emits the previous band's PA gain on a first-band TX;
    the explicit opt-in rewritten methods use the newly selected band.
    The Rust harness is not full Aura and makes NO physical TX claims.
    """
    root=os.getenv('K6_CW_AURA_SOURCE_ROOT')
    if not root:pytest.skip('requires generated pinned Aura source')
    rustc=__import__('shutil').which('rustc')
    if not rustc:pytest.skip('rustc not available')
    original=(Path(root)/'device/radio.rs').read_text()
    patched,changes=mod.relocate_PA_after_band_route(original)
    assert changes==2
    def execute(radio_src):
        methods=[]
        for name in mod.TARGETS:
            a,b=mod._function_span(radio_src,name)
            methods.append(radio_src[a:b])
        stub=r'''
#![allow(unused_variables,dead_code,non_camel_case_types)]
macro_rules! rl {($($t:tt)*) => {};}
#[derive(Clone,Copy,PartialEq,Debug)] enum Modulation { Fm,Cw,Cwf }
#[derive(Clone,Copy)] enum Power { Low }
#[derive(Clone,Copy)] enum Band { Uhf,Vhf }
struct SYST;
struct TxAllowed;
impl TxAllowed {fn allows(&self,_hz:u32)->bool {true}}
struct Config{tx_freq_hz:u32,modulation:Modulation,wide_band:bool,subaudio_tx:u8,power:Power}
mod board {
 pub fn set_speaker_switch(_g:(),_v:bool){}
 pub fn set_rx_band_off(_g:()){}
 pub fn set_rx_led(_g:(),_v:bool){}
}
mod hal {pub mod txdog {pub fn keyed(){}}}
struct Fd{band_uhf:bool,events:Vec<&'static str>}
impl Fd {
 fn rf_off(&mut self,_s:&mut SYST){}
 fn idle(&mut self,_s:&mut SYST){}
 fn wake(&mut self,_s:&mut SYST){}
 fn set_frequency_hz(&mut self,_s:&mut SYST,_hz:u32){}
 fn set_wide_bandwidth(&mut self,_s:&mut SYST,_w:bool){}
 fn set_subaudio_tx(&mut self,_s:&mut SYST,_v:u8){}
 fn set_scramble(&mut self,_s:&mut SYST,_v:u8){}
 fn apply_tx_mic_gain(&mut self,_s:&mut SYST){}
 fn tx_on(&mut self,_s:&mut SYST){self.events.push("tx_on");}
 fn pa_enable(&mut self,_s:&mut SYST,_p:Power){
  self.events.push(if self.band_uhf {"pa_uhf"} else {"pa_vhf"});
 }
 fn set_tx_band_uhf(&mut self,_s:&mut SYST){self.band_uhf=true;self.events.push("band_uhf");}
 fn set_tx_band_vhf(&mut self,_s:&mut SYST){self.band_uhf=false;self.events.push("band_vhf");}
 fn set_tx_led(&mut self,_s:&mut SYST,_on:bool){}
}
struct Radio{tx_allowed:TxAllowed,cfg:Config,gpioa:(),gpiob:(),fd6818:Fd,scramble_level:u8,tx_state:bool}
impl Radio{
 fn band_of(hz:u32)->Band{if hz>=300_000_000{Band::Uhf}else{Band::Vhf}}
'''+ '\n'.join(methods)+r'''
}
fn main(){
 let mut syst=SYST;
 // Start with stale VHF chip state, switch to UHF on first TX.
 let mut r=Radio{tx_allowed:TxAllowed,cfg:Config{tx_freq_hz:440_850_000,
  modulation:Modulation::Fm,wide_band:true,subaudio_tx:0,power:Power::Low},
  gpioa:(),gpiob:(),fd6818:Fd{band_uhf:false,events:Vec::new()},
  scramble_level:0,tx_state:false};
 assert!(r.enter_tx(&mut syst));
 println!("NORMAL {:?}",r.fd6818.events);
 // Start with stale UHF chip state, then TX VHF in CW mode.
 r.cfg.tx_freq_hz=146_560_000;
 r.cfg.modulation=Modulation::Cw;
 assert!(r.enter_tx_keyed(&mut syst));
 println!("CW {:?}",r.fd6818.events);
}
'''
        file=tmp_path/'shim.rs';file.write_text(stub)
        output=tmp_path/'shim.bin'
        subprocess.run([rustc,'--edition=2021','-Awarnings','-O',str(file),'-o',str(output)],
                       check=True,text=True,capture_output=True,timeout=50)
        return subprocess.run([str(output)],check=True,text=True,capture_output=True,timeout=10).stdout
    baseline=execute(original)
    updated=execute(patched)
    assert 'NORMAL ["tx_on", "pa_vhf", "band_uhf"]' in baseline
    assert 'NORMAL ["tx_on", "band_uhf", "pa_uhf"]' in updated
    assert 'CW ["tx_on", "pa_vhf", "band_uhf", "tx_on", "pa_uhf", "band_vhf"]' in baseline
    assert 'CW ["tx_on", "band_uhf", "pa_uhf", "tx_on", "band_vhf", "pa_vhf"]' in updated
