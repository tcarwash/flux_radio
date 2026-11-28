"""Compiled Cortex-M0 read-only TX27B idle baseline, including nonzero REG51.

No device writes, transmitter, serial connection or hardware mutation.
"""
from __future__ import annotations
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'tools')]
from modem import k6mod
from test_k6_i2s import START,run,need_clang
SRC=ROOT/'c/k6/mod/tx27b_reg51_idle_diag.c'


def registers(value):
    return {**START,0x00:0x4829,0x30:0xBFF1,0x51:value,
            0x08:0x87FF,0x52:0x0292,0x70:0x0000,0x71:0x1234}


@need_clang
def test_compiled_thumb_rx_only_diagnostic_accepts_nonzero_reg51():
    code,entry=k6mod.build(str(SRC))
    assert len(code)<1600 and entry>=0
    for val in (0,0x9037,0x8000,0xA03C,0x0300,0x0200):
        before=registers(val)
        tx={}
        ret,said,after,_=run(before,src=str(SRC),level=3,tx=tx, count=7_000_000)
        assert ret==27,(val,ret,said)
        assert after[0x51]==val and after[0x08]==before[0x08]
        assert after[0x30]==before[0x30]
        assert all(after[k]==before[k] for k in (0x52,0x70,0x71))
        assert tx.get('w',[])==[] and tx.get('log',[])==[]
        assert len(said)==4,said
        assert said[0]==f'tx27b 51a={val:04x} 51b={val:04x}'
        assert said[1]=='tx27b 08=87ff 52=0292'
        assert said[2]=='tx27b 70=0000 71=1234'
        assert said[3]=='tx27b 30a=bff1 30b=bff1'


@need_clang
@pytest.mark.parametrize('change,expected',[
    ({0x00:0x4819},-6),
    ({0x30:0xBFF3},-2),
])
def test_invalid_chip_or_transmitting_radio_refused_without_writes(change,expected):
    before={**registers(0x9037),**change}
    tx={}
    ret,said,after,_=run(before,src=str(SRC),level=3,tx=tx,count=7_000_000)
    assert ret==expected
    assert tx.get('w',[])==[] and tx.get('log',[])==[]
    assert after[0x51]==before[0x51]
    assert said==[]


def test_rejects_all_write_and_tx_api_calls_in_source():
    s=SRC.read_text()
    for call in ('->fd_write(', '->tx_on(', '->tx_pa(', '->tx_off(',
                 '->tx_on_quiet(', 'mmio_wr('):
        assert call not in s
    assert '#define K6_MODULE_LEVEL 3' in s
    assert 'return 27;' in s
    assert 'TX27B_DELAY_US 5000u' in s


def test_no_sotto_or_production_modem_file_changed():
    assert SRC.name=='tx27b_reg51_idle_diag.c'
