"""Run the actual catalog-format module bytes in Cortex-M0 Unicorn.

All API functions except scratch, say and read-only SPI are hard failures.
The independent native upstream encoder provides the exact wire oracle.
No physical device or private PCM is needed.
"""
import ctypes
import struct
import subprocess
import zlib
import pytest

from tools.k6_sotto_module import build,FIXED
from tools.k6_sotto_ivf_deck import build_deck,SLIM_CELLS
from modem import k6mod,pager

@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    code,entry=build()
    d=tmp_path_factory.mktemp("sotto-module-ref")
    so=d/"slim.so"
    subprocess.run(["cc","-O2","-shared","-fPIC","-DSOTTO_V36","-DSOTTO_PITCH_THIRD",
                    "-DFX_NARROW_MUL",str(FIXED/"sotto_fx_slim.c"),
                    str(FIXED/"fx_tables.c"),"-o",str(so)],check=True)
    c=ctypes.CDLL(str(so))
    c.slim_enc_bytes.restype=ctypes.c_uint
    for name,n in (("slim_enc_init",1),("slim_encode10",3),("slim_flush",2)):
        getattr(c,name).argtypes=[ctypes.c_void_p]*n
    s=ctypes.create_string_buffer(c.slim_enc_bytes())
    c.slim_enc_init(s)
    x=(ctypes.c_int16*80)()
    out=ctypes.create_string_buffer(6)
    bits=bytearray()
    for block in range(104):
        for i in range(80):
            j=(block*80+i)%40
            x[i]=(min(j,40-j)-10)*(400+(block%8)*20)
        if c.slim_encode10(s,x,out):bits.extend(out.raw)
    while c.slim_flush(s,out):bits.extend(out.raw)
    assert len(bits)==156
    table=b"".join(bytes((ctypes.c_char*n).in_dll(c,f"fx_cb{i}"))
                   for i,n in enumerate((10240,10240,5120,5120)))
    assert zlib.crc32(table)==0xd5856ecd
    h=bytearray(32)
    struct.pack_into("<4sHHI",h,0,b"K6CB",1,20,len(table))
    h[12:17]=bytes([4,9,9,8,8])
    struct.pack_into("<I",h,24,zlib.crc32(table))
    struct.pack_into("<I",h,28,zlib.crc32(h[:28]))
    deck=build_deck(SLIM_CELLS.read_text())
    image=k6mod.image(deck,0,1,None,level=5,lib=14,cards=True)
    return code,entry,bytes(h)+table,image,bytes(bits)

def wire(plain):
    p=bytes(plain)+pager.crc16_ccitt(plain).to_bytes(2,"big")
    return bytes(a^b for a,b in zip(p,pager.pn9(64)))+b"\x5a\xa5"

def expected(bits):
    def ctrl(kind,last,n,segments):
        h=bytearray(62);h[:4]=bytes([0xe2,1,kind,0])
        h[4:12]=b"SOTTOTES";h[12:16]=bytes([112,last,n,segments])
        h[16:20]=b"FDV1";return wire(h)
    packets=[ctrl(1,0,0,0)]
    for i in range(4):
        part=bits[i*48:(i+1)*48]
        h=bytearray(62)
        h[:6]=bytes([0xa9,112+i,0xc0,0,0,len(part)//6])
        h[6:6+len(part)]=part;packets.append(wire(h))
    packets.append(ctrl(2,115,2,4))
    digest=2166136261
    for b in b"".join(packets):digest=((digest^b)*16777619)&0xffffffff
    return f"SOTTO dry FNV {digest:08x}"

def emulate(artifacts,base,room=15408,corrupt=None):
    u=pytest.importorskip("unicorn")
    from unicorn import arm_const as a
    code,entry,cb,deck,bits=artifacts
    m=u.Uc(u.UC_ARCH_ARM,u.UC_MODE_THUMB|u.UC_MODE_MCLASS)
    m.ctl_set_cpu_model(a.UC_CPU_ARM_CORTEX_M0)
    m.mem_map(0x20000000,0x10000)
    m.mem_map(0x08000000,0x1000)
    m.mem_write(base,code)
    stop=0x08000000
    m.mem_write(stop,b"\x00\xbe")
    stubs={0x08000100:"say",0x08000104:"scratch",0x08000108:"fl_read",
           0x0800010c:"forbidden"}
    for addr in stubs:m.mem_write(addr,b"\x70\x47")
    api=0x2000d000
    ptrs=[0x0800010d]*22
    # 4-byte header then API function pointers in k6_mod.h.
    ptrs[0]=0x08000101
    ptrs[9]=0x08000105
    ptrs[16]=0x08000109
    m.mem_write(api,struct.pack("<HH",1,5)+struct.pack("<22I",*ptrs))
    scratch=(base+len(code)+8+3)&~3
    available=room-(scratch-(base-16))
    spi=bytearray(b"\xff"*0x100000)
    spi[:len(cb)]=cb
    deckaddr=0x10000+14*0x4000
    spi[deckaddr:deckaddr+len(deck)]=deck
    if corrupt=="cb":spi[50]^=1
    if corrupt=="deck":spi[deckaddr+32]^=1
    calls=[];reads=0
    def hook(uc,addr,size,data):
        nonlocal reads
        name=stubs[addr]
        r0=uc.reg_read(a.UC_ARM_REG_R0)
        if name=="say":
            msg=bytes(uc.mem_read(r0,64)).split(b"\0",1)[0].decode()
            calls.append(msg)
        elif name=="scratch":
            uc.mem_write(r0,struct.pack("<I",available))
            uc.reg_write(a.UC_ARM_REG_R0,scratch)
        elif name=="fl_read":
            r1=uc.reg_read(a.UC_ARM_REG_R1)
            n=uc.reg_read(a.UC_ARM_REG_R2)
            assert 0x100000<=r0<=0x200000-n
            reads+=1
            if corrupt=="read" and reads>450:
                uc.reg_write(a.UC_ARM_REG_R0,0xffffffff)
            else:
                uc.mem_write(r1,bytes(spi[r0-0x100000:r0-0x100000+n]))
                uc.reg_write(a.UC_ARM_REG_R0,0)
        else:raise AssertionError("Unexpected API call, including RF or flash write")
    for addr in stubs:m.hook_add(u.UC_HOOK_CODE,hook,begin=addr,end=addr)
    stack=0x2000f000
    m.mem_write(stack-2048,b"\xa5"*2048)
    top=(scratch+available)&~7
    m.mem_write(top-2048,b"\xa5"*2048)
    m.reg_write(a.UC_ARM_REG_R0,api)
    m.reg_write(a.UC_ARM_REG_SP,stack)
    m.reg_write(a.UC_ARM_REG_LR,stop|1)
    m.emu_start((base+entry)|1,stop,count=60_000_000)
    assert m.reg_read(a.UC_ARM_REG_PC)==stop,"instruction cap or unexpected exit"
    r=m.reg_read(a.UC_ARM_REG_R0)
    if r&0x80000000:r-=1<<32
    if r==26:
        # stage struct: tag_hi/tag_lo, 33 uint16_t words, then codec bytes.
        assert bytes(m.mem_read(scratch+74,156))==bits
    assert m.reg_read(a.UC_ARM_REG_SP)==stack
    painted=bytes(m.mem_read(stack-2048,2048))
    host_used=next((2048-i for i,b in enumerate(painted) if b!=0xa5),0)
    assert host_used<256
    painted=bytes(m.mem_read(top-2048,2048))
    used=next((2048-i for i,b in enumerate(painted) if b!=0xa5),0)
    if r==26:
        assert bytes(m.mem_read(top-2080,32))==b"\xa5"*32
    return r,calls,reads,used

@pytest.mark.parametrize("base",[0x20000110,0x20004110])
def test_actual_relocated_catalog_bytes(artifacts,base):
    code,entry,cb,deck,bits=artifacts
    assert entry==16 and k6mod.module_name(code)=="SOTTO TEST"
    r,log,reads,stack=emulate(artifacts,base)
    assert r==26 and log==[expected(bits)]
    assert reads>450 and stack<=1536
    print(f"module={len(code)} bytes, stack={stack}, SPI reads={reads}, {log[0]}")

@pytest.mark.parametrize("fault,room,rc",[("cb",15408,-10),("deck",15408,-10),
                                         ("read",15408,-12),(None,11776,-8)])
def test_fail_closed(artifacts,fault,room,rc):
    r,log,reads,stack=emulate(artifacts,0x20000110,room=room,corrupt=fault)
    assert r==rc and not any("dry FNV" in s for s in log)
    if room==11776:assert reads==0
