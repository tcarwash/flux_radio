"""Real Rust GP0 decimator: a default-off fractional clock correction.

Offline-only. No RF, radio, serial cable, or MCU image operations.
The input rate is inferred from GP0 PCM AIR real physical 500/1000Hz captures;
the input PDM bits here are synthetic 64-bit popcounts, not live GP0 DMA.
"""
from __future__ import annotations

from pathlib import Path
import math
import subprocess

import numpy as np
import pytest

from tools.k6_gp0_antialias_study import decimate

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'firmware/k6-aura/sotto-api30-gp0-capture.rs'
BUILD=ROOT/'firmware/k6-aura/build-fd6818b.sh'
DECLARED_GROUP_HZ=87260
ACTUAL_GROUP_HZ=93050  # Derived approx. from 8,530 PCM/s * 87260 / 8000.


@pytest.fixture(scope='module')
def rust_decimators(tmp_path_factory):
    source=SRC.read_text()
    state=source[source.index('#[repr(C)]\n#[derive(Clone,Copy)]\nstruct Decimator'):source.index('\n#[repr(C)]\nstruct State')]
    variable_divider=source[source.index('#[inline(always)]\nfn udiv'):source.index('// Exact constant-denominator divider')]
    fixed_divider=source[source.index('#[cfg(k6_sotto_gp0_fast_isr)]\n#[inline(always)]\nfn gp0_div_87260'):source.index('#[inline(always)]\nfn dec_push')]
    decimator=source[source.index('#[inline(always)]\nfn dec_push'):source.index('// The real hardware uses 16 volatile')]
    rust='''#![allow(dead_code)]
use std::io::{self,Read,Write};
const FS:u32=87260;
'''+state+'\n'+variable_divider+'\n'+fixed_divider+'\n'+decimator+'''
fn main() {
    let mut bytes=Vec::new();io::stdin().read_to_end(&mut bytes).unwrap();
    let mut state=Decimator{fs:FS,..Decimator::empty()};
    let mut out=io::stdout().lock();
    for raw in bytes {
        let mut sample=0i16;
        match dec_push(&mut state,raw as u32,&mut sample) {
            0 => {},
            1 => out.write_all(&sample.to_le_bytes()).unwrap(),
            other => panic!("unexpected decimator result {}",other),
        }
    }
}
'''
    directory=tmp_path_factory.mktemp('k6-gp0-clock-actual-rust')
    src=directory/'driver.rs';src.write_text(rust)
    result={}
    for label,config in [('baseline',()),('clock_7500',('--cfg','k6_sotto_gp0_clock_7500'))]:
        path=directory/label
        subprocess.run(['rustc','--edition=2021','-O','--cfg','k6_sotto_gp0_fast_isr',*config,
                        str(src),'-o',str(path)],check=True,timeout=45,capture_output=True,text=True)
        result[label]=path
    return result


def run(path,bits):
    result=subprocess.run([str(path)],input=np.asarray(bits,dtype=np.uint8).tobytes(),
                          check=True,timeout=30,capture_output=True)
    return np.frombuffer(result.stdout,dtype='<i2').copy()


def dominant_pitch(data):
    y=data.astype(np.float64)/32768
    y=y[400:] # Skip short DC startup interval.
    window=np.hanning(len(y))
    spectrum=abs(np.fft.rfft((y-y.mean())*window,n=131072))
    axis=np.fft.rfftfreq(131072,1/8000)
    spectrum[(axis<200)|(axis>1600)]=0
    return float(axis[np.argmax(spectrum)])


def test_baseline_decimator_matches_unchanged_python_reference(rust_decimators):
    rng=np.random.default_rng(54321)
    for bits in (rng.binomial(64,.5,size=16000).astype(np.uint8),
                 np.full(8000,32,dtype=np.uint8),
                 np.tile(np.array([0,64,32,60,4],dtype=np.uint8),1600)):
        baseline=run(rust_decimators['baseline'],bits)
        np.testing.assert_array_equal(baseline,decimate(bits))
        assert len(baseline)==len(bits)*8000//DECLARED_GROUP_HZ


@pytest.mark.parametrize('tone_hz',[500.,1000.])
def test_clock_correction_restores_physical_tone_without_changing_fast_divider(rust_decimators,tone_hz):
    count=round(ACTUAL_GROUP_HZ*.65)
    t=np.arange(count)/ACTUAL_GROUP_HZ
    # Input is synthetic count-density; source code under test is actual Rust.
    bits=np.rint(32+2.2*np.sin(2*np.pi*tone_hz*t)).clip(0,64).astype(np.uint8)
    old=run(rust_decimators['baseline'],bits)
    fixed=run(rust_decimators['clock_7500'],bits)
    assert len(old)==(count*8000)//DECLARED_GROUP_HZ
    assert len(fixed)==(count*7500)//DECLARED_GROUP_HZ
    assert abs(len(old)/.65 - 8531)<3
    assert abs(len(fixed)/.65 - 8000)<5
    old_pitch=dominant_pitch(old)
    fixed_pitch=dominant_pitch(fixed)
    predicted_old=tone_hz*8000/(ACTUAL_GROUP_HZ*8000/DECLARED_GROUP_HZ)
    assert abs(old_pitch-predicted_old)<3,(tone_hz,old_pitch,predicted_old)
    assert abs(fixed_pitch-tone_hz)<3,(tone_hz,fixed_pitch)
    assert abs(fixed_pitch-tone_hz)<abs(old_pitch-tone_hz)/8


def test_corrected_sample_clock_stable_without_dma_or_ram_state_changes(rust_decimators):
    rng=np.random.default_rng(1108)
    bits=rng.binomial(64,.5,size=ACTUAL_GROUP_HZ).astype(np.uint8)
    corrected=run(rust_decimators['clock_7500'],bits)
    assert len(corrected)==(ACTUAL_GROUP_HZ*7500)//DECLARED_GROUP_HZ
    assert 7995<=len(corrected)<=8001
    assert corrected.dtype==np.int16
    assert np.isfinite(corrected.astype(float)).all()


def test_firmware_gate_is_default_off_and_keeps_87260_fast_divider():
    source=SRC.read_text();builder=BUILD.read_text()
    assert 'K6_SOTTO_GP0_CLOCK_7500:-0' in builder
    assert '--cfg k6_sotto_gp0_clock_7500' in builder
    assert 'K6_SOTTO_GP0_CLOCK_7500 requires GP0 mic, RAM0=1, fast ISR and all GP0 prefilters OFF' in builder
    assert 'k6_sotto_gp0_clock_7500' in source
    assert 'let group_weight=7500u32;' in source
    assert 'let group_weight=8000u32;' in source
    assert 'let group_weight=64000u32;' in source
    assert 'const DIVISOR:u32=87260;' in source
    assert '#[cfg(k6_sotto_gp0_fast_isr)]' in source


def test_build_gate_shell_enforces_default_and_rejects_bad_profiles():
    import os
    builder=BUILD.read_text()
    section='case "${K6_SOTTO_GP0_CLOCK_7500:-0}" in'+builder.split('case "${K6_SOTTO_GP0_CLOCK_7500:-0}" in',1)[1].split('# Only full expanded RAM0',1)[0]
    def evaluate(extra):
        variables={'K6_SOTTO_MIC':'1','K6_RAM0':'1','K6_SOTTO_GP0_FAST_ISR':'1',
                   'K6_SOTTO_GP0_AA3':'0','K6_SOTTO_GP0_FIR11':'0',
                   'K6_SOTTO_GP0_FIR11_LATE':'0','K6_SOTTO_GP0_CIC2':'0',
                   'K6_SOTTO_GP0_CIC3':'0','K6_SOTTO_GP0_CLOCK_7500':'1'}
        variables.update(extra)
        return subprocess.run(['/bin/sh','-c','PACK_CFG=\n'+section+'\nprintf "cfg:%s" "$PACK_CFG"'],
                              env={**os.environ,**variables},capture_output=True,text=True)
    baseline=evaluate({'K6_SOTTO_GP0_CLOCK_7500':'0'})
    assert baseline.returncode==0 and baseline.stdout=='cfg:'
    enabled=evaluate({})
    assert enabled.returncode==0 and '--cfg k6_sotto_gp0_clock_7500' in enabled.stdout
    for bad in ({'K6_SOTTO_MIC':'0'}, {'K6_RAM0':'0'},
                {'K6_SOTTO_GP0_FAST_ISR':'0'}, {'K6_SOTTO_GP0_AA3':'1'},
                {'K6_SOTTO_GP0_FIR11':'1'}, {'K6_SOTTO_GP0_FIR11_LATE':'1'},
                {'K6_SOTTO_GP0_CIC2':'1'}, {'K6_SOTTO_GP0_CIC3':'1'},
                {'K6_SOTTO_GP0_CLOCK_7500':'2'}):
        invalid=evaluate(bad)
        assert invalid.returncode!=0,(bad,invalid.stdout,invalid.stderr)
