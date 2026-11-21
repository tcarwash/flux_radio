"""FD6818B candidate 600-baud 8-FSK TX register scheduler (Cortex-M0 emulator only).
No RF is keyed by this test; the compiled module is inert unless specifically
enabled at C compile time. Emulator timings do not establish physical behavior.
"""
from pathlib import Path
import re
import sys

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/"tests"))
from modem import k6mod
from test_k6_i2s import START, k6_clock, run, need_clang

SRC = ROOT/"c/k6/mod/tx8probe.c"
HDR = ROOT/"c/k6/mod/tx8_probe.h"
WPH = 1_032_444 / 100_000

EXPECTED_TONES = [450 + 300 * i for i in range(8)]
EXPECTED_SYMBOLS = (
    list(range(8)) + list(range(7,-1,-1)) +
    [0,7,1,6,2,5,3,4,4,3,5,2,6,1,7,0] +
    [0,2,4,6,1,3,5,7,7,5,3,1,6,4,2,0] +
    [1,4,7,2,5,0,3,6,6,3,0,5,2,7,4,1]
)


def _vector():
    text = HDR.read_text()
    tones = re.search(r"tx8_words\[TX8_WORD_COUNT\]\s*=\s*\{([^}]+)\}",text,re.S).group(1)
    words = [int(w) for w in re.findall(r"\d+(?=u)",tones)]
    symbols = re.search(r"tx8_symbols\[TX8_NS\]\s*=\s*\{([^}]+)\}",text,re.S).group(1)
    symbols = [int(w) for w in re.findall(r"\d+",symbols)]
    return words,symbols


@pytest.fixture(scope="module")
def bench_wrapper(tmp_path_factory):
    path=tmp_path_factory.mktemp("tx8")/"bench.c"
    path.write_text('#define K6_MODULE_LEVEL 3\n#define TX8_BENCH_ENABLE 1\n#include "'+str(SRC)+'"\n')
    return path


def _run(src, **kwargs):
    mmio,rcc,tim=k6_clock()
    tx={}
    regs={**START,0:0x4829}
    regs.update(kwargs.pop("regs",{}))
    ret,said,after,writes=run(regs,src=str(src),level=kwargs.pop("level",3),
                              rx_hz=kwargs.pop("rx_hz",440850000),tx=tx,
                              mmio=mmio,write_us=kwargs.pop("write_us",180),
                              count=40_000_000,**kwargs)
    assert rcc[0x18] == 0, "did not restore TIM17 RCC clock"
    assert not tim.get(0,0)&1, "left timer running"
    return ret,said,after,tx


def test_vectors_are_the_requested_8fsk():
    words,syms=_vector()
    assert syms==EXPECTED_SYMBOLS and len(syms)==64
    assert len(words)==8 and all(0<=s<8 for s in syms)
    for word,hz in zip(words,EXPECTED_TONES):
        assert abs(word/WPH-hz)<0.06


@need_clang
def test_default_module_inert():
    code,entry=k6mod.build(str(SRC))
    assert entry==0 and len(code)<512
    ret,said,after,tx=_run(SRC)
    assert ret==-9 and said==["tx8 disabled; bench build required"]
    assert not tx.get("log") and not tx.get("w")


@need_clang
def test_enabled_module_compiles_under_module_budget(bench_wrapper):
    code,entry=k6mod.build(str(bench_wrapper))
    assert entry==0 and len(code)<4096
    assert k6mod.source_level(str(bench_wrapper))==3


@need_clang
def test_64_symbols_clocked_with_guarded_tx(bench_wrapper):
    ret,said,after,tx=_run(bench_wrapper)
    assert ret==64 and said and said[-1].startswith("tx8 n=64 ")
    assert tx["log"][0][:2]==("tx_on",4000)
    assert tx["log"][-1][0]=="tx_off"
    assert after[0x70]==after[0x71]==0
    writes=tx["w"]
    freqs=[v for r,v,_ in writes if r==0x71 and v]
    words,syms=_vector()
    assert freqs==[words[s] for s in syms]
    assert len(freqs)==64
    tone_times=np.asarray([us for r,v,us in writes if r==0x71 and v])
    dt=np.diff(tone_times)
    assert abs(np.mean(dt)-1666.6667) < 10
    assert dt.min()>1400 and dt.max()<1950
    assert abs((tone_times[-1]-tone_times[0])-63*1e6/600)<650
    assert len([n for n,_,_ in tx["log"] if n=="tx_on"])==1
    assert len([n for n,_,_ in tx["log"] if n=="tx_off"])==1


@need_clang
def test_busy_or_wrong_frequency_never_keys(bench_wrapper):
    for options,expected in (
        ({"rx_hz":146560000},-2),
        ({"regs":{0x67:300}},-7),
        ({"regs":{0:0x4819}},-6),
    ):
        ret,said,after,tx=_run(bench_wrapper,**options)
        assert ret==expected
        assert not tx.get("log")
        assert not tx.get("w")


@need_clang
def test_guarded_transmit_refusal(bench_wrapper):
    # The emulated guarded TX service refuses before oscillator writes.
    # The test hook can refuse transmit based on its on_ret setting.
    mmio,rcc,tim=k6_clock()
    tx={"on_ret":-1}
    ret,said,after,w=run({**START,0:0x4829},src=str(bench_wrapper),
                          level=3,rx_hz=440850000,tx=tx,mmio=mmio,
                          write_us=180,count=40_000_000)
    assert ret==-5 and not tx.get("w")
    assert not tim.get(0,0)&1


@need_clang
def test_key_abort_restores_tx_and_timer(bench_wrapper):
    ret,said,after,tx=_run(bench_wrapper, keys=((185000,500000),))
    assert ret==-4 and said[-1]=="tx8 aborted; TX released"
    assert tx["log"][-1][0]=="tx_off"
    assert after[0x70]==after[0x71]==0
