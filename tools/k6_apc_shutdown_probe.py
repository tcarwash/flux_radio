"""Offline-only simulator comparison of PA15 shutdown SPI writes for two firmware images."""
from pathlib import Path
import contextlib
import io
import os
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[0:0]=[str(ROOT/"tests"),str(ROOT/"tools"),str(ROOT)]
from sim.k6emu.__main__ import K6Board
from test_k6_capability_sim import SimulatedSerial
from k6_user_store_migrate import compact,BASE,END
from modem import k6mod
import k6_live,k6_install

SLOT=26
TEST_MODULE=k6mod.image(b"\x2a\x20\x70\x47",0,123,None,level=25,lib=SLOT)
IMAGES={
  "installed":Path("/home/tyler/flux-bench/tx-injection-20261122/superset-remoteui/flux-tx-superset-remoteui.bin"),
  "candidate":Path("/home/tyler/flux-bench/tx-injection-20261122/apc-lab/build-isolated/flux-apc-b-lab.bin"),
}

class TextAndBinarySink(io.TextIOBase):
    def __init__(self):self.buffer=io.BytesIO()
    def write(self,s):return len(s)

def run_one(name,image):
    with contextlib.redirect_stdout(TextAndBinarySink()),contextlib.redirect_stderr(io.StringIO()):
        board=K6Board(image,collect_mmio=False)
        board.flash_data[0xb000:0xb004]=b"AURA"
        port=SimulatedSerial(board)
        port.advance(70_000_000)
        board.flash_data[BASE:END]=compact({SLOT:TEST_MODULE})[0]
        k6_install.time=SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep)
        k6_live.time=SimpleNamespace(monotonic=port.monotonic,sleep=port.sleep)
        link=k6_live.LiveLink(port,timeout=3,initial_seq=0)
        s0=link.status()
        before=bytes(board.flash_data)
        port.board.set_gpio("A",15,1)
        port.advance(600_000)
        s1=link.status()
        after=bytes(board.flash_data)
    changed=[i for i,(a,b) in enumerate(zip(before,after)) if a!=b]
    ranges=[]
    for i in changed:
        if ranges and i==ranges[-1][-1]+1:
            ranges[-1][1]=i
        else:ranges.append([i,i])
    print(name, "before",str(s0), "after",str(s1),
        "fault",str(board.fault),"changed_count",len(changed),
        "ranges",[(hex(a),hex(b)) for a,b in ranges[:40]])
    for a,b in ranges[:14]:
        print(" ",hex(a),before[a:b+1].hex(),"=>",after[a:b+1].hex())
    return changed,before,after

if __name__=="__main__":
    for name,img in IMAGES.items():
        run_one(name,img)
