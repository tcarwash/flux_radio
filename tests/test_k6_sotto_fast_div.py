"""Exact native division and physical K6 module size gate for Sotto fast-div.

Uses the exact C function bodies extracted from the compiled module header.
No radio connection, firmware flash, or RF is used.
"""
from __future__ import annotations

import ctypes
import hashlib
import random
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.k6_sotto_module import (
    build, SOTTO_ARENA_BYTES, SOTTO_SCRATCH_RESERVE,
    SOTTO_MODULE_OVERHEAD, SOTTO_LIVE_SCRATCH_REQUIRED,
)
HEADER=ROOT/'c/k6/mod/sotto_m0rt.h'
ORIGINAL_SHA='551bea3e8d72a47dcf7c97f6502b32614d7915fd4ab12d4f0b90d9f99d48fa6e'

@pytest.fixture(scope='module')
def division(tmp_path_factory):
    src=HEADER.read_text()
    lo=src.index('static unsigned m0rt_udiv(')
    hi=src.index('\nunsigned __aeabi_uidiv(',lo)
    method=src[lo:hi]
    assert method.count('#ifdef SOTTO_FAST_DIV')==1
    tmp=tmp_path_factory.mktemp('sotto-fast-div-m0')
    lib={}
    for opt in (False,True):
        c=tmp/('fast.c' if opt else 'legacy.c')
        shared=tmp/('fast.so' if opt else 'legacy.so')
        c.write_text('#include <stdint.h>\n'+method+'''
unsigned quotient(unsigned n,unsigned d) {
    unsigned rem=0; return m0rt_udiv(n,d,&rem);
}
unsigned remainder(unsigned n,unsigned d) {
    unsigned rem=0; (void)m0rt_udiv(n,d,&rem); return rem;
}
''')
        subprocess.run(
            ['cc','-O2','-fPIC','-shared','-fno-builtin',*( ['-DSOTTO_FAST_DIV=1'] if opt else []),
             str(c),'-o',str(shared)],
            check=True,capture_output=True,text=True,timeout=30)
        dll=ctypes.CDLL(str(shared))
        for name in ('quotient','remainder'):
            fn=getattr(dll,name)
            fn.argtypes=(ctypes.c_uint32,ctypes.c_uint32)
            fn.restype=ctypes.c_uint32
        lib[opt]=dll
    return lib

def test_fast_div_exact_at_boundaries(division):
    original,fast=division[False],division[True]
    numerators=[0,1,2,3,4,5,7,8,9,15,16,17,31,32,33,
                127,128,255,256,257,1023,1024,4095,4096,
                65534,65535,65536,65537,87260,96000,
                0xffff0000,0x7fffffff,0x80000000,0xfffffffe,0xffffffff]
    denominators=[0,1,2,3,4,7,8,9,15,16,31,32,33,
                  255,256,257,1023,1024,4095,4096,
                  65535,65536,65537,87260,96000,0x7fffffff,
                  0x80000000,0xfffffffe,0xffffffff]
    for n in numerators:
        for d in denominators:
            q=fast.quotient(n,d)
            r=fast.remainder(n,d)
            assert (q,r)==(original.quotient(n,d),original.remainder(n,d))
            assert (q,r)==((n//d,n%d) if d else (0xffffffff,n))

def test_fast_div_exact_for_a_million_deterministic_pairs(division):
    fast=division[True]
    rng=random.Random(0xA67B051)
    for i in range(1_000_000):
        n=rng.getrandbits(32)
        d=(rng.getrandbits(32) if i%5==0
           else rng.randrange(1,65536) if i%5==1
           else (n>>rng.randrange(0,32)) or 1 if i%5==2
           else rng.randrange(1,0xffffffff) if i%5==3
           else [1,2,3,87260,65535,1<<16,1<<30,0xffffffff][i%8])
        q=fast.quotient(n,d)
        r=fast.remainder(n,d)
        assert (q,r)==((n//d,n%d) if d else (0xffffffff,n)),(i,n,d,q,r)

@pytest.fixture(scope='module')
def modules():
    base,base_entry=build(host_resources=True,gp0_irq_tx=True)
    fast,fast_entry=build(host_resources=True,gp0_irq_tx=True,fast_div=True)
    history,history_entry=build(host_resources=True,gp0_irq_tx=True,fast_history_copy=True)
    both,both_entry=build(host_resources=True,gp0_irq_tx=True,fast_div=True,fast_history_copy=True)
    score_pair,score_pair_entry=build(host_resources=True,gp0_irq_tx=True,fast_div=True,fast_history_copy=True,fast_cb_pair=True)
    return (base,base_entry,fast,fast_entry,history,history_entry,both,both_entry,score_pair,score_pair_entry)


def _margin(code):
    return SOTTO_ARENA_BYTES-SOTTO_SCRATCH_RESERVE-SOTTO_MODULE_OVERHEAD-len(code)-SOTTO_LIVE_SCRATCH_REQUIRED


def test_default_gp0_module_remains_byte_identical_to_fielded(modules):
    code,entry=modules[:2]
    assert len(code)==9784 and entry==16
    assert hashlib.sha256(code).hexdigest()==ORIGINAL_SHA


def test_fast_div_preserves_arena_and_gains_scratch(modules):
    base,base_entry,code,entry=modules[:4]
    assert entry==base_entry
    # No optimization may consume the hard-won 48-byte physical stage-2
    # margin or require SPI module format changes.
    assert _margin(code)>=48
    assert len(code)<len(base)


def test_history_copy_and_combined_builds_shrink_and_preserve_margin(modules):
    base,base_entry,fast,fast_entry,history,history_entry,both,both_entry=modules[:8]
    assert history_entry==fast_entry==both_entry==base_entry==16
    assert len(history)==9748 and _margin(history)==84
    assert len(both)==9736 and _margin(both)==96
    assert len(history)<len(base) and len(both)<len(fast)


def test_paired_codebook_score_variant_fits_only_with_prior_optimizations(modules):
    score_pair,entry=modules[8:]
    assert entry==16 and len(score_pair)==9756 and _margin(score_pair)==76
    with pytest.raises(ValueError,match="at least 48 are required"):
        build(host_resources=True,gp0_irq_tx=True,fast_cb_pair=True)

def test_fast_div_is_opt_in_and_no_upstream_codec_edits():
    src=HEADER.read_text()
    builder=(ROOT/'tools/k6_sotto_module.py').read_text()
    assert '#ifdef SOTTO_FAST_DIV' in src and '#else' in src
    assert 'for (i = 31; i >= 0; i--)' in src
    assert 'while (d < n && !(d & 0x80000000u))' in src
    assert 'while (bit)' in src
    assert 'fast_div=False' in builder
    assert 'fast_history_copy=False' in builder
    assert 'fast_cb_pair=False' in builder
    assert 'paired codebook widths' in builder
    assert 'slim_history_shift' in builder
    assert 'SOTTO_FAST_MEMMOVE' not in src
    assert '["-DSOTTO_FAST_DIV=1"] if fast_div else []' in builder
    assert 'SOTTO_FAST_DIV' not in (ROOT/'c/fixed/sotto_fx_slim.c').read_text()
