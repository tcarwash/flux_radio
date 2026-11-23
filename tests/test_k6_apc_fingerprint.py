"""Guarded transmit APC fingerprint: only firmware-owned PA switching."""
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from modem import k6mod
from test_k6_i2s import run,need_clang,START

SRC=ROOT/"c/k6/mod/apc_fingerprint.c"


class APCReadModel:
    def __init__(self, *,on=0x40a2,off=0x0022):
        self.on=on
        self.off=off
        self.reads=0

    def read(self,reg,us):
        if reg==0x36:
            self.reads+=1
            return self.on if self.reads==1 else self.off
        return None

    def key(self,name,us):
        return 0

    def write(self,reg,value,us):
        raise AssertionError("APC fingerprint made an FD write")


@pytest.fixture
def enabled(tmp_path):
    p=tmp_path/"apc_on.c"
    p.write_text(
        "#define APC_FINGERPRINT_ENABLE 1\n"
        "#define APC_FIXTURE_ACK 1\n"
        "#define APC_WATCHDOG_ACK 1\n"
        f'#include "{SRC}"\n')
    return p


@need_clang
def test_default_inert_does_not_key():
    blob,entry=k6mod.build(str(SRC))
    assert entry==0 and len(blob)<512
    tx={}
    rc,said,after,w=run({**START,0:0x4829},src=str(SRC),level=3,
                         tx=tx,rx_hz=433500000)
    assert rc==-9
    assert not tx.get("log")
    assert not w


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(0,1),(1,0)])
def test_requires_two_compile_acks(tmp_path,fixture,watchdog):
    p=tmp_path/"apc_bad.c"
    p.write_text("#define APC_FINGERPRINT_ENABLE 1\n"
                 f"#define APC_FIXTURE_ACK {fixture}\n"
                 f"#define APC_WATCHDOG_ACK {watchdog}\n"
                 f'#include "{SRC}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_calibrated_target_readback_and_cleanup(enabled):
    model=APCReadModel(on=0x46a2,off=0x0022)
    tx={}
    blob,entry=k6mod.build(str(enabled))
    assert entry==0 and len(blob)<4096
    rc,said,after,w=run({**START,0:0x4829,0x30:0xbff1,0x67:120},
                         src=str(enabled),level=3,rx_hz=433500000,
                         tx=tx,fsk=model)
    assert rc==1,(rc,said)
    assert "APC on=46A2 off=0022" in said
    assert model.reads==2
    assert [v[0] for v in tx["log"]]==["tx_on","tx_pa","tx_off"]
    assert tx["log"][0][1]==650
    assert not tx.get("w")
    assert not w


@need_clang
@pytest.mark.parametrize("params,expect",[
    ({"rx_hz":144390000},-2),
    ({"regs":{0:0x4819}},-6),
    ({"regs":{0x67:400}},-7),
    ({"regs":{0x30:0xc1fa}},-2)
])
def test_no_transmission_if_ineligible(enabled,params,expect):
    regs={**START,0:0x4829,0x30:0xbff1,0x67:120}
    regs.update(params.get("regs",{}))
    tx={}
    rc,said,after,w=run(regs,src=str(enabled),level=3,
        rx_hz=params.get("rx_hz",433500000),tx=tx)
    assert rc==expect
    assert not tx.get("log")
    assert not w


@need_clang
def test_unexpected_pa_off_readback_aborts_and_unkeys(enabled):
    tx={}
    rc,said,after,w=run({**START,0:0x4829,0x30:0xbff1,0x67:120},
                         src=str(enabled),level=3,rx_hz=433500000,
                         tx=tx,fsk=APCReadModel(off=0x0080))
    assert rc==-13
    assert tx["log"][-1][0]=="tx_off"
    assert not w


def test_no_raw_apc_write():
    s=SRC.read_text()
    assert "a->fd_write" not in s
    assert "a->tx_on(650u)" in s
    assert "a->tx_off()" in s
