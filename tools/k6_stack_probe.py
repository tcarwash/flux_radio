"""Opt-in Cortex-M0 stack telemetry for compiled K6 emulator workloads.

Use as a pytest plugin: PYTHONPATH=tools python -m pytest -p k6_stack_probe
Set K6_STACK_ELF, K6_STACK_JSON and the normal simulator image variables.
The ELF must match the binary under test (checked against .text PT_LOAD).
Measures observed SP at block entries and stack writes, not a static proof
or physical interrupt latency qualification. No physical device access.
"""
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import pytest

def symbols(elf):
    out={}
    for line in subprocess.check_output(["llvm-nm","-S",str(elf)],text=True).splitlines():
        f=line.split()
        if len(f)==4:
            out[f[3]]=(int(f[0],16),int(f[1],16))
    def one(s):
        hits=[v for k,v in out.items() if k.endswith(s)]
        if len(hits)!=1:raise ValueError(f"ambiguous ELF symbol {s}: {hits}")
        return hits[0]
    arena,n=one("arena5ARENA")
    mod,m=one("MODULE_PAGE_BUFFER_STORAGE")
    return {"main":(one("__euninit")[0],one("_stack_start")[0]),
            "arena":(arena,arena+n),"module":(mod,mod+m)}

def check_image(elf, image):
    raw=elf.read_bytes()
    bin=image.read_bytes()
    # GNU/LLVM objcopy produces the exact resident flash payload.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p=Path(d)/"firmware.bin"
        subprocess.run(["llvm-objcopy","-O","binary",str(elf),str(p)],check=True)
        expected=p.read_bytes()
    if expected!=bin:
        raise ValueError("ELF does not match simulator image")

class StackProbe:
    def __init__(self,board,ranges):
        self.board=board
        self.ranges=ranges
        self.phase="boot"
        self.records={}
        self.invalid=[]
        u=board.uc
        U=board.ucmod
        a=board.arm
        self.reg_sp=a.UC_ARM_REG_SP
        self.reg_pc=a.UC_ARM_REG_PC
        # Sample after each existing emulation quantum instead of adding
        # a Python callback to every translated block or memory write.
        # Main stack also has the firmware's own 0xa5 high-water paint.
        original=u.emu_start
        def emu_start(*args,**kwargs):
            result=original(*args,**kwargs)
            self.sample(u,u.reg_read(self.reg_pc))
            return result
        u.emu_start=emu_start
    def sample(self,uc,pc,addr=None,width=0):
        sp=uc.reg_read(self.reg_sp)
        for name,(lo,hi) in self.ranges.items():
            if lo<=sp<=hi:
                low=sp
                # ARM PUSH memory-write hooks can run before SP is updated.
                if addr is not None and lo<=addr<sp and sp-addr<=128:
                    low=addr
                key=(self.phase,name)
                old=self.records.get(key)
                if old is None or low<old["low_sp"]:
                    self.records[key]={"phase":self.phase,"stack":name,
                        "low_sp":low,"pc":pc,"used_from_top":hi-low,
                        "reservation":hi-lo,"remaining":low-lo}
                return
        if len(self.invalid)<10:
            self.invalid.append({"phase":self.phase,"sp":hex(sp),"pc":hex(pc)})
    def block(self,uc,address,size,data):
        self.sample(uc,address)
    def write(self,uc,access,address,size,value,data):
        self.sample(uc,uc.reg_read(self.reg_pc),address,size)
    def report(self):
        lo,hi=self.ranges["main"]
        paint=bytes(self.board.uc.mem_read(lo,hi-lo))
        free=next((i for i,b in enumerate(paint) if b!=0xa5),len(paint))
        return {"ranges":self.ranges,"observed":list(self.records.values()),
                "main_contiguous_paint_bytes":free,
                "main_paint_high_water_bytes":hi-lo-free,
                "outside_ranges":self.invalid}

@pytest.fixture(autouse=True)
def measure_compiled_stack(request,monkeypatch):
    path=os.environ.get("K6_STACK_ELF")
    out=os.environ.get("K6_STACK_JSON")
    if not(path and out):yield;return
    from sim.k6emu import __main__ as sim
    elf=Path(path)
    ranges=symbols(elf)
    orig=sim.K6Board
    boards=[]
    class Measured(orig):
        def __init__(self,image,*args,**kwargs):
            check_image(elf,Path(image))
            super().__init__(image,*args,**kwargs)
            self.stack_probe=StackProbe(self,ranges)
            boards.append(self)
        def run(self,*args,**kwargs):
            result=super().run(*args,**kwargs)
            if self.stack_probe.phase=="boot":self.stack_probe.phase="workload"
            return result
    monkeypatch.setattr(sim,"K6Board",Measured)
    yield
    with Path(out).open("a") as f:
        for b in boards:
            record={"test":request.node.nodeid,"elf_sha256":hashlib.sha256(elf.read_bytes()).hexdigest(),
                    "fault":b.fault,**b.stack_probe.report()}
            f.write(json.dumps(record)+"\n")
