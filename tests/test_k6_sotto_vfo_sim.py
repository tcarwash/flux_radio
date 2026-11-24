"""Compiled firmware VFO selector runs installed level-29 Sotto through PTT.

Use a non-radiating stub with the exact module name. Independent from the
full encoder/TIM17/FSK emulator checks; no physical TX is modeled here.
"""
import os
from pathlib import Path
import pytest
from modem import k6mod
from k6_user_store_migrate import compact,BASE,END

ROOT=Path(__file__).resolve().parents[1]
STUB=r'''
#define K6_MODULE_LEVEL 29
#define K6_MODULE_OPT z
#include "k6_mod.h"
K6_MODULE_NAME("SOTTO TX");
int k6_main(const k6_api_t *a){
    if(a->level<29)return -22;
    a->say("SOTTO VFO PTT");
    return 77;
}
'''

def test_sotto_vfo_physical_ptt(tmp_path):
    image=os.getenv("K6_SIM_ASYNC_IMAGE")
    if not image:pytest.skip("set matching K6_SOTTO_VFO=1 firmware")
    from sim.k6emu.__main__ import K6Board
    src=tmp_path/"vfo.c";src.write_text(STUB)
    code,entry=k6mod.build(str(src),cflags=[*k6mod.CFLAGS,"-I"+str(ROOT/"c/k6")])
    env=k6mod.image(code,entry,123,None,level=29,lib=24)
    b=K6Board(Path(image),collect_mmio=False)
    b.flash_data[BASE:END]=compact({24:env})[0]
    b.flash_data[0xB000:0xB004]=b"AURA"
    b.fd_regs[0]=0x4829
    b.set_gpio("A",10,1)
    def advance(n):
        b.run(n)
        assert not b.fault,b.fault
    def hold(key,ms):
        b.press_key(key)
        until=b.cycles+ms*96000
        while b.cycles<until:advance(2_000_000)
        b.press_key(key,False)
        advance(4_000_000)
    advance(70_000_000)
    cycles = 0 if os.environ.get("K6_VFO_INITIAL_SELECTED") == "1" else 5
    for i in range(cycles):
        hold("SIDE2",1200)
        assert b"SOTTO VFO PTT" not in bytes(b.uart_tx)
        print("MODE cycle",i+1,flush=True)
    # PTT must route to the installed mode, not ordinary FM transmit.
    b.set_gpio("A",10,0)
    for i in range(50):
        advance(2_000_000)
        if b"SOTTO VFO PTT" in bytes(b.uart_tx):break
    else: pytest.fail("Physical PTT did not launch installed Sotto mode: "+repr(bytes(b.uart_tx)[-850:]))
    b.set_gpio("A",10,1)
    advance(4_000_000)
    log=bytes(b.uart_tx)
    print(log[-400:])
    assert b"K6 MOD ret=77 " in log
    assert b"K6 LIB load slot=24" in log
    assert b"SOTTO VFO PTT" in log
    # Stub never requests a TX lease: a mode launch alone must be RF-silent.
    assert not any(r==0x59 and v==0x08f8 for r,v in b.fd_writes)
