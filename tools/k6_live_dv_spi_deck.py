#!/usr/bin/env python3
"""Build a read-only K6SC v1 Sotto IVF deck for offline SPI-transport tests.

Matches upstream Blowfang fmsotto's signed-module payload layout. The deck
is deterministic (only public slim codebook search tables). This tool does
NOT install anything on the K6 or write SPI, and NEVER handles audio.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import re
import struct

SLIM_CELLS=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed/slim_cells.h')
VECTORS=(512,512,256)
EXPECTED=(8,64,64,32,26)


def build_deck(source:str)->bytes:
    text=re.sub(r'/\*.*?\*/',' ',source,flags=re.S)
    defs={m.group(1):int(m.group(2)) for m in
          re.finditer(r'#define\s+(\w+)\s+(-?\d+)',text)}
    cd,cs,maxn=defs['IVF_CD'],[defs[f'IVF_C{s}'] for s in range(3)],defs['IVF_MAXN']
    if (cd,*cs,maxn)!=EXPECTED:raise ValueError('Unexpected Sotto IVF index configuration')
    arrays={}
    for m in re.finditer(
        r'static const [\w ]+?\s(\w+)\[[^\]]*\]\s*=\s*\{([^}]*)\}',
        text):
        arrays[m.group(1)]=[int(v) for v in re.findall(r'-?\d+',m.group(2))]
    centroids=[]
    starts=[]
    ids=[]
    for s in range(3):
        cen=arrays[f'ivf_cen{s}']
        if len(cen)!=cs[s]*cd:raise ValueError('Invalid IVF centroid size')
        centroids.extend(cen)
        off=arrays[f'ivf_off{s}']
        ix=arrays[f'ivf_idx{s}']
        lo=arrays.get(f'ivf_lo{s}')
        if len(off)!=cs[s]+1 or off[0]!=0 or off[-1]!=VECTORS[s] or len(ix)!=VECTORS[s]:
            raise ValueError('Invalid IVF compressed index dimensions')
        seen=[]
        for c in range(cs[s]):
            a,b=off[c:c+2]
            if b<a or b-a>maxn:raise ValueError('Invalid IVF cell length')
            k=b-a if lo is None else lo[c]
            if not 0<=k<=b-a:raise ValueError('Invalid high-index cutoff')
            values=[ix[a+i]+(256 if i>=k else 0) for i in range(b-a)]
            starts.append(len(ids))
            ids.extend(values)
            seen.extend(values)
        starts.append(len(ids))
        if sorted(seen)!=list(range(VECTORS[s])):
            raise ValueError('IVF vector indices are not exact permutation')
    body=(struct.pack(f'<{len(centroids)}b',*centroids)+
          struct.pack(f'<{len(starts)}H',*starts)+
          struct.pack(f'<{len(ids)}H',*ids))
    hdr=b'K6SC'+bytes([1,cd,*cs,maxn,0,0])+struct.pack('<I',sum(body)&0xffffffff)
    blob=hdr+body
    if len(blob)!=4182:raise ValueError('Expected the exact 4182-byte K6SC deck')
    return blob


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cells',type=Path,default=SLIM_CELLS)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():p.error('refuse overwrite')
    binary=build_deck(a.cells.read_text())
    a.out.write_bytes(binary)
    print('K6SC v1',len(binary),'bytes, SHA256',hashlib.sha256(binary).hexdigest(),
          'OFFLINE only, no firmware or SPI operation')


if __name__=='__main__':main()
