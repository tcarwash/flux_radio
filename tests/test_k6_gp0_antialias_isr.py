"""Compare actual GP0 Rust decimator source in legacy and opt-in AA3 modes.

Builds only a standalone host binary from source, never communicates with a
radio. Tests both signed fixed-point behavior and spectral alias rejection.
"""
from pathlib import Path
import subprocess
import math
import numpy as np
import pytest

from tools.k6_gp0_antialias_study import decimate,phase_amp,FS

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'firmware/k6-aura/sotto-api30-gp0-capture.rs'
BUILD=ROOT/'firmware/k6-aura/build-fd6818b.sh'


@pytest.fixture(scope='module')
def rust_programs(tmp_path_factory):
    s=SRC.read_text()
    dec=s[s.index('#[repr(C)]\n#[derive(Clone,Copy)]\nstruct Decimator'):s.index('\n#[repr(C)]\nstruct State')]
    div=s[s.index('#[inline(always)]\nfn udiv'):s.index('// Exact constant-denominator divider')]
    fast=s[s.index('#[cfg(k6_sotto_gp0_fast_isr)]\n#[inline(always)]\nfn gp0_div_87260'):s.index('#[inline(always)]\nfn dec_push')]
    push=s[s.index('#[inline(always)]\nfn dec_push'):s.index('// The real hardware uses 16 volatile')]
    program='''
#![allow(dead_code)]
use std::io::{self,Read,Write};
const FS:u32=87260;
''' +dec+'\n'+div+'\n'+fast+'\n'+push+'''
fn main(){
    let f=std::env::args().nth(1).expect("fs").parse::<u32>().unwrap();
    assert!(f>=35000 && f<=160000);
    let mut d=Decimator{fs:f,..Decimator::empty()};
    let mut b=Vec::new();io::stdin().read_to_end(&mut b).unwrap();
    let mut stdout=io::stdout().lock();let mut out=[0u8;2];
    for count in b{
        let mut y=0i16;
        match dec_push(&mut d,count as u32,&mut y){
            1=>{out.copy_from_slice(&y.to_le_bytes());stdout.write_all(&out).unwrap();},
            0=>{},
            x=>panic!("decimator status {}",x)
        }
    }
}
'''
    p=tmp_path_factory.mktemp('gp0-antialias-actual-rust')
    src=p/'decimator.rs';src.write_text(program)
    builds={}
    for label,cfg in [('legacy',[]),('aa3',['--cfg','k6_sotto_gp0_aa3'])]:
        exe=p/label
        subprocess.run(['rustc','--edition=2021','-O','--cfg','k6_sotto_gp0_fast_isr',*cfg,str(src),'-o',str(exe)],
                       check=True,timeout=45,capture_output=True,text=True)
        builds[label]=exe
    return builds


def run(exe,bits,fs=FS):
    p=subprocess.run([str(exe),str(fs)],input=np.asarray(bits,dtype=np.uint8).tobytes(),
                     check=True,timeout=30,capture_output=True)
    return np.frombuffer(p.stdout,dtype='<i2').copy()


@pytest.mark.parametrize('fs',[87260,84960,110000,160000])
def test_aa3_and_legacy_match_reference_integer_sample_by_sample(rust_programs,fs):
    rng=np.random.default_rng(7709)
    bits=rng.binomial(64,.5,size=fs//6).astype(np.uint8)
    bits[::23]=0
    bits[::71]=64
    for name,poles in [('legacy',0),('aa3',3)]:
        got=run(rust_programs[name],bits,fs)
        expect=decimate(bits,poles=poles,fs=fs)
        np.testing.assert_array_equal(got,expect)
        assert len(got)==(len(bits)*8000)//fs


def test_anti_alias_suppresses_nine_khz_before_downsampling(rust_programs):
    t=np.arange(int(FS*.7))/FS
    frequencies=(320,1000,2000,2800,3500,7000,9000,15000)
    for freq in frequencies:
        bits=np.rint(32+4*np.sin(2*np.pi*freq*t)).astype(np.uint8)
        legacy=run(rust_programs['legacy'],bits)
        aa3=run(rust_programs['aa3'],bits)
        alias=abs((freq+4000)%8000-4000)
        base=phase_amp(legacy,alias)
        filtered=phase_amp(aa3,alias)
        db=20*math.log10(max(filtered,1e-9)/max(base,1e-9))
        if freq==1000:
            assert -1.5<db<0.3,('1k voice passband',db)
        if freq==9000:
            assert db<-20,('9k alias into 1k',db)
        if freq==15000:
            assert db<-30,('15k alias into 1k',db)
    # No change to PCM wire contract: both modes return one 16-bit 8k sample.


def test_arm_profile_is_explicit_opt_in():
    s=SRC.read_text();b=BUILD.read_text()
    assert '#[cfg(k6_sotto_gp0_aa3)]' in s
    assert '#[cfg(not(k6_sotto_gp0_aa3))]' in s
    assert 'K6_SOTTO_GP0_AA3:-0' in b
    assert 'K6_SOTTO_GP0_AA3 requires GP0 mic, RAM0=1 and fast ISR' in b
    assert 'K6_SOTTO_GP0_FAST_ISR:-0' in b
    assert 'let num=mag<<1;' in s
    assert 'raw<<7' in s
    assert 'const RAW_BYTES:usize=1024;' in s
    assert 'const HALF:usize=512;' in s
    # Full fixed-point area must fit i32 at 160kHz max even for all-one PDM.
    assert 64*(1<<7)*160000 < 2**31
