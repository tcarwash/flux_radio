"""Source contract and M0 model for B-chip native-FSK status sampling."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from k6_fsk_status_rf_bench import EXPECTED_CODE_SHA256,SOURCE as RF_SOURCE
from test_k6_i2s import need_clang
from test_k6_fsklab import run_m0,saved_regs

SRC=ROOT/"c/k6/mod/fsk_status_probe.c"

class StatusFskModel:
    def __init__(self):
        self.fifo=[];self.frames=[];self.started=None;self.length=0;self.reads=[]
    def key(self,name,us): return 0
    def write(self,reg,value,us):
        if reg==0x59 and value==0x80f8:
            self.fifo=[]
        elif reg==0x5d:
            self.length=((value>>8)+1)//2
        elif reg==0x5f:
            self.fifo.append(value)
        elif reg==0x59 and value==0x08f8:
            self.started=us
            self.frames.append((self.length,tuple(self.fifo),us))
    def read(self,reg,us):
        self.reads.append((reg,us))
        if reg==0x0c and self.started is not None:
            return int(us-self.started >= self.length*16/4800*1e6+20000)
        return None

@pytest.fixture
def enabled(tmp_path):
    p=tmp_path/"fsk-status-enabled.c"
    p.write_text("#define FSK_STATUS_ENABLE 1\n"
                 "#define FSK_STATUS_FIXTURE_ACK 1\n"
                 "#define FSK_STATUS_WATCHDOG_ACK 1\n"
                 f'#include "{SRC}"\n')
    return p

@need_clang
def test_default_is_inert_and_enabled_module_compiles():
    code,entry=k6mod.build(str(SRC))
    assert entry==0 and len(code)<512
    rf_code,rf_entry=k6mod.build(str(RF_SOURCE))
    import hashlib
    assert rf_entry==0 and hashlib.sha256(rf_code).hexdigest()==EXPECTED_CODE_SHA256

@need_clang
def test_three_bounded_frames_sample_status_without_fifo_reads(enabled):
    code,entry=k6mod.build(str(enabled))
    assert entry==0 and len(code)<4096
    model=StatusFskModel();tx={};baseline=saved_regs()
    ret,lines,after,w=run_m0(enabled,tx=tx,fsk=model,registers=baseline)
    assert ret==96,(ret,lines)
    assert [n for n,_,_ in model.frames]==[16,32,48]
    assert [len(words) for _,words,_ in model.frames]==[16,32,48]
    assert len([x for x in model.reads if x[0]==0x5f])==0
    assert len([x for x in model.reads if x[0] in (0x02,0x0c,0x3f,0x5e,0x30)])>=18
    assert not any(r==0x5e for r,_ in w)
    assert not any(r==0x02 and v!=0 for r,v in w)
    assert [x[0] for x in tx["log"]].count("tx_on")==1
    assert [x[0] for x in tx["log"]].count("tx_off")==1
    assert tx["log"][-1][0]=="tx_off"
    for reg in (0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d):
        assert after.get(reg,0)==baseline.get(reg,0),hex(reg)

@need_clang
def test_status_probe_compile_gates_require_both_acknowledgements(tmp_path):
    for fixture,watchdog in ((0,0),(1,0),(0,1)):
        p=tmp_path/f"no-ack-{fixture}-{watchdog}.c"
        p.write_text("#define FSK_STATUS_ENABLE 1\n"
                     f"#define FSK_STATUS_FIXTURE_ACK {fixture}\n"
                     f"#define FSK_STATUS_WATCHDOG_ACK {watchdog}\n"
                     f'#include "{SRC}"\n')
        with pytest.raises(Exception): k6mod.build(str(p))

def test_probe_never_reads_or_writes_fifo_level():
    source=SRC.read_text()
    assert "fd_read(R_FIFO)" not in source
    assert "wr(a,R_LEVEL" not in source
    assert "fd_read(R_IRQ)" in source
    assert "fd_write(0x5fu" not in source
