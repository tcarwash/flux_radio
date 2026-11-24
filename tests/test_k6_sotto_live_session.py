"""Run bounded FDV1 streaming scheduler as native C and Cortex-M0 code."""
import ctypes as C
import subprocess
from pathlib import Path
import pytest
from modem import pager
ROOT=Path(__file__).resolve().parents[1]
SRC=r"""
#include "sotto_live_session.h"
unsigned size(void){return sizeof(sotto_live_t);}
int start(sotto_live_t*s){return sl_begin(s,0x12345678,0x90abcdef);}
int push(sotto_live_t*s,const uint8_t*p){return sl_push(s,p);}
int finish(sotto_live_t*s){return sl_finish(s);}
int ack(sotto_live_t*s,int ok){return sl_ack(s,ok);}
unsigned count(sotto_live_t*s){return s->count;}
unsigned encoded(sotto_live_t*s){return s->encoded;}
int pull(sotto_live_t*s,uint8_t*p){
 const uint16_t*w=sl_peek(s);unsigned i;if(!w)return 0;
 for(i=0;i<33;i++){p[2*i]=w[i];p[2*i+1]=w[i]>>8;}return 66;
}
"""
@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    p=tmp_path_factory.mktemp("live-session");(p/"s.c").write_text(SRC)
    subprocess.run(["clang","-shared","-fPIC","-O2","-Wall","-Wextra","-Werror",
        "-I",str(ROOT/"c/k6/mod"),str(p/"s.c"),"-o",str(p/"s.so")],check=True)
    l=C.CDLL(str(p/"s.so"))
    for name,args in {"start":[C.c_void_p],"push":[C.c_void_p,C.c_void_p],
        "finish":[C.c_void_p],"ack":[C.c_void_p,C.c_int],"count":[C.c_void_p],
        "encoded":[C.c_void_p],"pull":[C.c_void_p,C.c_void_p]}.items():
        getattr(l,name).argtypes=args
    return l
def frame(i):
    return i.to_bytes(4,"little")+b"ab"
def decode(w):
    assert w[64:]==b"\x5a\xa5"
    p=bytes(a^b for a,b in zip(w[:64],pager.pn9(64)))
    assert pager.crc16_ccitt(p[:62])==int.from_bytes(p[62:],"big")
    return p
def pop(l,s):
    b=C.create_string_buffer(66)
    assert l.pull(s,b)==66
    assert l.ack(s,1)==0
    return decode(b.raw)
@pytest.mark.parametrize("n",[0,1,8,9,1151,1152,1153,6000])
def test_duration_independent(lib,n):
    l=lib;s=C.create_string_buffer(l.size());assert l.size()<=352
    assert l.start(s)==0
    out=[];peak=0
    # A bounded 160ms TX consumer against one 40ms speech frame.
    for i in range(n):
        assert l.push(s,frame(i))==0
        peak=max(peak,l.count(s))
        if i%4==3 and l.count(s):out.append(pop(l,s))
    while l.finish(s)==-2:out.append(pop(l,s))
    while l.count(s):out.append(pop(l,s))
    assert b"".join(p[6:6+6*p[5]] for p in out if p[0]==0xa9)==b"".join(map(frame,range(n)))
    assert l.encoded(s)==n and peak<=4
    tag=None;last=None;segments=0
    for p in out:
        if p[0]==0xe2 and p[2]==1:
            assert tag is None;tag=p[4:12];last=111;segments=0
        elif p[0]==0xe2 and p[2]==2:
            assert tag==p[4:12] and p[13]==last and p[15]==segments
            tag=None
        else:
            assert tag and p[0]==0xa9 and p[1]==last+1
            last=p[1];segments+=1
    assert tag is None
def test_backpressure_and_fault(lib):
    l=lib;s=C.create_string_buffer(l.size());assert l.start(s)==0
    i=0
    while l.push(s,frame(i))==0:i+=1
    assert i<40 and l.count(s)==4 and l.encoded(s)==i
    before=s.raw
    assert l.push(s,frame(i))==-2 and s.raw==before
    pop(l,s)
    assert l.push(s,frame(i))==0
    assert l.ack(s,0)==-4
    assert l.count(s)==0 and l.push(s,frame(i+1))<0 and l.finish(s)<0
def test_thumb(tmp_path):
    pytest.importorskip("unicorn");pytest.importorskip("capstone")
    import importlib.util
    src=tmp_path/"s.c";src.write_text(SRC)
    elf=tmp_path/"s.elf";obj=tmp_path/"s.o"
    subprocess.run(["clang","--target=thumbv6m-none-eabi","-mcpu=cortex-m0","-mthumb",
        "-Oz","-ffreestanding","-fno-builtin","-ffunction-sections","-fdata-sections",
        "-I",str(ROOT/"c/k6/mod"),"-c",str(src),"-o",str(obj)],check=True)
    subprocess.run(["ld.lld","-T",str(ROOT/"c/fixed/cm4/link.ld"),"--gc-sections","-e","start",
        *[x for name in ("size","push","finish","ack","count","pull","encoded") for x in ("-u",name)],
        str(obj),"-o",str(elf)],check=True)
    spec=importlib.util.spec_from_file_location("live_cm0",ROOT/"c/fixed/cm0/run.py")
    m0=importlib.util.module_from_spec(spec);spec.loader.exec_module(m0);m0.WS=0
    m=m0.M0(str(elf));s=m0.cm4.IO+0x1000;buf=m0.cm4.IO+0x2000
    assert m.call("size")[0]<=352
    assert m.call("start",s)[0]==0
    packets=[]
    def drain():
        assert m.call("pull",s,buf)[0]==66
        packets.append(decode(bytes(m.mu.mem_read(buf,66))))
        assert m.call("ack",s,1)[0]==0
    for i in range(1161):
        m.mu.mem_write(buf,frame(i));assert m.call("push",s,buf)[0]==0
        if i%4==3 and m.call("count",s)[0]:drain()
    assert m.call("finish",s)[0]==0
    while m.call("count",s)[0]:drain()
    assert m.call("encoded",s)[0]==1161
    assert sum(p[0]==0xe2 and p[2]==1 for p in packets)==2
    assert sum(p[0]==0xe2 and p[2]==2 for p in packets)==2
    assert b"".join(p[6:6+6*p[5]] for p in packets if p[0]==0xa9)==b"".join(map(frame,range(1161)))
