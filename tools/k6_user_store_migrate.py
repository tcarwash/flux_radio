#!/usr/bin/env python3
"""Offline, non-destructive legacy library -> KUL2 image. Never writes a radio.
Preserves logical slot IDs, including themes, help decks and legacy code pages.
Requires explicit review of unknown slot contents; original dump is not modified.
"""
import argparse,struct,zlib
from pathlib import Path
BASE=0x110000;END=0x190000;SECTOR=4096;SLOT=16384;DIR_BYTES=1036

def is_packed(raw):
    return raw[:4]==b'KUL2' or raw[SECTOR:SECTOR+4]==b'KUL2'

def locations(raw):
    if len(raw)!=END-BASE:raise ValueError('expected exact 512 KiB user library dump')
    candidates=[]
    for copy in range(2):
        d=raw[copy*SECTOR:copy*SECTOR+DIR_BYTES]
        magic,g,crc,inv=struct.unpack_from('<4sIII',d)
        if magic!=b'KUL2' or inv!=(~g&0xffffffff) or zlib.crc32(d[12:])!=crc:continue
        entries={};used=set();valid=True
        for id in range(255):
            start,k,n=struct.unpack_from('<BBH',d,16+4*id)
            if not n:
                if start or k:valid=False;break
                continue
            sectors=set(range(start,start+k))
            if k!=(n+SECTOR-1)//SECTOR or start+k>126 or used&sectors:valid=False;break
            used|=sectors;entries[id]=((start+2)*SECTOR,n)
        if valid:candidates.append((g,entries))
    if not candidates:raise ValueError('no valid KUL2 directory')
    chosen=candidates[0]
    for other in candidates[1:]:
        if 0<(other[0]-chosen[0])&0xffffffff<0x80000000:chosen=other
    return chosen[1]

def objects(raw):
    return {id:bytes(raw[a:a+n]) for id,(a,n) in locations(raw).items()}

def compact(entries):
    out=bytearray(b'\xff'*(END-BASE));d=bytearray(DIR_BYTES);pos=2;manifest=[]
    for id,blob in sorted(entries.items()):
        n=len(blob);k=(n+SECTOR-1)//SECTOR
        if not 0<=id<255 or not 0<n<=65535:raise ValueError('invalid object ID or length')
        if pos+k>128:raise ValueError('not enough space after directory reservation')
        out[pos*SECTOR:pos*SECTOR+n]=blob
        struct.pack_into('<BBH',d,16+4*id,pos-2,k,n);manifest.append((id,n,k));pos+=k
    for copy,g in [(0,1),(1,2)]:
        struct.pack_into('<4sIII',d,0,b'KUL2',g,0,(~g)&0xffffffff);struct.pack_into('<I',d,8,zlib.crc32(d[12:]))
        out[copy*SECTOR:copy*SECTOR+DIR_BYTES]=d
    return bytes(out),manifest

def migrate(raw, *, preserve_opaque=False):
    if len(raw)!=END-BASE:raise ValueError('expected exact 512 KiB user library dump')
    if is_packed(raw):raise ValueError('already a KUL2 library')
    entries={}
    for id in range(32):
        slot=raw[id*SLOT:(id+1)*SLOT]
        if slot==b'\xff'*SLOT:continue
        if slot[:3]==b'K6T' and 1<=slot[3]<=9:
            n=12+int.from_bytes(slot[4:6],'little')
            if not 12<=n<=8192:raise ValueError(f'invalid theme in slot {id}')
        elif slot[:3]==b'K6M' and slot[13]&1 and slot[15]==id:
            n=24+int.from_bytes(slot[4:6],'little')
            if not 24<n<=SLOT:raise ValueError(f'invalid image in slot {id}')
        else:
            if not preserve_opaque:raise ValueError(f'unrecognized slot {id}; export or inspect before migration')
            n=SLOT
        entries[id]=slot[:n]
    return compact(entries)

def upgrade(raw, *, preserve_opaque=False):
    return compact(objects(raw)) if is_packed(raw) else migrate(raw,preserve_opaque=preserve_opaque)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('destination',type=Path);a=p.parse_args()
    if a.source.resolve()==a.destination.resolve() or a.destination.exists():p.error('destination must be a new file')
    raw=a.source.read_bytes();bank=raw[BASE:END] if len(raw)==0x200000 else raw
    out,manifest=upgrade(bank);a.destination.write_bytes(out)
    for id,n,k in manifest:print(f'ID {id:3}: {n:5} bytes, {k} sectors')
    print(f'{126-sum(k for _,_,k in manifest)} payload sectors remain; offline image only')
if __name__=='__main__':main()
