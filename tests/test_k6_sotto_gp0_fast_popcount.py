"""Bit-exact, volatile, unrolled B-chip GP0 PDM count regression."""
from pathlib import Path
import re
import subprocess

import pytest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'firmware/k6-aura/sotto-api30-gp0-capture.rs'

@pytest.fixture(scope='module')
def parity_binary(tmp_path_factory):
    text=SOURCE.read_text()
    start=text.index('unsafe fn gp0_bits_64(raw:*mut u8,off:usize)->u32 {')
    end=text.index('\n#[inline(always)]\nunsafe fn pcm_sample', start)
    body=text[start:end]
    assert body.count('pdm_word!(')==16
    out=tmp_path_factory.mktemp('gp0-pdm-parity')
    src=out/'parity.rs'
    src.write_text('''
use core::ptr::read_volatile as rdv;
%s

fn legacy(words:&[u32;16])->u32{
    let mut acc=0u32;
    for j in 0..16_usize {
        let word=unsafe{rdv(words.as_ptr().add(j))};
        acc=acc.wrapping_add(word&0x02020202);
    }
    acc>>=1;
    acc=(acc&0x00ff00ff)+((acc>>8)&0x00ff00ff);
    (acc&0xffff)+(acc>>16)
}
fn check(words:[u32;16]){
    let p=words.as_ptr() as *mut u8;
    let new=unsafe{gp0_bits_64(p,0)};
    let old=legacy(&words);
    assert_eq!(new,old,"word mismatch {:?}",words);
    assert!(new<=64);
}
fn main(){
    let mut count=0u32;
    check([0;16]); check([!0;16]);
    check([0x02020202;16]); check([0x01010101;16]);
    for i in 0..16 {
        for bit in 0..32 {
            let mut words=[0u32;16];
            words[i]=1u32<<bit;
            check(words);count+=1;
        }
    }
    let mut state=0x2398bdeb_u32;
    for i in 0..350_000 {
        let mut words=[0u32;16];
        for j in 0..16 {
            state=state.wrapping_mul(1664525).wrapping_add(1013904223);
            words[j]=if i%7==0{(state&0x02020202)}
                else if i%7==1{!state}
                else{state};
        }
        check(words);count+=1;
    }
    println!("verified_gp0_pdm_groups={count}");
}
'''.replace('%s',body))
    bin=out/'parity'
    subprocess.run(['rustc','--edition=2021','-O',str(src),'-o',str(bin)],
                   text=True,capture_output=True,check=True,timeout=50)
    return bin

def test_unrolled_pdm_matches_legacy_for_350k_groups(parity_binary):
    p=subprocess.run([str(parity_binary)],text=True,capture_output=True,
                     check=True,timeout=30)
    assert 'verified_gp0_pdm_groups=350512' in p.stdout

def test_default_volatile_loop_and_opt_in_guards():
    s=SOURCE.read_text()
    assert '#[cfg(k6_sotto_gp0_fast_isr)]\n        let bits=gp0_bits_64(raw,off+i*64);' in s
    assert '#[cfg(not(k6_sotto_gp0_fast_isr))]\n        let bits={' in s
    assert 'let word=rdv(raw.add(off+i*64+j*4).cast::<u32>());' in s
    assert 'rdv(p.add($n))' in s
    assert s.count('pdm_word!(')==16
