"""Bit-exact GP0 fixed-rate divider, default-off build gate and decimator tests."""
from pathlib import Path
import subprocess
import re
import textwrap

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "firmware/k6-aura/sotto-api30-gp0-capture.rs"
BUILD = ROOT / "firmware/k6-aura/build-fd6818b.sh"

@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    source = SRC.read_text()
    block = re.search(r'(?ms)^fn gp0_div_87260\(n:u32\)->u32\{.*?^\}', source)
    assert block, "fast divider function absent"
    module = block.group(0)
    host = tmp_path_factory.mktemp("gp0-fast-divider")
    rust = host / "check.rs"
    rust.write_text("""
#![allow(unused_parens)]
%s
fn main() {
    const DIVISOR:u32=87260;
    let mut count=0_u64;
    // All discontinuities for quotient 0..49199: exact and both neighbors.
    for q in 0..=49220_u32 {
        let base=(q as u64)*(DIVISOR as u64);
        for delta in -3_i64..=3_i64 {
            let n=base as i64 + delta;
            if n<0 || n>u32::MAX as i64 {continue;}
            let n=n as u32;
            assert_eq!(gp0_div_87260(n),n / DIVISOR,"quotient boundary {n}");
            count+=1;
        }
    }
    // Input within the whole unsigned domain, deterministic.
    let mut state=0x6c8e_9cf1_u32;
    for _ in 0..500_000 {
        state=state.wrapping_mul(1664525).wrapping_add(1013904223);
        assert_eq!(gp0_div_87260(state),state/DIVISOR,"random {state}");
        count+=1;
    }
    // The actual decimator only encounters unsigned n=abs(area)<<8.
    for mag in [0u32,1,2,341,342,87260,5584640,5584639,5584641] {
        let n=mag<<8;
        assert_eq!(gp0_div_87260(n),n/DIVISOR);
        count+=1;
    }
    // Compare baseline and accelerated signal-processing state, sample by
    // sample, across speech-like sign changes, silence, and saturation.
    #[derive(Clone)]
    struct D { phase:u32, area:i32, dc:i32, samples:u32, clipped:u32 }
    fn step(d:&mut D,bits:u32,fast:bool)->Option<i16>{
        let x=2*(bits as i32)-64;
        let newphase=d.phase+8000;
        if newphase<87260 {d.area+=x*8000;d.phase=newphase;return None;}
        let left=87260-d.phase;
        let area=d.area+x*(left as i32);
        let mag=area.unsigned_abs();
        let numerator=mag<<8;
        let q=if fast {gp0_div_87260(numerator)} else {numerator/87260} as i32;
        let centered=if area<0 {-q}else{q};
        d.dc+=((centered<<8)-d.dc)>>7;
        let mut sample=(centered-(d.dc>>8))*8;
        if sample>32767 {sample=32767;d.clipped+=1;}
        if sample< -32768 {sample=-32768;d.clipped+=1;}
        d.phase=newphase-87260;
        d.area=x*(d.phase as i32);
        d.samples+=1;
        Some(sample as i16)
    }
    let mut state=0x9182_7761_u32;
    for pattern in 0..6 {
        let mut reference=D{phase:0,area:0,dc:0,samples:0,clipped:0};
        let mut optimized=reference.clone();
        for i in 0..160_000 {
            state=state.wrapping_mul(1664525).wrapping_add(1013904223);
            let bits=match pattern {
                0=>0,1=>64,2=>32,3=>(state>>26),
                4=>if i%10_000<5_000 {60}else{4},
                _=>((i/330)%65),
            };
            assert_eq!(step(&mut reference,bits,false),step(&mut optimized,bits,true));
            assert_eq!(reference.phase,optimized.phase);
            assert_eq!(reference.area,optimized.area);
            assert_eq!(reference.dc,optimized.dc);
            assert_eq!(reference.clipped,optimized.clipped);
            assert_eq!(reference.samples,optimized.samples);
        }
    }
    println!("exact_division_cases={count} bit_identical_gp0_groups=960000");
}
""".replace("%s",module))
    binary = host / "check"
    compile_run = subprocess.run(["rustc", "--edition=2021", "-O",
                                  str(rust), "-o", str(binary)],
                                 check=True,capture_output=True,text=True,timeout=45)
    return binary

def test_exact_gp0_divider_across_quotient_edges_and_random_inputs(runner):
    proc = subprocess.run([str(runner)],capture_output=True,text=True,
                          check=True,timeout=35)
    assert "exact_division_cases=" in proc.stdout
    assert "bit_identical_gp0_groups=960000" in proc.stdout

def test_default_is_untouched_with_opt_in_and_rate_fallback():
    s=SRC.read_text()
    b=BUILD.read_text()
    assert '#[cfg(k6_sotto_gp0_fast_isr)]' in s
    assert '#[cfg(not(k6_sotto_gp0_fast_isr))]' in s
    assert 'if d.fs==FS {gp0_div_87260(num)} else {udiv(num,d.fs)}' in s
    assert 'const MAGIC:u32=49220;' in s
    assert 'K6_SOTTO_GP0_FAST_ISR:-0' in b
    assert 'k6_sotto_gp0_fast_isr' in b
    assert 'K6_SOTTO_GP0_FAST_ISR requires GP0 mic' in b

def test_reciprocal_fits_cortex_m0_without_u64_or_floating_point():
    s=SRC.read_text()
    div=s[s.index('fn gp0_div_87260'):s.index('fn dec_push')]
    assert 'u64' not in div and 'f32' not in div and 'f64' not in div
    assert '(n>>16)*MAGIC' in div
    assert '((n&0xffff)*MAGIC)>>16' in div
    assert 'q+((n-q*DIVISOR)>=DIVISOR) as u32' in div
