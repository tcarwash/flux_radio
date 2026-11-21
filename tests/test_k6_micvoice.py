"""Offline-only microphone path candidate: inert default, gates, ADPCM parser."""
import importlib.util
import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/"tests"))
from modem import k6mod
from test_k6_i2s import START, run, need_clang

SRC=ROOT/"c/k6/mod/micvoice.c"
PARSER=ROOT/"tools/k6_micvoice.py"
spec=importlib.util.spec_from_file_location("flux_micvoice",PARSER)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.fixture
def bench_wrapper(tmp_path):
    p=tmp_path/"miclab.c"
    p.write_text('#define K6_MODULE_LEVEL 13\n#define FLUX_MICVOICE_LAB 1\n#include "'+str(SRC)+'"\n')
    return p


def sample_log(data: bytes):
    assert len(data)%26==0
    n=len(data)*2
    rows=[f"p {i:04x} "+data[i:i+26].hex()
          for i in range(0,len(data),26)]
    return "\n".join([
        "K6 MOD say mickrec start 30=bff1 47=6040 48=82df rx=1a46d650",
        "K6 MOD say mickrec keyed 30=c1fa 36=007f now=c3fe",
        *("K6 MOD say "+line for line in rows),
        f"K6 MOD say micrec p ticks=0a00 n={n:04x} w=c8 rest=15ae over=0000",
        "K6 MOD say mickrec done",
    ])+"\n"


@need_clang
def test_default_lab_is_inert_and_small():
    code,entry=k6mod.build(str(SRC))
    assert entry==0 and len(code)<512 and k6mod.source_level(str(SRC))==13
    tx={}
    ret,said,regs,writes=run(START,src=str(SRC),level=13,tx=tx)
    assert ret==-9
    assert said==["MICVOICE LAB DISABLED"]
    assert not tx.get("log") and not tx.get("w")


@need_clang
def test_active_variant_compiles_and_refuses_other_chip(bench_wrapper):
    code,entry=k6mod.build(str(bench_wrapper))
    assert entry==0 and len(code)<6000
    assert k6mod.source_level(str(bench_wrapper))==13
    tx={}
    ret,said,regs,writes=run({**START,0:0x4819},src=str(bench_wrapper),
                              level=13,tx=tx,keypad=[])
    assert ret==-1
    assert not tx.get("log")
    assert not tx.get("w")


def test_guard_sources_are_explicit_and_no_auto_lab_tx():
    s=SRC.read_text()
    assert "#define FLUX_MICVOICE_LAB 0" in s
    assert "api->fd_read(0) != 0x4829u" in s
    assert "api->rx_hz()" in s and "home != BENCH_HZ" in s
    assert 'co_key(api, KEY_WAIT_MS)' in s
    assert 'api->tx_pa(0)' in s
    assert 'api->fd_read(0x36) & 0x0080u' in s
    assert "mic_bus_write(api, 0x30, want)" in s
    assert "api->tx_off()" in s
    assert '#define CW_TEXT "AG7SU"' in s
    assert '#define MAX_BYTES 4096u' in s


def test_decode_ima_adpcm_zero_and_positive_waveform():
    assert np.all(m.decode_adpcm(bytes(26))==0)
    y=m.decode_adpcm(bytes([0x77]*26))
    assert len(y)==52
    assert y[0]>0 and y[4]>y[0] and y[-1]<=32767


def test_strict_roundtrip_and_wav(tmp_path):
    data=bytes([0x77,0x10,0x00,0x89]*13)
    f=tmp_path/"serial.txt"
    f.write_text(sample_log(data))
    out=tmp_path/"mic"
    info=m.export_log(f,out)
    assert info["samples"]==len(data)*2
    assert info["bytes"]==len(data)
    assert info["reg30_mic_enabled"]==0xc3fe
    assert out.with_suffix(".adpcm").read_bytes()==data
    with np.load(out.with_suffix(".npz")) as z:
        assert z["decoded_unscaled"].size==len(data)*2
        assert z["adpcm"].tobytes()==data
    with wave.open(str(out.with_suffix(".wav")),"rb") as w:
        assert w.getframerate()==round(info["measured_sample_rate_hz"])
        assert w.getnframes()==len(data)*2
    assert json.loads(out.with_suffix(".json").read_text())["samples"]==len(data)*2


@pytest.mark.parametrize("modify",[
    lambda rows:rows.pop(4),
    lambda rows:rows.__setitem__(2,rows[2].replace("0000","001a")),
    lambda rows:rows.__setitem__(-1,"K6 MOD say wrong end"),
    lambda rows:rows.__setitem__(1,rows[1].replace("now=c3fe","now=c1fa")),
])
def test_parser_refuses_partial_corrupt_and_wrong_state(modify):
    data=bytes(range(52))
    rows=sample_log(data).splitlines()
    modify(rows)
    with pytest.raises(ValueError):
        m.parse_log("\n".join(rows)+"\n")
