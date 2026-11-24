"""Execute the real bounded TX pump with synthetic PCM and async transport.
The C test driver also runs as ARMv6-M code; it is not a physical RF test."""
import ctypes as C
import importlib.util
from pathlib import Path
import subprocess
import pytest
ROOT=Path(__file__).resolve().parents[1]
SRC=r"""
#include "sotto_live_engine.h"
typedef struct {
    sl_engine_t s;
    unsigned blocks, flush, txstart, txdone, stopped, aborted, ended, delay, timer, fault;
    uint32_t hash;
} fixture;
static int enc(void*v,const int16_t*p,uint8_t*f){
    fixture*t=v;unsigned i;
    for(i=0;i<80;i++)if(p[i]!=(int16_t)(t->blocks*80u+i))return -1;
    t->blocks++;
    if(t->blocks%4)return 0;
    for(i=0;i<6;i++)f[i]=(uint8_t)(t->blocks+i);
    return 1;
}
static int flush(void*v,uint8_t*f){
    fixture*t=v;unsigned i;
    if(t->flush++)return 0;
    for(i=0;i<6;i++)f[i]=(uint8_t)(0x70+i);
    return 1;
}
static int start(void*v,const uint16_t*w){
    fixture*t=v;unsigned i;
    if(t->timer||t->fault==1||w[32]!=0xa55a)return -1;
    t->txstart++;t->timer=t->delay;
    for(i=0;i<33;i++)t->hash=(t->hash^w[i])*16777619u;
    return 0;
}
static int poll(void*v){
    fixture*t=v;
    if(t->fault==2)return -1;
    if(t->timer&&!--t->timer){t->txdone++;return 1;}
    return 0;
}
static void stop(void*v){((fixture*)v)->stopped++;}
static void abort_tx(void*v){fixture*t=v;t->aborted++;t->timer=0;}
static void end(void*v){((fixture*)v)->ended++;}
static const sl_ops_t ops={enc,flush,start,poll,stop,abort_tx,end};
unsigned size(void){return sizeof(fixture);}
int init(fixture*t,unsigned delay){
    unsigned i;for(i=0;i<sizeof(*t);i++)((uint8_t*)t)[i]=0;
    t->delay=delay;t->hash=2166136261u;
    return sl_engine_begin(&t->s,0x1234,0x5678);
}
int sample(fixture*t,int x){return sl_pcm_sample(&t->s.mic,(int16_t)x);}
int tick(fixture*t){return sl_engine_tick(&t->s,&ops,t);}
int release(fixture*t){return sl_engine_release(&t->s,&ops,t);}
void fail(fixture*t,unsigned f){t->fault=f;}
unsigned get(fixture*t,unsigned i){
    switch(i){
    case 0:return t->blocks;case 1:return t->s.tx.encoded;
    case 2:return t->txstart;case 3:return t->txdone;
    case 4:return t->stopped;case 5:return t->aborted;case 6:return t->ended;
    case 7:return t->s.phase;case 8:return t->s.tx.count;
    case 9:return t->hash;default:return t->s.mic.lost;
    }
}
"""
@pytest.fixture(scope="module")
def native(tmp_path_factory):
    p=tmp_path_factory.mktemp("engine");(p/"s.c").write_text(SRC)
    subprocess.run(["clang","-shared","-fPIC","-O2","-Wall","-Wextra","-Werror",
        "-I",str(ROOT/"c/k6/mod"),str(p/"s.c"),"-o",str(p/"s.so")],check=True)
    l=C.CDLL(str(p/"s.so"))
    for name,n in (("init",2),("sample",2),("tick",1),("release",1),("fail",2),("get",2)):
        getattr(l,name).argtypes=[C.c_void_p]+[C.c_uint]*(n-1)
    l.get.restype=C.c_uint
    return l
def run(call,s,blocks):
    assert call("init",s,16)==0
    peak=0
    for i in range(blocks):
        for j in range(80):assert call("sample",s,(i*80+j)&0xffff)==0
        assert call("tick",s)==0
        peak=max(peak,call("get",s,8))
    assert call("get",s,2)>0 # transmitted before release
    assert call("release",s)==0
    for _ in range(200):
        r=call("tick",s)
        assert r>=0
        if r==1:break
    else:pytest.fail("did not finish")
    assert call("get",s,0)==blocks
    assert call("get",s,1)==blocks//4+1
    assert call("get",s,2)==call("get",s,3)
    assert [call("get",s,i) for i in (4,5,6,7)]==[1,0,1,3]
    assert peak<=4
    return call("get",s,9)
def test_long(native):
    l=native;s=C.create_string_buffer(l.size())
    assert l.size()<=1100
    run(lambda n,*a:getattr(l,n)(*a),s,24000)
@pytest.mark.parametrize("fault",[1,2])
def test_transport_failure(native,fault):
    l=native;s=C.create_string_buffer(l.size());assert l.init(s,16)==0
    for i in range(400):
        assert l.sample(s,i)==0
        if i%80==79:assert l.tick(s)==0
    l.fail(s,fault)
    for _ in range(10):
        if l.tick(s)<0:break
    else:pytest.fail("fault not propagated")
    assert l.get(s,5)==1 and l.get(s,6)==0 and l.get(s,7)==4
    assert l.tick(s)<0 and l.get(s,5)==1
def test_overrun(native):
    l=native;s=C.create_string_buffer(l.size());assert l.init(s,16)==0
    for i in range(320):assert l.sample(s,i)==0
    assert l.sample(s,320)<0 and l.get(s,10)==1
    assert l.tick(s)<0 and l.get(s,5)==1
def test_thumb(tmp_path,native):
    pytest.importorskip("unicorn");pytest.importorskip("capstone")
    src=tmp_path/"s.c";src.write_text(SRC)
    elf=tmp_path/"s.elf";obj=tmp_path/"s.o"
    subprocess.run(["clang","--target=thumbv6m-none-eabi","-mcpu=cortex-m0","-mthumb",
        "-Oz","-ffreestanding","-fno-builtin","-ffunction-sections","-fdata-sections",
        "-I",str(ROOT/"c/k6/mod"),"-c",str(src),"-o",str(obj)],check=True)
    subprocess.run(["ld.lld","-T",str(ROOT/"c/fixed/cm4/link.ld"),"--gc-sections","-e","init",
        *[x for n in ("size","sample","tick","release","get","fail") for x in ("-u",n)],
        str(obj),"-o",str(elf)],check=True)
    spec=importlib.util.spec_from_file_location("engine_m0",ROOT/"c/fixed/cm0/run.py")
    m0=importlib.util.module_from_spec(spec);spec.loader.exec_module(m0);m0.WS=0
    m=m0.M0(str(elf));s=m0.cm4.IO+0x1000
    def call(n,*a):return m.call(n,*a)[0]
    assert call("size")<=1100
    # Long native test exercises counter wrap; Thumb runs shorter cadence.
    h=run(call,s,104)
    ns=C.create_string_buffer(native.size())
    assert h==run(lambda n,*a:getattr(native,n)(*a),ns,104)
