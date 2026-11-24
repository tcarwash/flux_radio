#!/usr/bin/env python3
"""Offline actual Sotto live TX module -> native FSK IQ -> compiled RX.

Build from a selected Sotto development checkout without changing it.
API29 microphone/resource/async services are modeled, not resident firmware.
No hardware device or radio installation is supported.
"""
import argparse
from collections import OrderedDict,Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import time
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
import numpy as np
from k6_live_emu import Emu, FCPU
from modem import k6mod,pager
from sim.k6emu.rf.backend import RFBackend
from sim.k6emu.rf.virtual_link import Channel,VirtualAir,AirSource,VirtualTxBackend
from tools.k6_fsk_emulator_replay import receive
from tools.k6_fsk_virtual_link import HZ, values

# Run the development tree's own build and independent native reference.
# No generated module is written into the source checkout.
PREPARE=r"""
import ctypes as C,struct,subprocess,sys,zlib
from pathlib import Path
from tools.k6_sotto_module import build,FIXED
from tools.k6_sotto_ivf_deck import build_deck,SLIM_CELLS
d=Path(sys.argv[1])
code,entry=build(host_resources=True,live_tx=True)
(d/'module.bin').write_bytes(code)
(d/'entry').write_text(str(entry))
so=d/'ref.so'
subprocess.run(['cc','-O2','-shared','-fPIC','-DSOTTO_V36','-DSOTTO_PITCH_THIRD','-DFX_NARROW_MUL',
 str(FIXED/'sotto_fx_slim.c'),str(FIXED/'fx_tables.c'),'-o',str(so)],check=True)
c=C.CDLL(str(so));c.slim_enc_bytes.restype=C.c_uint
for name,n in [('slim_enc_init',1),('slim_encode10',3),('slim_flush',2)]:
 getattr(c,name).argtypes=[C.c_void_p]*n
state=C.create_string_buffer(c.slim_enc_bytes());c.slim_enc_init(state)
pcm=(C.c_int16*80)();out=C.create_string_buffer(6);bits=bytearray()
if len(sys.argv)>2:
 payload=Path(sys.argv[2]).read_bytes()
 assert len(payload)%160==0 and len(payload)>0
 samples=struct.unpack('<%dh'%(len(payload)//2),payload)
else:
 samples=[(min(i%40,40-i%40)-10)*(400+(i//80%8)*20) for i in range(8320)]
for block in range(len(samples)//80):
 for i in range(80):pcm[i]=samples[block*80+i]
 if c.slim_encode10(state,pcm,out):bits.extend(out.raw)
while c.slim_flush(state,out):bits.extend(out.raw)
assert 0<len(bits)<=4000 and len(bits)%6==0
(d/'reference.bit').write_bytes(bits)
table=b''.join(bytes((C.c_char*n).in_dll(c,f'fx_cb{i}')) for i,n in enumerate((10240,10240,5120,5120)))
h=bytearray(32);struct.pack_into('<4sHHI',h,0,b'K6CB',1,20,len(table))
h[12:17]=bytes([4,9,9,8,8]);struct.pack_into('<I',h,24,zlib.crc32(table))
struct.pack_into('<I',h,28,zlib.crc32(h[:28]))
(d/'codebook.bin').write_bytes(h+table)
(d/'deck.bin').write_bytes(build_deck(SLIM_CELLS.read_text()))
"""

def prepare(tree,d,pcm=None):
    tree=Path(tree).resolve()
    args=[sys.executable,"-c",PREPARE,str(d)]
    if pcm is not None:
        pcm=np.asarray(pcm,dtype="<i2")
        if len(pcm)%80 or not 800<=len(pcm)<=24000:
            raise ValueError("PCM must be 0.1–3.0 seconds of 8kHz signed 16-bit mono, in 80-sample blocks")
        audio_path=d/"input.pcm"
        audio_path.write_bytes(pcm.tobytes())
        args.append(str(audio_path))
    subprocess.run(args,cwd=tree,check=True)
    h=(tree/"c/k6/k6_mod.h").read_text().split("typedef struct k6_api {")[1].split("} k6_api_t;")[0]
    names=re.findall(r"\(\*(\w+)\)\(",re.sub(r"/\*.*?\*/","",h,flags=re.S))
    # Main API27 has a scalar mb_version *between* callback pointers.
    # Model the real 4-byte ABI slot, never mistake it for a function.
    if re.search(r"\buint32_t\s+mb_version\s*;",h):
        if names.count("stage_packet")!=1 or "mb_version" in names:
            raise ValueError("unexpected KISS mailbox ABI layout")
        names.insert(names.index("stage_packet"),"mb_version")
    rev=subprocess.check_output(["git","-C",str(tree),"rev-parse","HEAD"],text=True).strip()
    dirty=bool(subprocess.check_output(["git","-C",str(tree),"status","--porcelain","--untracked-files=no"],text=True).strip())
    wire=k6mod.build(str(ROOT/"tests/data/k6/sotto_wire_rx_emu.c"),
                     cflags=[*k6mod.CFLAGS,"-I"+str(tree/"c/k6")])
    return dict(wire=wire,code=(d/"module.bin").read_bytes(),entry=int((d/"entry").read_text()),
                cb=(d/"codebook.bin").read_bytes(),deck=(d/"deck.bin").read_bytes(),
                reference=(d/"reference.bit").read_bytes(),names=names,revision=rev,dirty=dirty,pcm=pcm)

REGS=(0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d)
VALS=(0x8000,0xf8,0xe1,0x95,0xc194,0x076b,0x3e36,0x5625,0x4100)
PCM=np.array([(min(i%40,40-i%40)-10)*(400+(i//80%8)*20) for i in range(8320)],dtype="<i2")


class LiveTx(Emu):
    def __init__(self,artifact,backend,*,cpi=1.5,spi_us=34,fd_us=20,arena=14400,adc_cycles=0,cache_bytes=0,spi_hit_us=2,spi_byte_us=0):
        self.pcm=artifact.get("pcm")
        if self.pcm is None:self.pcm=PCM
        self.capture=None;self.capture_start=None;self.sample=0;self.highwater=0
        self.adc_cycles=adc_cycles;self.wall_delay=False
        self.lost=False;self.lease_expired=False;self.keyed=False;self.lease_end=0;self.saved=None
        self.pre_release=0;self.spi_reads=0;self.spi_us=spi_us;self.spi_byte_us=spi_byte_us;self.spi_read_bytes=0;self.keyed_read_bytes=0;self.tx_frames=[]
        self.cache=OrderedDict();self.cache_bytes=cache_bytes;self.cache_used=0
        self.spi_hit_us=spi_hit_us;self.cache_hits=0;self.keyed_reads=0;self.read_sizes=Counter();self.codebook_reads=[]
        self.first_rf_ms=None;self.cb=artifact["cb"];self.deck=artifact["deck"];self.deck_opened=False
        self.tx_backend=backend;self.deadline=0
        room=arena-len(artifact["code"])-24
        if room<=0:raise ValueError("module exceeds arena")
        self.api_level=30 if "mb_version" in artifact["names"] else 29
        super().__init__(artifact["code"],artifact["entry"],np.zeros(1,np.uint8),0,1,
                         cpi=cpi,max_s=35,rf_backend=backend,rf_hz=HZ,level=self.api_level,
                         scratch=room,fd_us=fd_us,stack_win=704)
        old=len(self.names)
        if len(artifact["names"])> (self.stop-self.stubs)//16:
            raise ValueError("API table exceeds emulator stub region")
        if artifact["names"][:old]!=self.names:
            raise ValueError("unsupported API layout")
        self.names=artifact["names"]
        if not all(n in self.names for n in ("mic_stream","fsk_async","resource_read")):
            raise ValueError("API29/30 live Sotto module required")
        for i in range(old,len(self.names)):
            self.mu.mem_write(self.stubs+16*i,b"\x70\x47")
        self.mu.mem_write(self.api,struct.pack("<HH",1,self.api_level)+b"".join(
            struct.pack("<I",0 if n=="mb_version" else (self.stubs+16*i|1))
            for i,n in enumerate(self.names)))
        self.mu.hook_add(self.U.UC_HOOK_CODE,self.on_stub,begin=self.stubs+16*old+1,
                         end=self.stubs+16*len(self.names))
        self.fd_regs[0]=0x4829
        self.initial_regs={r:self.fd_regs.get(r,0) for r in REGS}

    def tick(self,cyc,poll=False):
        # Model CPU time stolen by the periodic ADC ISR. Explicit delays
        # are wall-clock waits; foreground work and SPI/FD access are
        # conservatively treated as preemptible CPU work.
        if self.capture is not None and not self.wall_delay and not self.lost:
            base=self.cyc+cyc
            target=min(len(self.pcm),int((base-self.capture_start)/(FCPU/8000)))
            while True:
                extra=max(0,target-self.sample)*self.adc_cycles
                new=min(len(self.pcm),int((base+extra-self.capture_start)/(FCPU/8000)))
                if new==target:break
                target=new
            cyc+=extra
        super().tick(cyc,poll)
        if self.keyed and self.cyc>=self.lease_end:
            self.lease_expired=True;self.keyed=False;self.capture=None;self.cleanup()
        if self.capture is None or self.lost:return
        target=min(len(self.pcm),int((self.cyc-self.capture_start)/(FCPU/8000)))
        while self.sample<target:
            p,c,o,l=self.mu.mem_read(self.capture+640,4)
            if o>=80 or (p-c)&255>=4:
                self.mu.mem_write(self.capture+643,b"\x01");self.lost=True;break
            n=min(target-self.sample,80-o)
            self.mu.mem_write(self.capture+2*((p&3)*80+o),self.pcm[self.sample:self.sample+n].tobytes())
            self.sample+=n;o+=n
            if o==80:
                p=(p+1)&255;o=0
            self.mu.mem_write(self.capture+640,bytes([p,c,o,l]))
            self.highwater=max(self.highwater,(p-c)&255)

    def wr(self,r,v):
        if not self.keyed:return False
        self.tx_backend.write(r,v,self.fd_regs,self.cyc/FCPU)
        self.fd_regs[r]=v
        self.tick(self.fd_us*96)
        return self.keyed

    def cleanup(self):
        if self.saved is None:return
        saved=self.saved;self.saved=None
        # No recursive watchdog tick during cleanup.
        for r,v in [(0x59,0xf8),(0x3f,0),(2,0),*reversed(list(zip(REGS,saved)))]:
            self.tx_backend.write(r,v,self.fd_regs,self.cyc/FCPU)
            self.fd_regs[r]=v
            super().tick(self.fd_us*96)

    def async_fsk(self,op,ptr,n):
        if not self.keyed:return -1
        if op==0:
            if self.saved is not None or n!=33 or ptr%2 or not self.scr<=ptr<=self.scr+self.scratch-66:
                return -1
            data=bytes(self.mu.mem_read(ptr,66))
            if data[-2:]!=b"\x5a\xa5":return -1
            self.saved=[self.fd_regs.get(r,0) for r in REGS]
            for r,v in [(0x59,0xf8),(2,0),*zip(REGS,VALS),(0x59,0x80f8),(0x59,0xf8)]:
                if not self.wr(r,v):return -1
            for w in struct.unpack("<33H",data):
                if not self.wr(0x5f,w):return -1
            pending=self.tx_backend.read(0xc,self.fd_regs,self.cyc/FCPU)&1
            self.tick(self.fd_us*96)
            if pending or not self.keyed:self.cleanup();return -1
            if not self.wr(0x59,0x8f8):return -1
            if self.tx_backend.active is None:self.cleanup();return -1
            self.tx_frames.append(data)
            if self.capture is not None:self.pre_release+=1
            if self.first_rf_ms is None and self.capture_start is not None:self.first_rf_ms=(self.cyc-self.capture_start)/96000
            self.deadline=self.cyc+FCPU*.7
            return 0
        if op==1 and self.saved is not None:
            if self.cyc>=self.deadline:self.cleanup();return -1
            done=self.tx_backend.read(0xc,self.fd_regs,self.cyc/FCPU)&1
            self.tick(self.fd_us*96)
            if done:self.cleanup();return 1
            return 0
        return -1

    def on_stub(self,uc,addr,size,data):
        name=self.names[(addr-self.stubs)//16]
        a=self.A
        r0,r1,r2,r3=[uc.reg_read(r) for r in (a.UC_ARM_REG_R0,a.UC_ARM_REG_R1,a.UC_ARM_REG_R2,a.UC_ARM_REG_R3)]
        ret=0;us=1
        if name=="resource_open":
            if r0==14 and not self.keyed:
                self.deck_opened=True;ret=len(self.deck)
            else:ret=-1
        elif name=="resource_read":
            src=self.cb if r0==0 else self.deck if r0==1 else b""
            if (r0==1 and not self.deck_opened) or r1+r3>len(src) or not r3:ret=-1
            else:
                uc.mem_write(r2,src[r1:r1+r3]);self.spi_reads+=1;self.spi_read_bytes+=r3
                if self.keyed:
                    self.keyed_reads+=1;self.keyed_read_bytes+=r3;self.read_sizes[r3]+=1
                    if r0==0 and r3==20 and r1>=32 and (r1-32)%20==0:
                        self.codebook_reads.append((r1-32)//20)
                    key=(r0,r1,r3)
                    # Hypothetical exact-range LRU at resource service, not
                    # firmware cache. Include 12B metadata per entry.
                    if key in self.cache:
                        self.cache.move_to_end(key);self.cache_hits+=1
                        us=self.spi_hit_us
                    else:
                        us=self.spi_us+self.spi_byte_us*r3
                        cost=r3+12
                        if cost<=self.cache_bytes:
                            while self.cache_used+cost>self.cache_bytes:
                                _,old=self.cache.popitem(last=False);self.cache_used-=old
                            self.cache[key]=cost;self.cache_used+=cost
                else:us=self.spi_us+self.spi_byte_us*r3
        elif name=="mic_stream":
            if r0==3:
                ret=int(self.cyc>=FCPU*.02 and (self.capture_start is None or
                         self.cyc<self.capture_start+len(self.pcm)*FCPU/8000))
            elif r0==0 and self.keyed and r2==644 and self.scr<=r1<=self.scr+self.scratch-r2:
                self.capture=r1;self.capture_start=self.cyc
            elif r0==1:self.capture=None
            else:ret=-1
        elif name=="fsk_async":ret=self.async_fsk(r0,r1,r2)
        elif name=="tx_on":
            if self.keyed or not 0<r0<=25000:ret=-1
            else:self.keyed=True;self.lease_end=self.cyc+r0*96000
        elif name=="tx_off":
            self.cleanup();self.keyed=False;self.capture=None
        elif name=="lcd_flush":us=30000
        elif name=="delay_us":
            self.wall_delay=True
            try:return super().on_stub(uc,addr,size,data)
            finally:self.wall_delay=False
        elif name in ("say","ticks","fd_read","ms","key_down","scratch","lcd_text"):
            return super().on_stub(uc,addr,size,data)
        else:raise RuntimeError("unexpected API "+name)
        self.tick(us*96)
        uc.reg_write(a.UC_ARM_REG_R0,ret&0xffffffff)


def decode_session(frames):
    """Independent strict FDV1 control and L64 CRC check; no expected payload."""
    good=[];offsets=[];bad=0
    for frame in frames:
        candidates=[]
        for off in (0,2):
            raw=frame[off:off+64]
            p=bytes(x^y for x,y in zip(raw,pager.pn9(64)))
            if len(p)!=64 or pager.crc16_ccitt(p[:62])!=int.from_bytes(p[62:],"big"):continue
            if p[:2]==b"\xe2\x01" and p[2] in (1,2) and p[3]==0 and p[16:20]==b"FDV1" and not any(p[20:62]) and any(p[4:12]) and (
                    (p[2]==1 and p[13:16]==bytes(3)) or
                    (p[2]==2 and 1<=p[14]<=8 and 1<=p[15]<=144 and p[13]-p[12]+1==p[15])):
                candidates.append((off,p))
            elif p[0]==0xa9 and p[2:5]==b"\xc0\x00\x00" and 1<=p[5]<=8 and not any(p[6+6*p[5]:62]):
                candidates.append((off,p))
        if len(candidates)!=1:bad+=1;continue
        off,p=candidates[0];good.append(p);offsets.append(off)
    complete=False;codec=b""
    if bad==0 and len(good)>=3:
        first,last=good[0],good[-1];voice=good[1:-1]
        complete=(first[:3]==b"\xe2\x01\x01" and last[:3]==b"\xe2\x01\x02"
                  and first[4:13]==last[4:13] and any(first[4:12]) and first[13:16]==bytes(3)
                  and last[15]==len(voice) and last[13]==first[12]+len(voice)-1
                  and all(p[0]==0xa9 and p[1]==first[12]+i and p[5]==(8 if i<len(voice)-1 else last[14])
                          for i,p in enumerate(voice)))
        if complete:codec=b"".join(p[6:6+6*p[5]] for p in voice)
    return codec,dict(valid=len(good),invalid=bad,complete=complete,offset_2=offsets.count(2))


def wire_decode(artifact,frames):
    """Execute the actual C wire decoder in a separate Cortex-M0 module."""
    if not frames:return []
    code,entry=artifact["wire"]
    e=Emu(code,entry,np.zeros(1,np.uint8),0,1,max_s=1,level=2)
    n=len(frames)
    if 4+130*n>e.scratch:raise ValueError("wire fixture scratch overflow")
    e.mu.mem_write(e.scr,struct.pack("<I",n)+b"".join(frames))
    result=e.run()
    if result is None or result[0]!=n:raise RuntimeError("wire decoder failed")
    raw=bytes(e.mu.mem_read(e.scr+4+66*n,64*n))
    return [raw[64*i:64*(i+1)] for i in range(n)]


def wire_records_session(records):
    """Reassemble one FDV1 epoch from independently C-validated records."""
    good=[r for r in records if r[0]]
    if len(good)!=len(records) or len(good)<3:return b"",False
    first,last=good[0],good[-1];voice=good[1:-1]
    complete=(first[0]==2 and last[0]==3 and first[2]==last[2] and first[4:12]==last[4:12]
              and last[13]==len(voice) and last[12]==first[2]+len(voice)-1
              and all(r[0]==1 and r[2]==first[2]+i and r[3]==(8 if i<len(voice)-1 else last[3])
                      for i,r in enumerate(voice)))
    return (b"".join(r[16:16+6*r[3]] for r in voice) if complete else b""),complete


def trial(artifact,channel=Channel(),*,cpi=1.5,spi_us=34,fd_us=20,threshold=4,poll_us=1000,adc_cycles=0,cache_bytes=0,spi_hit_us=2,spi_byte_us=0,capture_codec=False,capture_rf_frames=False):
    if not 0<cpi<=10 or not 0<=spi_us<=1000 or not 1<=fd_us<=1000 or not 0<=adc_cycles<12000 or not 0<=cache_bytes<=4096 or not 0<=spi_hit_us or not math.isfinite(spi_byte_us) or not 0<=spi_byte_us<=100 or (cache_bytes and spi_hit_us>spi_us):
        raise ValueError("invalid timing parameters")
    air=VirtualAir(channel);b=VirtualTxBackend(air,240000,HZ)
    t=time.perf_counter()
    try:
        e=LiveTx(artifact,b,cpi=cpi,spi_us=spi_us,fd_us=fd_us,adc_cycles=adc_cycles,cache_bytes=cache_bytes,spi_hit_us=spi_hit_us,spi_byte_us=spi_byte_us)
        result=e.run()
        if result is None:raise RuntimeError("live module exceeded virtual deadline")
        ret,stack=result
        if ret>>31:ret-=1<<32
        b.advance_tx(e.cyc/FCPU)
        tx=b.tx_status()
        if e.keyed or e.capture is not None or e.saved is not None:
            raise RuntimeError("live module returned without cleanup")
        if any(e.fd_regs.get(r,0)!=v for r,v in e.initial_regs.items()):
            raise RuntimeError("RF register restore mismatch")
    finally:b.close()
    frames=[];rx={}
    if air.bursts:
        r=RFBackend(AirSource(air,240000,HZ))
        try:frames,rx=receive(r,tune=HZ,duration=air.bursts[-1].end+.06,frame_bytes=66,
                              threshold=threshold,poll_us=poll_us)
        finally:r.close()
    codec,session=decode_session(frames)
    records=wire_decode(artifact,frames)
    c_codec,c_complete=wire_records_session(records)
    if c_codec!=codec or c_complete!=session["complete"] or sum(bool(r[0]) for r in records)!=session["valid"] or sum(r[0]!=0 and r[1]==2 for r in records)!=session["offset_2"]:
        raise RuntimeError("C wire decoder and independent Python oracle disagree")
    summary=dict(revision=artifact["revision"],source_dirty=artifact["dirty"],
                module_sha256=hashlib.sha256(artifact["code"]).hexdigest(),
                module_bytes=len(artifact["code"]),module_return=ret,
                stack_bytes=stack,cpi=cpi,spi_us=spi_us,spi_byte_us=spi_byte_us,fd_us=fd_us,adc_cycles=adc_cycles,threshold=threshold,poll_us=poll_us,
                channel=channel.__dict__,samples=e.sample,samples_expected=len(e.pcm),pcm_highwater=e.highwater,pcm_lost=e.lost,lease_expired=e.lease_expired,
                spi_reads=e.spi_reads,keyed_reads=e.keyed_reads,spi_read_bytes=e.spi_read_bytes,keyed_read_bytes=e.keyed_read_bytes,read_sizes=dict(e.read_sizes),
                codebook_profile=codebook_profile(e.codebook_reads),
                cache_bytes=cache_bytes,cache_hits=e.cache_hits,spi_hit_us=spi_hit_us,tx_before_release=e.pre_release,first_rf_ms=e.first_rf_ms,
                tx=tx,rx=rx.get("fsk"),received=len(frames),session=session,
                codec_frames=len(codec)//6,codec_exact=codec==artifact["reference"],
                wire_decoder_agrees=True,
                logs=e.said,wall_seconds=round(time.perf_counter()-t,3))
    if capture_codec:
        # Only requested captures expose binary voice frames; default is JSON-safe.
        summary["received_codec_bytes"]=codec
    if capture_rf_frames:
        # Offline-only opt-in raw IQ receiver result. No expected TX bytes
        # substituted for recovered frames.
        summary["received_physical_frames"]=frames
    return summary



def codebook_profile(indices):
    """Observed vector locality; an offline optimization aid, not a cache claim."""
    if not indices:return dict(reads=0,unique=0)
    from collections import Counter
    seen=Counter(indices)
    neighbor=sum(abs(a-b)==1 for a,b in zip(indices,indices[1:]))
    recent_8=sum(v in indices[max(0,i-8):i] for i,v in enumerate(indices))
    blocks={}
    for b in (1,2,4,8):
        from collections import OrderedDict
        # Fixed 256-byte SRAM model, 12-byte metadata per entry.
        capacity=max(1,256//(20*b+12))
        cache=OrderedDict();hits=0
        for v in indices:
            k=v//b
            if k in cache:
                hits+=1;cache.move_to_end(k)
            else:
                cache[k]=None
                if len(cache)>capacity:cache.popitem(last=False)
        blocks[str(b)]=dict(hits=hits,transactions=len(indices)-hits,
                             bytes_read=(len(indices)-hits)*20*b)
    return dict(reads=len(indices),unique=len(seen),adjacent_pairs=neighbor,
                recent_8_reuses=recent_8,frequency_top5=seen.most_common(5),
                estimated_256b_prefetch=blocks)

def accepted(r):
    return (r["module_return"]==r["codec_frames"] and r["codec_frames"]>0
            and r["samples"]==r["samples_expected"] and not r["pcm_lost"] and not r["lease_expired"]
            and r["codec_exact"] and r["wire_decoder_agrees"] and r["session"]["complete"]
            and r["tx"]["completed"]==r["received"] and not r["tx"]["active"]
            and not any(r["tx"][k] for k in ("aborted","rejected","overflows"))
            and r["rx"] is not None and not any(r["rx"][k] for k in ("overruns","underflows","gaps")))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sotto-tree",type=Path,default=ROOT,help="checkout containing API29 experimental or API30 main-layout live Sotto source")
    for n,k,d in [("cpi",float,[1.5]),("spi-us",float,[34]),("fd-us",float,[20]),
                  ("adc-cycles",int,[0]),("cache-bytes",int,[0]),("threshold",int,[4]),("poll-us",int,[1000]),("cn",float,[25]),
                  ("prefix-every",int,[0])]:
        p.add_argument("--"+n,type=values(k),default=d)
    p.add_argument("--spi-hit-us",type=float,default=2)
    p.add_argument("--spi-byte-us",type=float,default=0,help="additional modeled SPI us per returned byte; 0 preserves legacy constant-per-read timing")
    p.add_argument("--json",type=Path)
    p.add_argument("--pcm-wav",type=Path,help="optional 8kHz mono PCM16 WAV fixture (0.1-3s, multiples of 10ms)")
    p.add_argument("--strict",action="store_true",help="exit nonzero if any trial fails acceptance")
    a=p.parse_args()
    if not (a.sotto_tree/"c/k6/mod/sotto_live_tx.c").is_file():
        p.error("select a checkout containing the live Sotto TX module with --sotto-tree")
    from itertools import product
    axes=[a.cpi,a.spi_us,a.fd_us,a.adc_cycles,a.cache_bytes,a.threshold,a.poll_us,a.cn,a.prefix_every]
    if math.prod(map(len,axes))>128:p.error("maximum 128 trials")
    pcm=None
    if a.pcm_wav is not None:
        try:
            with wave.open(str(a.pcm_wav),"rb") as w:
                if w.getnchannels()!=1 or w.getframerate()!=8000 or w.getsampwidth()!=2 or w.getcomptype()!="NONE":
                    p.error("WAV must be 8000 Hz mono uncompressed signed 16-bit")
                pcm=np.frombuffer(w.readframes(w.getnframes()),dtype="<i2").copy()
            if len(pcm)%80 or not 800<=len(pcm)<=24000:
                p.error("WAV must have 800-24000 samples, in blocks of 80")
        except (wave.Error,OSError) as exc:p.error(str(exc))
    rows=[]
    with tempfile.TemporaryDirectory() as d:
        artifact=prepare(a.sotto_tree,Path(d),pcm=pcm)
        for cpi,spi,fd,adc,cache,threshold,poll,cn,prefix in product(*axes):
            r=trial(artifact,Channel(cn_db=cn,prefix_every=prefix),cpi=cpi,spi_us=spi,
                    fd_us=fd,adc_cycles=adc,cache_bytes=cache,spi_hit_us=a.spi_hit_us,spi_byte_us=a.spi_byte_us,threshold=threshold,poll_us=poll)
            r["accepted"]=accepted(r)
            rows.append(r);print(json.dumps(r),flush=True)
    if a.json:a.json.write_text(json.dumps(rows,indent=2)+"\n")


    if a.strict and not all(r["accepted"] for r in rows):sys.exit(1)

if __name__=="__main__":main()
