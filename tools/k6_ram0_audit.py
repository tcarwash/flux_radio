#!/usr/bin/env python3
"""Audit an expanded Flux ELF, using llvm-nm; fail if protected SRAM is crossed."""
import argparse
import json
import subprocess


def audit(elf, mode="1"):
    rows = subprocess.check_output(['llvm-nm','-S',elf], text=True).splitlines()
    symbols = {}
    for line in rows:
        fields = line.split()
        if len(fields) == 4:
            addr, size, typ, name = fields
            symbols[name] = (int(addr,16),int(size,16))
    def sym(suffix):
        matches = [v for n,v in symbols.items() if n.endswith(suffix)]
        if len(matches) != 1:
            raise ValueError(f'Expected one symbol {suffix}: {matches}')
        return matches[0]
    lo,hi = sym('__sram0')[0],sym('__eram0')[0]
    assert 0x20000100 == lo <= hi <= 0x20001000, 'RAM0 protected region overlap'
    for name in ('MOD_SCREEN','RX_RING') + (('APP_SLOT',) if mode in ('1','stack','balanced') else ()):
        addr,size=sym(name)
        assert lo <= addr and addr+size <= hi, f'{name} outside RAM0'
    base,size=sym('arena5ARENA')
    top=sym('_stack_start')[0]
    end=sym('__euninit')[0]
    assert sym('__sheap')[0] == end, 'heap/stack paint starts outside main RAM'
    assert sym('_stack_end')[0] == end, 'stack floor outside main RAM'
    assert size == ({"1":14976,"balanced":14400}.get(mode,11776)) and 0x20001028 <= base < base+size <= end < top <= 0x20005ff0
    assert top-end >= (3800 if mode == "balanced" else 2780), 'main stack headroom regressed'
    return {'arena_base':hex(base),'arena_bytes':size,'ram0_used':hi-lo,'ram0_free':0x20001000-hi,'stack_reservation':top-end}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('elf');p.add_argument('--mode',choices=['1','small','stack','balanced'],default='1');a=p.parse_args()
    print(json.dumps(audit(a.elf,a.mode),indent=2))
