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
    for label,cfg in [('legacy',[]),('aa3',['--cfg','k6_sotto_gp0_aa3']),('fir11',['--cfg','k6_sotto_gp0_fir11']),('fir11_late',['--cfg','k6_sotto_gp0_fir11_late']),('cic2',['--cfg','k6_sotto_gp0_cic2'])]:
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
    assert '#[cfg(not(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11,k6_sotto_gp0_fir11_late,k6_sotto_gp0_cic2)))]' in s
    assert 'K6_SOTTO_GP0_AA3:-0' in b
    assert 'K6_SOTTO_GP0_AA3 requires GP0 mic, RAM0=1 and fast ISR' in b
    assert 'K6_SOTTO_GP0_FAST_ISR:-0' in b
    assert 'let num=mag<<1;' in s
    assert 'raw<<7' in s
    assert 'const RAW_BYTES:usize=1024;' in s
    assert 'const HALF:usize=512;' in s
    # Full fixed-point area must fit i32 at 160kHz max even for all-one PDM.
    assert 64*(1<<7)*160000 < 2**31


@pytest.mark.parametrize('fs',[87260,84960,110000,160000])
def test_fir11_matches_real_rust_integer_samples_and_extremes(rust_programs,fs):
    rng=np.random.default_rng(1984)
    bits=rng.binomial(64,.5,size=fs//7).astype(np.uint8)
    bits[::19]=0
    bits[::37]=64
    actual=run(rust_programs['fir11'],bits,fs)
    reference=decimate(bits,fs=fs,fir11=True)
    np.testing.assert_array_equal(actual,reference)
    assert len(actual)==len(bits)*8000//fs


def test_fir11_spectral_tradeoff_against_three_pole_aa3(rust_programs):
    t=np.arange(int(FS*.75))/FS
    for freq in (1000,2800,7000,9000,15000):
        bits=np.rint(32+4*np.sin(2*np.pi*freq*t)).astype(np.uint8)
        legacy=run(rust_programs['legacy'],bits)
        fir11=run(rust_programs['fir11'],bits)
        aa3=run(rust_programs['aa3'],bits)
        alias=abs((freq+4000)%8000-4000)
        base=phase_amp(legacy,alias)
        db_fir=20*math.log10(max(phase_amp(fir11,alias),1e-9)/max(base,1e-9))
        db_aa3=20*math.log10(max(phase_amp(aa3,alias),1e-9)/max(base,1e-9))
        if freq==1000:
            assert -1.0<db_fir<0.3,(freq,db_fir)
        if freq==2800:
            assert db_fir>db_aa3+2,(freq,db_fir,db_aa3)
        if freq==9000:
            assert db_fir < -16,(freq,db_fir)
        if freq==15000:
            assert db_fir < -20,(freq,db_fir)


def test_fir11_flag_gated_and_uncombined():
    s=SRC.read_text();b=BUILD.read_text()
    assert "K6_SOTTO_GP0_FIR11:-0" in b
    assert "K6_SOTTO_GP0_AA3:-0" in b
    assert "k6_sotto_gp0_fir11" in s
    assert "fir_taps:[i8;11]" in s
    assert "not(any(k6_sotto_gp0_aa3,k6_sotto_gp0_fir11,k6_sotto_gp0_fir11_late,k6_sotto_gp0_cic2))" in s
    assert 64*128*160000 < 2**31

@pytest.mark.parametrize('fs',[35000,84960,87260,110000,160000])
def test_delayed_fir11_exact_rust_fixed_point_matches_python(rust_programs,fs):
    rng=np.random.default_rng(12787+fs)
    bits=rng.binomial(64,.5,size=fs//4).astype(np.uint8)
    bits[::23]=0
    bits[::37]=64
    bits[::89]=32
    for fixture in (bits, np.full(fs//7,64,dtype=np.uint8),
                    np.full(fs//7,0,dtype=np.uint8)):
        got=run(rust_programs['fir11_late'],fixture,fs)
        ref=decimate(fixture,fs=fs,fir11_late=True)
        np.testing.assert_array_equal(got,ref)
        assert len(got)==len(fixture)*8000//fs


def test_delayed_fir11_preserves_spectral_response_and_scaling(rust_programs):
    t=np.arange(int(FS*.85))/FS
    for freq in (320,1000,2000,2800,3500,7000,9000,15000):
        bits=np.rint(32+4*np.sin(2*np.pi*freq*t)).astype(np.uint8)
        late=run(rust_programs['fir11_late'],bits)
        reference=run(rust_programs['fir11'],bits)
        legacy=run(rust_programs['legacy'],bits)
        alias=abs((freq+4000)%8000-4000)
        db_late=20*math.log10(max(phase_amp(late,alias),1e-9)/max(phase_amp(legacy,alias),1e-9))
        db_ref=20*math.log10(max(phase_amp(reference,alias),1e-9)/max(phase_amp(legacy,alias),1e-9))
        assert abs(db_late-db_ref)<1.0,(freq,db_late,db_ref)
        if freq==2800:assert db_late > -3
        if freq==9000:assert db_late < -16
        if freq==15000:assert db_late < -20


def test_delayed_fir11_flag_remains_off_and_exclusive():
    b=BUILD.read_text(); s=SRC.read_text()
    assert 'K6_SOTTO_GP0_FIR11_LATE:-0' in b
    assert 'k6_sotto_gp0_fir11_late' in s
    assert 'K6_SOTTO_GP0_FIR11_LATE requires GP0 mic, RAM0, fast ISR, FIR11=0, AA3=0' in b
    assert 'remainder<<8' in s
    # Worst-case unscaled weighted sum fits i32 at max accepted group rate.
    assert 64*11*160000 < 2**31
    # Post-normalization quotient/remainder fit 32-bit, without 64-bit math.
    assert (160000-1)*256 < 2**32

@pytest.mark.parametrize('fixture',['binomial','silence','full','impulse'])
def test_cic2_r8_real_rust_matches_python_with_modular_integrators(rust_programs,fixture):
    fs=FS
    n=fs//3
    rng=np.random.default_rng(198712)
    if fixture=='binomial':bits=rng.binomial(64,.5,size=n).astype(np.uint8)
    elif fixture=='silence':bits=np.zeros(n,dtype=np.uint8)
    elif fixture=='full':bits=np.full(n,64,dtype=np.uint8)
    else:
        bits=np.full(n,32,dtype=np.uint8)
        bits[0]=64;bits[::113]=0;bits[::347]=64
    actual=run(rust_programs['cic2'],bits)
    expected=decimate(bits,cic2=True)
    np.testing.assert_array_equal(actual,expected)
    assert abs(len(actual)-n*8000/fs)<=2
    assert actual.dtype==np.int16


def test_cic2_spectral_response_relative_aa3(rust_programs):
    t=np.arange(int(FS*.9))/FS
    for f in (1000,2800,3500,7000,9000,15000):
        counts=np.rint(32+4*np.sin(2*np.pi*f*t)).astype(np.uint8)
        cic=run(rust_programs['cic2'],counts)
        base=run(rust_programs['legacy'],counts)
        aa3=run(rust_programs['aa3'],counts)
        alias=abs((f+4000)%8000-4000)
        db=lambda y:20*math.log10(max(1e-9,phase_amp(y,alias))/max(1e-9,phase_amp(base,alias)))
        if f==1000:assert -2<db(cic)<.6,(f,db(cic))
        if f==2800:assert db(cic)>db(aa3)+1.5,(f,db(cic),db(aa3))
        # R=8 is a genuine intermediate-rate stage (10,907.5 Hz). Its
        # aliases are NOT in general located at the direct 8kHz alias bin!
        # Measure the strongest 80–3900Hz component, rather than only 1kHz.
        def strongest(y):
            x=y.astype(float)[1600:]
            window=np.hanning(len(x))
            freq=np.fft.rfftfreq(len(x),d=1/8000)
            amplitude=np.abs(np.fft.rfft(x*window))*2/window.sum()
            amplitude[(freq<80)|(freq>3900)]=0
            i=int(np.argmax(amplitude))
            return float(freq[i]),float(amplitude[i])
        bfreq,bamp=strongest(base)
        cfreq,camp=strongest(cic)
        strongest_db=20*math.log10(max(camp,1e-9)/max(bamp,1e-9))
        if f==9000:
            assert abs(cfreq-1907.5)<10,(cfreq,camp)
            assert -20<strongest_db<-9,(f,strongest_db)
        if f==15000:
            assert strongest_db<-14,(f,strongest_db)


def test_cic2_config_is_default_off_rate_restricted_and_bounded():
    source=SRC.read_text(); build=BUILD.read_text()
    assert 'K6_SOTTO_GP0_CIC2:-0' in build
    assert 'k6_sotto_gp0_cic2' in source
    assert 'd.cic_i1=d.cic_i1.wrapping_add(raw as u16)' in source
    assert 'd.cic_i2=d.cic_i2.wrapping_add(d.cic_i1)' in source
    assert 'let group_weight=64000u32;' in source
    assert 'let num=mag<<2;' in source
    assert 'if d.fs!=FS{return -1;}' in source
    assert 64*64*160000*4 < 2**32
    with pytest.raises(ValueError,match='requires'):
        decimate(np.full(1000,32,dtype=np.uint8),fs=110000,cic2=True)
