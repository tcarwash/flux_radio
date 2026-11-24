"""API29 compiled host capture lifecycle. The emulator explicitly schedules
TIM17 handlers and models SRAM alias coherency; no real timing/SNR claim."""
import os,struct,subprocess
from pathlib import Path
import pytest
from test_k6_capability_sim import simulated_radio
from modem import k6mod
from k6_user_store_migrate import compact,BASE,END
from sim.k6emu.__main__ import TIM17, SYSCFG_CFGR1, RAM_BASE, FLASH_BASE
SRC=r'''
#define K6_MODULE_LEVEL 29
#define K6_MODULE_OPT z
#include "k6_mod.h"
K6_MODULE_NAME("PCM TEST");
int k6_main(const k6_api_t*a){
 unsigned room,i;uint8_t *p=a->scratch(&room);
 if(room<644||a->level<29)return -10;
 for(i=0;i<644;i++)p[i]=0;
 if(a->tx_on(1000))return -11;
 if(a->mic_stream(0,p,644)){a->tx_off();return -12;}
 for(i=0;i<220;i++)a->delay_us(100);
 a->mic_stream(1,0,0);
 i=p[640];a->tx_off();
 return i>=2&&i<=3&&!p[643]?77:-13;
}
'''
@pytest.mark.parametrize("busy",[False,True])
def test_capture_host(simulated_radio,tmp_path,busy):
    image=os.getenv("K6_SIM_ASYNC_IMAGE");elf=os.getenv("K6_SIM_ASYNC_ELF")
    if not image or not elf:pytest.skip("set API29 image and ELF")
    src=tmp_path/"pcmtest.c";src.write_text(SRC)
    root=Path(__file__).resolve().parents[1]
    code,entry=k6mod.build(str(src),cflags=[*k6mod.CFLAGS,"-I"+str(root/"c/k6")])
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    b=port.board;u=b.ucmod;a=b.arm
    sym={}
    for row in subprocess.check_output(["llvm-nm","-S","-C",elf],text=True).splitlines():
        parts=row.split(None,3)
        if len(parts)==4:sym[parts[3]]=int(parts[0],16)
    b.fd_regs[0]=0x4829
    b.flash_data[BASE:END]=compact({24:k6mod.image(code,entry,124,None,level=29,lib=24)})[0]
    # Preserve the real normal module dispatch, but bypass menu navigation.
    # PTT is a physical GPIO in the simulation, not a remote CAT launch.
    pending=sym["aura::app::pager::PENDING_LIB_SLOT"]
    b.set_gpio("A",10,0)
    b.uc.mem_write(pending,b"\x18")
    v=RAM_BASE+4*38
    oldvec=bytes(b.uc.mem_read(v,4))
    def alias(uc,access,addr,size,value,data):
        if b.regs.get(SYSCFG_CFGR1,0)&3==3:uc.mem_write(addr-RAM_BASE,value.to_bytes(size,"little"))
    b.uc.hook_add(u.UC_HOOK_MEM_WRITE,alias,begin=v,end=v+3)
    if busy:b.regs[0x40021018]=b.regs.get(0x40021018,0)|(1<<18)
    ga=b.regs.get(0x48000000,0)&(3<<22)
    old=b._service_uart_irq;next_due=None;count=0
    def irq():
        nonlocal next_due,count
        old()
        if not b.regs.get(TIM17,0)&1 or not b.nvic_enabled&(1<<22):
            next_due=None;return
        if next_due is None:next_due=b.cycles+12000
        if b.cycles<next_due or b.uc.reg_read(a.UC_ARM_REG_PRIMASK)&1:return
        next_due+=12000
        assert b.regs.get(SYSCFG_CFGR1,0)&3==3
        handler=int.from_bytes(b.uc.mem_read(v,4),"little")
        assert handler==(sym["aura::hal::pcm_capture::isr"]|1)
        regs=[getattr(a,"UC_ARM_REG_R"+str(i)) for i in range(13)]+[a.UC_ARM_REG_SP,a.UC_ARM_REG_LR,a.UC_ARM_REG_PC,a.UC_ARM_REG_XPSR,a.UC_ARM_REG_PRIMASK]
        saved=[(r,b.uc.reg_read(r)) for r in regs]
        b.regs[TIM17+0x10]=1
        b.uc.reg_write(a.UC_ARM_REG_LR,RAM_BASE|1)
        b.uc.emu_start(handler,RAM_BASE,count=15000)
        assert b.uc.reg_read(a.UC_ARM_REG_PC)==RAM_BASE
        for r,value in saved:b.uc.reg_write(r,value)
        count+=1
    b._service_uart_irq=irq
    for _ in range(200):
        b.run(200000,quantum=32)
        assert not b.fault,b.fault
        if b"K6 MOD ret=" in bytes(b.uart_tx):break
    else:pytest.fail("module did not return")
    b.set_gpio("A",10,1)
    b.run(200000)
    log=bytes(b.uart_tx)
    print(log[-500:],count)
    assert b"K6 MOD ret="+(b"-12" if busy else b"77")+b" " in log
    assert (count==0 if busy else count>=160)
    assert b.uc.mem_read(v,4)==oldvec
    assert b.nvic_enabled&(1<<22)==0
    assert not b.regs.get(TIM17,0)&1
    assert b.regs.get(0x48000000,0)&(3<<22)==ga
    assert b.regs.get(SYSCFG_CFGR1,0)&3!=3
    assert b.fd_regs.get(0x36,0)&0x80==0

def test_live_tx_full_host(simulated_radio,tmp_path):
    """Real installed encoder + host resource reads during guarded TX.
    Inject timed synthetic PCM into the actual host ring (the ISR is tested
    separately above) and model completion on the RFIC bus. Not RF evidence."""
    from tools.k6_sotto_module import build
    from tests.test_k6_sotto_module import artifacts as _artifacts
    from tools.k6_sotto_ivf_deck import build_deck,SLIM_CELLS
    from modem import pager
    import ctypes,zlib
    from tools.k6_sotto_module import FIXED
    image=os.getenv("K6_SIM_ASYNC_IMAGE");elf=os.getenv("K6_SIM_ASYNC_ELF")
    if not image or not elf:pytest.skip("set API29 image and ELF")
    code,entry=build(host_resources=True,live_tx=True)
    so=tmp_path/"tables.so"
    subprocess.run(["cc","-shared","-fPIC",str(FIXED/"fx_tables.c"),"-o",str(so)],check=True)
    c=ctypes.CDLL(str(so))
    table=b"".join(bytes((ctypes.c_char*n).in_dll(c,f"fx_cb{i}")) for i,n in enumerate((10240,10240,5120,5120)))
    h=bytearray(32);struct.pack_into("<4sHHI",h,0,b"K6CB",1,20,len(table))
    h[12:17]=bytes([4,9,9,8,8]);struct.pack_into("<I",h,24,zlib.crc32(table));struct.pack_into("<I",h,28,zlib.crc32(h[:28]))
    deck=k6mod.image(build_deck(SLIM_CELLS.read_text()),0,1,None,level=5,lib=14,cards=True)
    port=simulated_radio(Path(image).read_bytes(),cable_only=True);b=port.board;u=b.ucmod
    b.fd_regs[0]=0x4829
    b.flash_data[0x100000:0x100000+len(h)+len(table)]=bytes(h)+table
    env=k6mod.image(code,entry,123,None,level=29,lib=24)
    b.flash_data[BASE:END]=compact({14:deck,24:env})[0]
    before=bytes(b.flash_data)
    sym={}
    for row in subprocess.check_output(["llvm-nm","-S","-C",elf],text=True).splitlines():
        parts=row.split(None,3)
        if len(parts)==4:sym[parts[3]]=int(parts[0],16)
    v=RAM_BASE+4*38
    oldvec=bytes(b.uc.mem_read(v,4))
    def alias(uc,access,addr,size,value,data):
        if b.regs.get(SYSCFG_CFGR1,0)&3==3:uc.mem_write(addr-RAM_BASE,value.to_bytes(size,"little"))
    b.uc.hook_add(u.UC_HOOK_MEM_WRITE,alias,begin=v,end=v+3)
    b.set_gpio("A",10,1);b.uc.mem_write(sym["aura::app::pager::PENDING_LIB_SLOT"],b"\x18")
    # Keep PTT released through preflight; then emulate the operator.
    first=None;capture=None;released=False;samples=0
    frames=[];fifo=[];rf_deadline=None;sent_held=0
    oldpins=b._fd_pin_change
    def pins(prev,new):
        nonlocal rf_deadline,sent_held
        n=len(b.fd_writes);before_read=b.fd_read_mode
        oldpins(prev,new)
        if len(b.fd_writes)>n:
            reg,val=b.fd_writes[-1]
            if reg==0x02:b.fd_regs[0x0c]=0
            if reg==0x59 and val==0x80f8:fifo.clear()
            if reg==0x5f:fifo.append(val)
            if reg==0x59 and val==0x08f8:
                assert len(fifo)==33 and fifo[-1]==0xa55a
                frames.append(struct.pack("<33H",*fifo))
                sent_held+=not released
                rf_deadline=b.cycles+152*96000
            if reg==0x59 and val==0x00f8:rf_deadline=None
        if not before_read and b.fd_read_mode and b.fd_read_addr==0x0c and rf_deadline is not None and b.cycles>=rf_deadline:
            b.fd_regs[0x0c]=1
    b._fd_pin_change=pins
    oldirq=b._service_uart_irq
    def irq():
        nonlocal first,capture,released,samples
        oldirq()
        if first is None and b"Unplug cable; hold PTT" in bytes(b.uart_tx):first=b.cycles
        if first is not None and capture is None and b.cycles-first>=20*96000:b.set_gpio("A",10,0)
        ring=int.from_bytes(b.uc.mem_read(sym["aura::hal::pcm_capture::RING"],4),"little")
        if ring and capture is None:capture=b.cycles
        if ring and capture is not None:
            # Inject one 10ms block whenever the prior block was consumed,
            # with a minimum 10ms model cadence. This intentionally models
            # the producer interface, not real-time scheduling/CPU margin.
            p,c,o,l=bytes(b.uc.mem_read(ring+640,4))
            if samples<104 and p==c and b.cycles-capture>=(samples+1)*10*96000:
                pcm=[]
                for i in range(80):
                    j=(samples*80+i)%40;pcm.append((min(j,40-j)-10)*(400+(samples%8)*20))
                b.uc.mem_write(ring+160*(p&3),struct.pack("<80h",*pcm))
                b.uc.mem_write(ring+640,bytes([(p+1)&255,c,0,l]))
                samples+=1
            elif samples==104 and p==c and not released:
                b.set_gpio("A",10,1);released=True
    b._service_uart_irq=irq
    for _ in range(700):
        b.run(200000)
        assert not b.fault,b.fault
        if b"K6 MOD ret=" in bytes(b.uart_tx):break
    else:pytest.fail("integrated module did not return")
    log=bytes(b.uart_tx)
    print(log[-600:],len(frames),samples,sent_held)
    assert b"K6 MOD ret=26 " in log and sent_held>0 and len(frames)==6 and samples==104
    plain=[bytes(x^y for x,y in zip(p[:64],pager.pn9(64))) for p in frames]
    assert all(pager.crc16_ccitt(p[:62])==int.from_bytes(p[62:],"big") for p in plain)
    assert plain[0][:3]==b"\xe2\x01\x01" and plain[-1][:3]==b"\xe2\x01\x02"
    assert b.uc.mem_read(v,4)==oldvec and b.nvic_enabled&(1<<22)==0
    assert b.fd_regs.get(0x36,0)&0x80==0
    assert bytes(b.flash_data)==before
