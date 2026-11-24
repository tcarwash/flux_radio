"""Compile the inert radio-side guarded Sotto packet/queue self-test.

The single 1684-byte API3 module uses synthetic six-byte frames only.
It has no PA, RFIC, timer, GPIO, flash or serial calls in its C source.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path
from modem import k6mod,pager

SRC=Path(__file__).resolve().parents[1]/'c/k6/mod/dv_guard33_selftest.c'


def test_immutable_independent_golden_vectors():
    s=SRC.read_text()
    prof=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    for name,n,mid,mul,add in [('a',8,112,17,3),('b',2,113,19,7)]:
        m=re.search(r'static const uint16_t '+name+r'_golden\[33\]\s*=\s*\{(.*?)\};',s,re.S)
        assert m
        words=[int(x,16) for x in re.findall(r'0x([0-9a-fA-F]+)u',m.group(1))]
        assert len(words)==33
        encoded=bytes((i*mul+add)&255 for i in range(n*6))
        actual=prof.pack(encoded,msg_id=mid)[0]+b'\x5a\xa5'
        target=[int.from_bytes(actual[i*2:i*2+2],'little') for i in range(33)]
        assert words==target


def test_k6_real_api3_compilation_and_inert_source():
    code,entry=k6mod.build(str(SRC))
    assert entry==0
    assert 0<len(code)<4096
    assert len(code)==1684
    assert hashlib.sha256(code).hexdigest()=='650c3493580b5e53102b46715eb78cc38e6faa79c21632f89b41f60bc1a560ea'
    assert hashlib.sha256(SRC.read_bytes()).hexdigest()=='d7793ebb887cc13c4d1079ec9db2055250e501da54f1ac9e75c9cd413fd88bb3'
    src=SRC.read_text()
    for dangerous in (r'\btx_on\s*\(',r'\btx_pa\s*\(',r'\bfd_write\s*\(',
                      r'\bfd_read\s*\(',r'\bfl_prog\s*\(',r'\bfl_erase\s*\(',
                      r'0x480000',r'0x4000',r'\b__asm'):
        assert not re.search(dangerous,src)
    assert 'api->say("dv guard33 packet selftest PASS")' in src
