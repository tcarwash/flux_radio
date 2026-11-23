"""TX-15R 192-event, long pilot/holdout discriminator / bounded module acceptance.

Unicorn tests run an emulated M0 with no physical RF, UART or SPI access.
"""
from pathlib import Path
import re
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
sys.path.insert(0,str(ROOT/"tools"))
from modem import k6mod
from test_k6_i2s import run,k6_clock,START,need_clang

SOURCE=ROOT/"c/k6/mod/tx8probe.c"
HEADER=ROOT/"c/k6/mod/tx8_long_phase_probe.h"
N=192
PILOT=48
EXPECTED_WORDS=[5183,8074,11264,14702,17479,20515,23467,26007]


def parse_long_header(path=HEADER):
    src=Path(path).read_text()
    assert re.search(r"#define\s+TX8_NS\s+192u",src)
    assert re.search(r"#define\s+TX8_PILOT\s+48u",src)
    def arr(name):
        m=re.search(r"static const uint(?:8|16)_t\s+"+name+
                    r"\[\w+\]\s*=\s*\{([^}]+)\}",src,re.S)
        assert m,name
        return [int(x) for x in re.findall(r"\d+",m.group(1))]
    w,s=arr("tx8_words"),arr("tx8_symbols")
    assert len(w)==8 and len(s)==N
    assert w==EXPECTED_WORDS and all(0<=x<8 for x in s)
    return w,s


def wrapper(tmp_path,*,rf=False,ack_fixture=True,ack_watchdog=True,baud=150):
    p=tmp_path/("tx15r-armed.c" if rf else "tx15r-pa-off.c")
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_PHASE_VECTOR_V3 1\n"
        f"#define TX8_SYMBOL_RATE {baud}u\n"
        f"#define TX8_RF_ENABLE {int(rf)}\n"
        f"#define TX8_RF_FIXTURE_ACK {int(ack_fixture)}\n"
        f"#define TX8_RF_WATCHDOG_ACK {int(ack_watchdog)}\n"
        "#define TX8_HZ 433500000u\n"
        "#define TX8_GAIN 24u\n"
        f'#include "{SOURCE}"\n'
    )
    return p


def test_long_vector_discriminates_repeated_pair_and_has_pilot():
    w,s=parse_long_header()
    hz=np.array(w)/10.32444
    changes=[i for i in range(1,N) if s[i]!=s[i-1]]
    strong=[i for i in changes if abs(hz[s[i]]-hz[s[i-1]])>450]
    assert [i for i in strong if i<PILOT]==list(range(3,48,3))
    assert len([i for i in strong if i>=PILOT])>=40
    assert s[:3]==[5,5,5]
    assert s[-3:]==[4,4,4]
    assert len(set(s[:PILOT]))==8 and len(set(s[PILOT:]))==8
    assert max(abs(hz-np.array([502,782,1091,1424,1693,1987,2273,2519])))<.05
    # Enough time for 192 scheduled symbols, plus a 100-ms lead, inside
    # the 1800-ms TX lease with 400+ms for safe de-key.
    assert N/150+.100+.050 < 1.8
    assert N/150 > .8


@need_clang
@pytest.mark.parametrize("rf",[False,True])
def test_long_bounded_cortex_m0_emulation_with_192_exact_writes(tmp_path,rf):
    p=wrapper(tmp_path,rf=rf)
    code,entry=k6mod.build(str(p))
    assert entry==0 and 0<len(code)<4096
    mmio,rcc,timers=k6_clock()
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x70:0,0x71:0},src=str(p),
        level=3,rx_hz=433500000,tx=tx,mmio=mmio,write_us=180,
        count=240_000_000,
    )
    assert ret==N and said[-1].startswith("tx8r n=192 "),said
    assert "late=0" in said[-1],said
    w,s=parse_long_header()
    assert [v for reg,v,t in tx["w"] if reg==0x71 and v]==[w[k] for k in s]
    assert tx["log"][0][:2]==("tx_on",1800 if rf else 4000)
    assert tx["log"][-1][0]=="tx_off"
    assert [e[1] for e in tx["log"] if e[0]=="tx_pa"]==([0,1,0] if rf else [0]*193)
    assert after[0x70]==0 and after[0x71]==0
    assert not timers.get(0,0)&1 and rcc[0x18]==0


@need_clang
@pytest.mark.parametrize("ack_fixture,ack_watchdog",[(0,0),(0,1),(1,0)])
def test_long_rf_needs_both_acknowledgements(tmp_path,ack_fixture,ack_watchdog):
    p=wrapper(tmp_path,rf=True,ack_fixture=ack_fixture,ack_watchdog=ack_watchdog)
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_long_must_be_150_symbols_per_s(tmp_path):
    p=wrapper(tmp_path,baud=300)
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_mutually_exclusive_phase_vectors(tmp_path):
    p=wrapper(tmp_path)
    p.write_text("#define TX8_PHASE_VECTOR_V2 1\n"+p.read_text())
    with pytest.raises(Exception):
        k6mod.build(str(p))
