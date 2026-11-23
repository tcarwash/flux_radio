"""TX-15P non-commensurate tone-oscillator phase-vector acceptance.

All actual RF interaction is prohibited here. The separate Unicorn
emulator only simulates register writes and PTT state transitions.
"""
from pathlib import Path
import sys
import re

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
sys.path.insert(0,str(ROOT/"tools"))

from k6_tx15_phase_vector import (
    MODES, FS, OLD_HEADER, NEW_HEADER, PILOT_SYMBOLS, N_SYMBOLS,
    coherence,design_report,ideal_audio_phase,parse_header,
)
from modem import k6mod
from test_k6_i2s import run,k6_clock,START,need_clang

SOURCE=ROOT/"c/k6/mod/tx8probe.c"


def test_original_150_rate_vector_is_phase_ambiguous():
    old=design_report(OLD_HEADER)
    assert len(old["symbols"])==64
    # Word rounding makes fractions up to 0.00022, but practical
    # phase discrimination under the 0.43-second capture is ~zero.
    assert all(
        c["pilot_analytic_phase_coherence"] > .999 and
        c["heldout_analytic_phase_coherence"] > .999
        for c in old["hypothesis_coherence"]
    )


def test_phase_vector_distinguishes_models_on_pilot_and_heldout():
    report=design_report(NEW_HEADER)
    assert not report["all_nominal_tone_cycle_counts_integral"]
    assert len(report["symbols"])==64
    assert len(set(report["symbols"][:PILOT_SYMBOLS]))==8
    assert sum(x==y for x,y in zip(report["symbols"][1:],
                                   report["symbols"][:-1]))>=20
    for c in report["hypothesis_coherence"]:
        assert c["pilot_analytic_phase_coherence"] < .80,c
        assert c["heldout_analytic_phase_coherence"] < .45,c
    assert report["offline_only"]
    assert report["physical_rf_emission_claim"] is False


def test_frequency_words_have_expected_hz_and_bounded_spectrum():
    words,symbols=parse_header(NEW_HEADER)
    nominal=np.array([502,782,1091,1424,1693,1987,2273,2519])
    assert np.max(abs(words/10.32444-nominal)) < .05
    assert np.min(nominal)>450 and np.max(nominal)<2550
    assert len(symbols)==64 and all(0<=x<8 for x in symbols)
    assert [int(x) for x in symbols[:16]]==[
        0,0,1,2,2,3,4,4,5,6,6,7,0,7,7,1
    ]


def test_phase_models_same_word_and_change_reset():
    words,sym=parse_header(NEW_HEADER)
    continuous=ideal_audio_phase(words,sym,"continuous")
    changes=ideal_audio_phase(words,sym,"reset_on_change")
    every=ideal_audio_phase(words,sym,"reset_on_every_write")
    sps=FS//150
    assert len(continuous)==sps*N_SYMBOLS
    # The second symbol is the SAME word: reset-on-change and
    # phase-continuous have the same next tone phase, but reset-
    # every-write zeroes it. This is the key new discriminator.
    assert abs(changes[sps]-continuous[sps])<1e-8
    assert abs(np.exp(1j*(every[sps]-continuous[sps]))-1)>.1
    # The third symbol changes tone: reset-on-change differs from
    # continuous because the previous 2*502 Hz slots contributed
    # a fractional number of cycles to the phase accumulator.
    assert abs(np.exp(1j*(changes[2*sps]-continuous[2*sps]))-1)>.1


def test_models_are_not_measurements_and_reject_invalid_data(tmp_path):
    with pytest.raises(ValueError):
        ideal_audio_phase(np.arange(8),np.arange(64)%8,"rf_iq_generator")
    with pytest.raises(ValueError):
        ideal_audio_phase(np.arange(8),np.arange(64)%8,"continuous",mcu_ppm=5000)
    p=tmp_path/"bad.h"
    p.write_text("not a vector")
    with pytest.raises(ValueError):
        parse_header(p)


@pytest.fixture
def phase_wrapper(tmp_path):
    p=tmp_path/"tx15phase.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_PHASE_VECTOR_V2 1\n"
        "#define TX8_SYMBOL_RATE 150u\n"
        f'#include "{SOURCE}"\n'
    )
    return p


@need_clang
def test_phase_vector_64_writes_preserve_default_pa_off_and_cleanup(phase_wrapper):
    # No RF_ENABLE = no explicit PA-on and no actual hardware access.
    code,entry=k6mod.build(str(phase_wrapper))
    assert entry==0 and len(code)<4096
    mmio,rcc,timers=k6_clock()
    tx={}
    ret,said,after,writes=run(
        {**START,0:0x4829,0x70:0,0x71:0},
        src=str(phase_wrapper),level=3,rx_hz=440850000,
        tx=tx,mmio=mmio,write_us=180,count=130_000_000)
    assert ret==64,(ret,said)
    assert said[-1].startswith("tx8p2 n=64 ")
    assert "late=0" in said[-1]
    words,symbols=parse_header(NEW_HEADER)
    tone_writes=[v for reg,v,t in tx["w"] if reg==0x71 and v]
    assert tone_writes==[int(words[s]) for s in symbols]
    assert [x[0] for x in tx["log"]]==[
        "tx_on","tx_pa",*["tx_pa"]*63,"tx_pa","tx_off"
    ]
    assert all(e[1]==0 for e in tx["log"] if e[0]=="tx_pa")
    assert after[0x70]==0 and after[0x71]==0
    assert not timers.get(0,0)&1 and rcc[0x18]==0


@need_clang
def test_phase_vector_does_not_allow_wrong_symbol_rate(tmp_path):
    p=tmp_path/"wrong_rate.c"
    p.write_text(
        "#define TX8_BENCH_ENABLE 1\n"
        "#define TX8_PHASE_VECTOR_V2 1\n"
        "#define TX8_SYMBOL_RATE 300u\n"
        f'#include "{SOURCE}"\n'
    )
    with pytest.raises(Exception):
        k6mod.build(str(p))


@need_clang
def test_rf_variant_still_requires_both_independent_acknowledgements(tmp_path):
    for fixture,watchdog in [(0,0),(1,0),(0,1)]:
        p=tmp_path/f"fail-{fixture}-{watchdog}.c"
        p.write_text(
            "#define TX8_BENCH_ENABLE 1\n"
            "#define TX8_PHASE_VECTOR_V2 1\n"
            "#define TX8_RF_ENABLE 1\n"
            f"#define TX8_RF_FIXTURE_ACK {fixture}\n"
            f"#define TX8_RF_WATCHDOG_ACK {watchdog}\n"
            "#define TX8_HZ 433500000u\n"
            "#define TX8_SYMBOL_RATE 150u\n"
            f'#include "{SOURCE}"\n'
        )
        with pytest.raises(Exception):
            k6mod.build(str(p))
