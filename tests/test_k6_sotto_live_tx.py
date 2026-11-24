"""Actual relocatable Sotto TX bytes with a bounded deterministic HAL model.
No radio, real PTT, RF or measured timing. Tests codec and transport contract."""
import re,struct
from pathlib import Path
import pytest
from modem import k6mod,pager
from tools.k6_sotto_module import build
from tests.test_k6_sotto_module import artifacts
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture(scope="module")
def txmod():return build(host_resources=True,live_tx=True)

def run(txmod,artifacts,fault):
    u=pytest.importorskip("unicorn")
    from unicorn import arm_const as a
    code,entry=txmod;_,_,cb,deck,bits=artifacts
    m=u.Uc(u.UC_ARCH_ARM,u.UC_MODE_THUMB|u.UC_MODE_MCLASS)
    m.ctl_set_cpu_model(a.UC_CPU_ARM_CORTEX_M0)
    m.mem_map(0x20000000,0x10000);m.mem_map(0x08000000,0x1000)
    base=0x20001690;end=base-16+14400-16
    scratch=(base+len(code)+8+3)&~3
    room=end-scratch
    assert len(code)+24<=10240
    m.mem_write(base,code);m.mem_write(end,b"\xa5"*16)
    stop=0x08000000;api=0x2000d000
    # Use the append-only actual header field order, not guessed offsets.
    h=(ROOT/"c/k6/k6_mod.h").read_text().split("typedef struct k6_api {")[1].split("} k6_api_t;")[0]
    h=re.sub(r"/\*.*?\*/","",h,flags=re.S)
    names=re.findall(r"\(\*(\w+)\)\(",h)
    assert names[:14]==["say","ticks","fd_read","fd_write","delay_us","tune","rx_hz","ms","key_down","scratch","tx_on","tx_off","tx_pa","key_poll"]
    stubs={0x08000100+4*i:n for i,n in enumerate(names)}
    m.mem_write(api,struct.pack("<HH",1,29)+struct.pack("<"+"I"*len(names),*(x|1 for x in stubs)))
    now=0;capture_at=None;next_sample=0;ring=0;keyed=False;active=False
    inflight=None;frames=[];completed=0;pre_release=0;logs=[];calls=[]
    def advance(us):
        nonlocal now,next_sample
        now+=us
        if not active or fault=="no_samples":return
        until=min(now,capture_at+1040000)
        while next_sample<8320 and capture_at+(next_sample+1)*125<=until:
            p,c,o,l=bytes(m.mem_read(ring+640,4))
            if (p-c)&255>=4:
                m.mem_write(ring+643,b"\x01");break
            block,i=divmod(next_sample,80);j=(block*80+i)%40
            x=(min(j,40-j)-10)*(400+(block%8)*20)
            m.mem_write(ring+2*((p&3)*80+o),struct.pack("<h",x))
            next_sample+=1
            o+=1
            if o==80:m.mem_write(ring+640,bytes([(p+1)&255,c,0,l]))
            else:m.mem_write(ring+642,bytes([o]))
    def hook(uc,addr,size,data):
        nonlocal now,capture_at,ring,keyed,active,inflight,completed,pre_release
        name=stubs[addr]
        r0,r1,r2,r3=[uc.reg_read(r) for r in (a.UC_ARM_REG_R0,a.UC_ARM_REG_R1,a.UC_ARM_REG_R2,a.UC_ARM_REG_R3)]
        r=0
        if name=="scratch":
            uc.mem_write(r0,struct.pack("<I",room));r=scratch
        elif name=="say":
            logs.append(bytes(uc.mem_read(r0,60)).split(b"\0",1)[0].decode())
        elif name in ("lcd_text","lcd_flush"):pass
        elif name=="fd_read":assert r0==0;r=0x4819 if fault=="chip" else 0x4829
        elif name=="resource_open":
            assert not keyed;r=len(deck)-24 if r0==14 else -1
        elif name=="resource_read":
            src=cb if r0==0 else deck[16:-8]
            assert r0 in (0,1) and r1+r3<=len(src)
            if keyed and fault=="spi":r=-1
            else:uc.mem_write(r2,src[r1:r1+r3])
        elif name=="ms":advance(1000);r=now//1000
        elif name=="ticks":r=now//100
        elif name=="delay_us":advance(r0)
        elif name=="key_down":r=int(fault=="cancel" and capture_at is not None and now-capture_at>150000)
        elif name=="tx_on":
            assert now>=75000 and not keyed and r0<=25000
            keyed=True;calls.append("key")
        elif name=="tx_off":
            keyed=False;active=False;inflight=None;calls.append("off")
        elif name=="mic_stream":
            if r0==3:
                r=int(now>=20000 and (capture_at is None or now<capture_at+1040000))
                if fault=="no_press":r=0
            elif r0==0:
                assert keyed and r2==644 and scratch<=r1<=end-r2
                ring=r1;capture_at=now;active=True
                if fault=="overrun":uc.mem_write(ring+643,b"\x01")
            elif r0==1:active=False
            else:raise AssertionError("unknown capture op")
        elif name=="fsk_async":
            assert keyed
            if r0==0:
                assert inflight is None and r2==33
                packet=bytes(uc.mem_read(r1,66));assert packet[-2:]==b"\x5a\xa5"
                frames.append(packet);inflight=now+152000
                if active:pre_release+=1
            elif r0==1:
                assert inflight is not None
                if fault=="tx":r=-1
                elif now>=inflight:inflight=None;r=1;completed+=1
            else:raise AssertionError("unexpected op")
        else:raise AssertionError("Unexpected API "+name)
        uc.reg_write(a.UC_ARM_REG_R0,r&0xffffffff)
    for addr in stubs:
        m.mem_write(addr,b"\x70\x47");m.hook_add(u.UC_HOOK_CODE,hook,begin=addr,end=addr)
    sp=0x2000f000;m.reg_write(a.UC_ARM_REG_R0,api);m.reg_write(a.UC_ARM_REG_SP,sp);m.reg_write(a.UC_ARM_REG_LR,stop|1)
    m.emu_start((base+entry)|1,stop,count=70_000_000)
    assert m.reg_read(a.UC_ARM_REG_PC)==stop
    assert m.reg_read(a.UC_ARM_REG_SP)==sp
    assert bytes(m.mem_read(end,16))==b"\xa5"*16
    assert not keyed and not active
    ret=m.reg_read(a.UC_ARM_REG_R0);ret=ret-(1<<32) if ret>>31 else ret
    if fault is None:
        assert ret==26 and completed==len(frames)==6 and pre_release>0
        plain=[bytes(x^y for x,y in zip(p[:64],pager.pn9(64))) for p in frames]
        assert all(pager.crc16_ccitt(p[:62])==int.from_bytes(p[62:],"big") for p in plain)
        assert plain[0][:3]==b"\xe2\x01\x01" and plain[-1][:3]==b"\xe2\x01\x02"
        assert b"".join(p[6:6+6*p[5]] for p in plain[1:-1])==bits
    elif fault in ("chip","no_press"):
        assert ret<0 and "key" not in calls and not frames
    else:
        assert ret<0 and "off" in calls
    return ret

@pytest.mark.parametrize("fault",[None,"chip","no_press","no_samples","overrun","spi","tx","cancel"])
def test_live_tx_module(txmod,artifacts,fault):run(txmod,artifacts,fault)
