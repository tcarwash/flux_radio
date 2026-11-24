"""Actual ARM catalog module against a strict FD6818B transaction model.

The model verifies FIFO words, completion gating, timeout, cancel and
cleanup. It is NOT a physical radio, RF or watchdog timing measurement.
"""
import re
import struct
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
from tests.test_k6_sotto_module import artifacts, wire

MOD=Path(__file__).resolve().parents[1]/"c/k6/mod"
FLAGS=["-DSOTTO_RF_BENCH=1","-DSOTTO_RF_FIXTURE_ACK=1","-DSOTTO_RF_WATCHDOG_ACK=1"]

@pytest.fixture(scope="module")
def modules():
    src=str(MOD/"sotto_tx_fixture.c")
    inert=k6mod.build(src)
    armed=k6mod.build(src,cflags=[*k6mod.CFLAGS,*FLAGS])
    assert k6mod.module_name(armed[0])=="SOTTO RFTEST"
    assert len(armed[0])<3000 and armed[1]==16
    return inert,armed

def test_requires_explicit_bench_flags():
    with pytest.raises(subprocess.CalledProcessError):
        k6mod.build(str(MOD/"sotto_tx_fixture.c"),
                    cflags=[*k6mod.CFLAGS,"-DSOTTO_RF_BENCH=1"])

def fixture_packets(bits):
    def ctrl(kind,last,n,segments):
        h=bytearray(62);h[:4]=bytes([0xe2,1,kind,0])
        h[4:12]=b"SOTTOTES";h[12:16]=bytes([112,last,n,segments])
        h[16:20]=b"FDV1";return wire(h)
    out=[ctrl(1,0,0,0)]
    for i in range(4):
        part=bits[i*48:(i+1)*48];h=bytearray(62)
        h[:6]=bytes([0xa9,112+i,0xc0,0,0,len(part)//6])
        h[6:6+len(part)]=part;out.append(wire(h))
    out.append(ctrl(2,115,2,4))
    return out

class Board:
    def __init__(self,module, fault=None, base=0x20001110):
        u=pytest.importorskip("unicorn")
        from unicorn import arm_const as a
        self.u,self.a=u,a
        self.mu=m=u.Uc(u.UC_ARCH_ARM,u.UC_MODE_THUMB|u.UC_MODE_MCLASS)
        m.ctl_set_cpu_model(a.UC_CPU_ARM_CORTEX_M0)
        m.mem_map(0x20000000,0x6000)
        m.mem_map(0x08000000,0x1000)
        m.mem_map(0x48000000,0x1000)
        self.base=base;self.code,self.entry=module
        m.mem_write(base,self.code)
        self.stop=0x08000000;self.api=0x20004500
        self.scratch=(base+len(self.code)+11)&~3
        names=["say","ticks","fd_read","fd_write","delay_us","tune","rx_hz","ms",
               "key_down","scratch","tx_on","tx_off","tx_pa","key_poll"]
        self.stubs={0x08000100+4*i:n for i,n in enumerate(names)}
        self.stubs[0x08000300]="forbidden"
        ptrs=[0x08000301]*32
        for i in range(len(names)):ptrs[i]=0x08000101+4*i
        m.mem_write(self.api,struct.pack("<HH",1,5)+struct.pack("<32I",*ptrs))
        for addr in self.stubs:
            m.mem_write(addr,b"\x70\x47")
            m.hook_add(u.UC_HOOK_CODE,self.hook,begin=addr,end=addr)
        self.fault=fault;self.keyed=False;self.pa=False
        self.tx_on_calls=0;self.tx_off_calls=0;self.ms=0;self.done=0
        self.fifo=[];self.frames=[];self.pa_on_calls=0;self.log=[]
        self.regs={0:0x4829,0x30:0,0x3f:0x0020,0x59:0x0020,
                   0x58:0x42,0x70:0x2c,0x72:0x36,
                   0x5a:0x12,0x5b:0x34,0x5c:0x56,0x5d:0x0f00,0x67:0}
        self.saved=dict(self.regs)
        self.active_at=None;self.started=0;self.restore_attempts=[]
    def hook(self,uc,addr,size,data):
        a=self.a;n=self.stubs[addr]
        v=uc.reg_read(a.UC_ARM_REG_R0);w=uc.reg_read(a.UC_ARM_REG_R1)
        out=0
        if n=="say":
            self.log.append(bytes(uc.mem_read(v,60)).split(b"\0")[0].decode())
        elif n=="scratch":
            uc.mem_write(v,struct.pack("<I",0 if self.fault=="scratch" else 4096))
            out=self.scratch
        elif n=="rx_hz":out=144390000 if self.fault=="freq" else 433500000
        elif n=="key_down":
            out=int(self.fault=="cancel" and self.started>=2)
        elif n=="ms":out=self.ms
        elif n=="delay_us":
            self.ms+=max(1,v//1000)
            if self.active_at is not None and self.ms-self.active_at>=110:
                if self.fault!="timeout" or self.started!=2:
                    self.done=1;self.active_at=None
        elif n=="tx_on":
            assert not self.keyed and 0<v<=3900
            self.tx_on_calls+=1
            if self.fault=="tx_refused":out=-1
            else:self.keyed=True;self.pa=True;self.regs[0x30]=0xc1fa
        elif n=="tx_pa":
            assert self.keyed
            self.pa=bool(v)
            self.pa_on_calls+=bool(v)
        elif n=="tx_off":
            assert self.keyed
            self.tx_off_calls+=1;self.keyed=False;self.pa=False
            self.regs[0x30]=0
        elif n=="fd_read":
            if v==0x0c:
                out=1 if self.fault=="stale" else self.done
            elif v==0 and self.fault=="chip":out=0x4819
            elif v==0x67 and self.fault=="busy":out=0xffff
            elif v==0x30 and self.fault=="drop" and self.started>=2:
                out=0
            else:out=self.regs.get(v,0)
        elif n=="fd_write":
            assert self.keyed,"write outside guarded TX lease"
            assert v not in (0x30,0x33,0x36,0x37)
            if not self.pa and self.started:
                self.restore_attempts.append(v)
            if v==0x5f:
                if self.fault=="write" and self.started==1 and len(self.fifo)==8:
                    out=-1
                else:self.fifo.append(w)
            elif v==0x59 and w==0x80f8:
                self.fifo=[];self.active_at=None
            elif v==0x02:self.done=0
            elif v==0x59 and w==0x08f8:
                assert self.pa and len(self.fifo)==33 and self.fifo[-1]==0xa55a
                assert self.regs[0x5d]==0x4100 and self.done==0
                self.frames.append(struct.pack("<33H",*self.fifo))
                self.started+=1;self.active_at=self.ms
            if self.fault=="restore" and not self.pa and self.started and v==0x72:
                out=-1
            if not out:self.regs[v]=w
        else:raise AssertionError(f"Unexpected call {n}")
        uc.reg_write(a.UC_ARM_REG_R0,out&0xffffffff)
    def run(self):
        a=self.a;m=self.mu
        m.reg_write(a.UC_ARM_REG_R0,self.api)
        m.reg_write(a.UC_ARM_REG_SP,0x20005f00)
        m.reg_write(a.UC_ARM_REG_LR,self.stop|1)
        m.emu_start((self.base+self.entry)|1,self.stop,count=2_000_000)
        assert m.reg_read(a.UC_ARM_REG_PC)==self.stop
        r=m.reg_read(a.UC_ARM_REG_R0)
        return r-(1<<32) if r&0x80000000 else r

def test_inert_default(modules):
    b=Board(modules[0]);assert b.run()==-9
    assert b.tx_on_calls==0 and not b.frames

def test_exact_real_arm_packets(modules,artifacts):
    *_,bits=artifacts
    # Checked-in public fixture must still match unmodified upstream encoder.
    src=(MOD/"sotto_tx_fixture.inc").read_text()
    assert bytes(int(x,16) for x in re.findall(r"0x([0-9a-f]{2})",src))==bits
    b=Board(modules[1]);assert b.run()==6
    assert b.frames==fixture_packets(bits)
    assert b.tx_on_calls==b.tx_off_calls==b.pa_on_calls==1
    assert not b.keyed and not b.pa
    assert all(b.regs[r]==v for r,v in b.saved.items())
    print(f"module={len(modules[1][0])} B; exact RF FIFO model frames={len(b.frames)}")

@pytest.mark.parametrize("fault,rc,frames",[
    ("scratch",-8,0),("freq",-2,0),("chip",-2,0),("busy",-7,0),
    ("tx_refused",-5,0),("stale",-14,0),("cancel",-4,2),
    ("timeout",-14,2),("drop",-5,2),("write",-13,1),("restore",-15,6),
])
def test_failure_cleanup(modules,fault,rc,frames):
    b=Board(modules[1],fault);assert b.run()==rc
    assert len(b.frames)==frames and not b.keyed and not b.pa
    assert b.tx_on_calls==b.tx_off_calls+(fault=="tx_refused")
    if b.tx_off_calls:
        for r,v in b.saved.items():
            if fault!="restore" or r!=0x72:assert b.regs[r]==v
    if fault=="restore":assert 0x3f in b.restore_attempts
