"""Compiled firmware async FSK qualification in the emulator ONLY."""
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink
from modem import k6mod
from k6_user_store_migrate import compact,BASE,END
SOURCE=r'''
#define K6_MODULE_LEVEL 28
#define K6_MODULE_OPT z
#include "k6_mod.h"
K6_MODULE_NAME("ASYNC TEST");
int k6_main(const k6_api_t*a){
 unsigned n,i,busy=0;int r;
 uint16_t *p=a->scratch(&n);
 if(a->level<28||n<66||!p)return -10;
 for(i=0;i<33;i++)p[i]=(uint16_t)(0x1234u+i);
 p[32]=0xa55a;
 if(a->tx_on(2000))return -11;
 if(a->fsk_async(0,p,33)){a->tx_off();return -12;}
 for(i=0;i<200;i++){
    r=a->fsk_async(1,0,0);
    if(r==1){a->tx_off();return busy?77:-13;}
    if(r<0){a->tx_off();return -14;}
    busy++;a->delay_us(5000);
 }
 a->fsk_async(2,0,0);return -15;
}
'''
@pytest.mark.parametrize("fault",["none","timeout","chip"])
def test_async_fsk_compiled(simulated_radio,monkeypatch,tmp_path,fault):
    image=os.getenv("K6_SIM_ASYNC_IMAGE")
    if not image:pytest.skip("set K6_SIM_ASYNC_IMAGE")
    root=Path(__file__).resolve().parents[1]
    src=tmp_path/"probe.c";src.write_text(SOURCE)
    code,entry=k6mod.build(str(src),cflags=[*k6mod.CFLAGS,"-I"+str(root/"c/k6")])
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    b=port.board
    b.fd_regs[0]=0x4829 if fault!="chip" else 0x4819
    # Minimal explicit completion model on the physical 3-wire bus.
    # No claim of actual RF timing or audio coexistence.
    old=b._fd_pin_change; reads=0;started=False;fifo=[];frames=[]
    def pins(prev,new):
        nonlocal reads,started
        n=len(b.fd_writes);before=b.fd_read_mode
        old(prev,new)
        if len(b.fd_writes)>n:
            reg,val=b.fd_writes[-1]
            if reg==0x02:b.fd_regs[0x0c]=0
            if reg==0x59 and val==0x80f8:fifo.clear()
            if reg==0x5f:fifo.append(val)
            if reg==0x59 and val==0x08f8:
                frames.append(tuple(fifo));started=True;reads=0
            if reg==0x59 and val==0x00f8:started=False
        if not before and b.fd_read_mode and b.fd_read_addr==0x0c and started:
            reads+=1
            if reads>=4 and fault=="none":b.fd_regs[0x0c]=1
    b._fd_pin_change=pins
    b.flash_data[BASE:END]=compact({24:k6mod.image(code,entry,123,None,level=28,lib=24)})[0]
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    link=LiveLink(port,timeout=3,initial_seq=0)
    assert link.module_capabilities()["module_api_max"] in (28,29)
    link.tune(433500000)
    r=link.run_module(24,allow_higher_api=True,timeout=30)
    print(fault,r)
    assert r["returned"] and r["result"]=={"none":77,"timeout":-14,"chip":-12}[fault],r
    assert len(frames)==(0 if fault=="chip" else 1)
    if frames:assert frames[0]==tuple([0x1234+i for i in range(32)]+[0xa55a])
    assert not link.status()["transmitting"]
    assert not b.fault
