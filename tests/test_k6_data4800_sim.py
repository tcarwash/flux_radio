"""Opt-in real Cortex-M0 firmware test of the bounded Data FSK transmitter.

Only the RFIC FIFO IRQ is modeled. This is NOT physical RF evidence.
The temporary test module is generated inside pytest; no catalog entry.
"""
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_k6_capability_sim import simulated_radio
import k6_live
from k6_live import LiveLink
from k6_user_store_migrate import compact, objects, BASE, END
from modem import k6mod

@pytest.mark.parametrize("mode",["complete","timeout","stale","bad-chip","session"])
def test_data4800_compiled_firmware(simulated_radio,monkeypatch,tmp_path,mode):
    image=os.environ.get("K6_SIM_DATA4800_IMAGE")
    if not image:pytest.skip("set K6_SIM_DATA4800_IMAGE to compiled Data 4800 image")
    source=tmp_path/"fsk4800.c"
    header=Path(__file__).resolve().parents[1]/"c/k6/k6_mod.h"
    source.write_text(f'''
#define K6_MODULE_LEVEL 24
#include "{header}"
K6_MODULE_NAME("FSK SIM");
int k6_main(const k6_api_t *a){{
    uint32_t n=0;uint16_t *w=(uint16_t*)a->scratch(&n);
    if(!w||n<66)return -9;
    for(unsigned i=0;i<33;i++)w[i]=(uint16_t)(0x1234u+i);
    if(a->tx_on(3900))return -8;
    if(a->tx_pa(0)){{a->tx_off();return -7;}}
    int rc=a->fsk_configured(2,w,33,4800u|(21u<<16));
    a->tx_off();
    return rc;
}}
''')
    if mode=="session":
        mod=header.parent/"mod"
        source.write_text(f'''
#define K6_MODULE_LEVEL 24
#define SOTTO_STAGE_MAX_FRAMES 26u
#include "{header}"
#include "{mod/'sotto_data_tx.h'}"
#include "{mod/'sotto_tx_fixture.inc'}"
K6_MODULE_NAME("SOTTO SIM");
int k6_main(const k6_api_t *a){{
    uint32_t n=0;sotto_stage_t *s=(sotto_stage_t*)a->scratch(&n);
    if(!s||n<sizeof(*s))return -8;
    if(sotto_stage_begin(s,112,0x534f5454u,0x4f544553u))return -12;
    for(unsigned i=0;i<26;i++)if(sotto_stage_record(s,fixture+6*i))return -12;
    if(sotto_stage_seal(s,0,1))return -12;
    return sotto_data_send(a,s);
}}
''')
    code,entry=k6mod.build(str(source))
    port=simulated_radio(Path(image).read_bytes(),cable_only=True)
    monkeypatch.setattr(k6_live,"time",SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep))
    port.baudrate=115200
    l=LiveLink(port,timeout=3,initial_seq=0)
    assert "lib_mutate" in l.capabilities()["features"]
    assert l.module_capabilities()["module_api_max"]>=24
    port.board.flash_data[BASE:END]=compact({})[0]
    envelope=k6mod.image(code,entry,l.serial_watermark()+1,None,level=24,lib=30)
    assert l.mutate_library(envelope,baseline=True)["verified"]
    assert objects(port.board.flash_data[BASE:END])=={30:envelope}
    s=l.tune(433500000)
    assert not s["transmitting"]
    board=port.board
    class RFRegs(dict):
        def __init__(self,original):
            super().__init__(original)
            self.fifo=[]
            self.frames=[]
            self.armed=False
        def __setitem__(self,key,value):
            super().__setitem__(key,value)
            if key==0x02 and value==0:
                super().__setitem__(0x0c,0)
            if key==0x59 and value==0x80f8:
                self.fifo=[]
                self.armed=True
            if key==0x5f and self.armed:
                self.fifo.append(value)
                if mode=="stale" and len(self.fifo)==33:
                    super().__setitem__(0x0c,1)
            if key==0x59 and value==0x08f8 and self.armed:
                self.armed=False
                self.frames.append(tuple(self.fifo))
                if mode in ("complete","session"):super().__setitem__(0x0c,1)
    board.fd_regs=RFRegs(board.fd_regs)
    board.fd_regs[0]=0x4819 if mode=="bad-chip" else 0x4829
    start=len(board.fd_writes)
    r=l.run_module(30,timeout=8,allow_higher_api=True)
    assert r["returned"],r
    assert not l.status()["transmitting"]
    writes=board.fd_writes[start:]
    if mode=="session":
        import struct
        from tools.k6_sotto_rf_bench import fixture
        _,expected=fixture()
        assert r["result"]==6,(r,writes[-80:])
        assert [struct.pack("<33H",*w) for w in board.fd_regs.frames]==[p+bytes.fromhex("5aa5") for p in expected]
    else:
        if mode in ("complete","timeout"):
            assert board.fd_regs.frames==[tuple(0x1234+i for i in range(33))]
            assert (0x72,0xc194) in writes
        else:
            assert not board.fd_regs.frames
        assert r["result"]==(0 if mode=="complete" else -1 if mode=="bad-chip" else -2), (r,writes[-80:])
    delete=k6mod.image(b"K6ERASE",0,l.serial_watermark()+1,None,level=1,lib=30,remove=True)
    assert l.mutate_library(delete)["removed"]
    assert not objects(port.board.flash_data[BASE:END])
