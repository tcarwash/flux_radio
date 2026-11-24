"""Tests for the standalone Sotto TX module's bounded record/seal/send core.

Actual ARMv6-M compiler + optional Unicorn execution; no RF or hardware I/O.
"""
from pathlib import Path
import ctypes
import importlib.util
import struct
import subprocess
import pytest
from modem import pager

ROOT = Path(__file__).resolve().parents[1]
HEADER = ROOT / "c/k6/mod"
SOURCE = r"""
#include "sotto_tx_stage.h"
unsigned stage_bytes(void) {return sizeof(sotto_stage_t);}
int start(sotto_stage_t *s, unsigned id) {
    return sotto_stage_begin(s,id,0x12345678u,0x90abcdefu);
}
int record(sotto_stage_t *s,const uint8_t *p) {return sotto_stage_record(s,p);}
int seal(sotto_stage_t *s,unsigned lost,int ok) {return sotto_stage_seal(s,lost,ok);}
int pull(sotto_stage_t *s,uint8_t *p) {
    const uint16_t *w=sotto_stage_peek(s);
    unsigned i;
    if(!w)return 0;
    for(i=0;i<33u;++i){p[i*2u]=(uint8_t)w[i];p[i*2u+1u]=(uint8_t)(w[i]>>8);}
    return 66;
}
int confirm(sotto_stage_t *s,int ok) {return sotto_stage_confirm(s,ok);}
unsigned phase(sotto_stage_t *s) {return s->phase;}
"""
@pytest.fixture(scope="module")
def native(tmp_path_factory):
    d=tmp_path_factory.mktemp("sotto-stage")
    src=d/"stage.c";src.write_text(SOURCE)
    lib=d/"stage.so"
    subprocess.run(["clang","-std=c99","-Wall","-Wextra","-Werror","-O2",
                    "-fPIC","-shared","-I",str(HEADER),str(src),"-o",str(lib)],check=True)
    c=ctypes.CDLL(str(lib))
    for name,n in (("start",2),("record",2),("seal",3),("pull",2),("confirm",2),("phase",1)):
        getattr(c,name).argtypes=[ctypes.c_void_p]+[
            ctypes.c_void_p if name in ("record","pull") else ctypes.c_uint
        ]*(n-1)
        getattr(c,name).restype=ctypes.c_int
    c.stage_bytes.restype=ctypes.c_uint
    return c

def unwhiten(w):
    assert len(w)==66 and w[64:]==b"\x5a\xa5"
    p=bytes(a^b for a,b in zip(w[:64],pager.pn9(64)))
    assert pager.crc16_ccitt(p[:62])==int.from_bytes(p[62:],"big")
    return p

def pull(c,s):
    b=ctypes.create_string_buffer(66)
    return bytes(b.raw) if c.pull(s,b)==66 else None

@pytest.mark.parametrize("count",[1,7,8,9,16,26,79,80])
def test_complete_recording_then_framing(native,count):
    c=native;s=ctypes.create_string_buffer(c.stage_bytes())
    assert 480<c.stage_bytes()<=576
    assert c.start(s,112)==0
    assert pull(c,s) is None
    for i in range(count):
        assert c.record(s,bytes([i]*6))==0
    assert c.seal(s,0,1)==0
    packets=[]
    while (w:=pull(c,s)) is not None:
        assert pull(c,s)==w
        packets.append(unwhiten(w))
        assert c.confirm(s,1)==0
    assert c.phase(s)==4
    assert len(packets)==(count+7)//8+2
    assert packets[0][0]==0xe2 and packets[0][2]==1
    assert packets[-1][0]==0xe2 and packets[-1][2]==2
    assert b"".join(p[6:6+6*p[5]] for p in packets[1:-1])==b"".join(bytes([i]*6) for i in range(count))
    assert packets[-1][14]==(count-1)%8+1
    assert packets[-1][15]==(count+7)//8

@pytest.mark.parametrize("lost,ok",[(1,1),(0,0)])
def test_loss_never_emits(native,lost,ok):
    c=native;s=ctypes.create_string_buffer(c.stage_bytes())
    assert c.start(s,112)==0
    assert c.record(s,b"abc123")==0
    assert c.seal(s,lost,ok)<0
    assert c.phase(s)==5 and pull(c,s) is None

def test_failed_tx_never_retries(native):
    c=native;s=ctypes.create_string_buffer(c.stage_bytes())
    assert c.start(s,112)==0
    assert c.record(s,b"abc123")==0
    assert c.seal(s,0,1)==0 and pull(c,s)
    assert c.confirm(s,0)<0
    assert c.phase(s)==5 and pull(c,s) is None

def test_capacity_and_id_bounds(native):
    c=native;s=ctypes.create_string_buffer(c.stage_bytes())
    assert c.start(s,0)<0 and pull(c,s) is None
    assert c.start(s,247)<0 and pull(c,s) is None
    assert c.start(s,246)==0
    for _ in range(80):assert c.record(s,b"abc123")==0
    assert c.record(s,b"abc123")<0
    assert pull(c,s) is None

def test_arm_emulator(tmp_path):
    pytest.importorskip("unicorn")
    pytest.importorskip("capstone")
    src=tmp_path/"stage.c";src.write_text(SOURCE)
    obj=tmp_path/"stage.o";elf=tmp_path/"stage.elf"
    subprocess.run(["clang","--target=thumbv6m-none-eabi","-mcpu=cortex-m0",
                    "-mthumb","-mfloat-abi=soft","-Oz","-ffreestanding",
                    "-fno-builtin","-std=c99","-Wall","-Wextra","-Werror",
                    "-ffunction-sections","-fdata-sections","-I",str(HEADER),
                    "-c",str(src),"-o",str(obj)],check=True)
    subprocess.run(["ld.lld","-T",str(ROOT/"c/fixed/cm4/link.ld"),"--gc-sections",
                    "-e","start","-u","record","-u","seal","-u","pull",
                    "-u","confirm","-u","stage_bytes","-u","phase",
                    str(obj),"-o",str(elf)],check=True)
    spec=importlib.util.spec_from_file_location("sotto_stage_cm0",ROOT/"c/fixed/cm0/run.py")
    cm0=importlib.util.module_from_spec(spec);spec.loader.exec_module(cm0)
    cm0.WS=0
    m=cm0.M0(str(elf))
    state=cm0.cm4.IO+0x1000
    data=cm0.cm4.IO+0x2000
    assert m.call("stage_bytes")[0]<=576
    assert m.call("start",state,112)[0]==0
    assert m.call("pull",state,data)[0]==0
    for i in range(26):
        m.mu.mem_write(data,bytes([i])*6)
        assert m.call("record",state,data)[0]==0
    assert m.call("seal",state,0,1)[0]==0
    packets=[]
    for _ in range(6):
        assert m.call("pull",state,data)[0]==66
        packets.append(unwhiten(bytes(m.mu.mem_read(data,66))))
        assert m.call("confirm",state,1)[0]==0
    assert m.call("phase",state)[0]==4
    assert m.call("pull",state,data)[0]==0
    assert b"".join(p[6:6+6*p[5]] for p in packets[1:-1])==b"".join(bytes([i])*6 for i in range(26))
    assert packets[-1][14:16]==bytes([2,4])
