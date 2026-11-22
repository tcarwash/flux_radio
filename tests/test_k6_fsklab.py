"""Native FD6818B known-FSK TX laboratory: host vector and M0 emulator tests.

All TX in these tests is Unicorn-emulated. No radio, serial port, SDR,
actual RF transmission, flashing, or calibration register change.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
from modem import k6mod
from test_k6_i2s import need_clang, run, START
from k6_fsk_lab_vector import (
    build_vector, make_probe_vector, bits_from_words, words_from_bits
)

SRC = ROOT / "c/k6/mod/fsklab.c"
HEADER = ROOT / "c/k6/mod/fsk_lab_vector.h"
MANIFEST = ROOT / "docs/k6-fsk-lab-vector.json"
TX_REGS = (0x3f,0x59,0x58,0x70,0x72,0x5a,0x5b,0x5c,0x5d)


def saved_regs():
    return {**START, 0x00:0x4829, 0x30:0xBFF1, 0x3f:0,
            0x67:120, 0x40:0x3516, 0x59:0, 0x70:0,
            0x58:0, 0x72:0, 0x5a:0, 0x5b:0, 0x5c:0, 0x5d:0}


class FSKChipModel:
    """Only the mailbox/FIFO and TX interrupt behavior; no RF waveforms."""
    def __init__(self, *, complete=True, latency_ms=20):
        self.fifo = []
        self.started = None
        self.complete = complete
        self.latency_us = round(latency_ms*1000)
        self.traffic = []

    def key(self, name, us):
        self.traffic.append((name,us))
        return 0

    def write(self, reg, value, us):
        if reg == 0x5f:
            self.fifo.append(value)
        if reg == 0x59 and value == 0x08f8:
            self.started = us
        self.traffic.append((reg,value,us))

    def read(self, reg, us):
        if reg == 0x0c:
            payload_us = 1024/4800*1e6
            return int(self.complete and self.started is not None and
                       us - self.started >= payload_us+self.latency_us)
        return None


@pytest.fixture()
def enabled_source(tmp_path):
    wrapper = tmp_path / "fsklab-enabled.c"
    wrapper.write_text(
        "#define K6_MODULE_LEVEL 3\n"
        "#define FSK_LAB_ENABLE 1\n"
        "#define FSK_LAB_FIXTURE_ACK 1\n"
        "#define FSK_LAB_WATCHDOG_ACK 1\n"
        f'#include "{SRC}"\n'
    )
    return wrapper


def run_m0(source, *, tx=None, fsk=None, registers=None, rx_hz=433500000,
           keys=(), power_off_us=None):
    tx = {} if tx is None else tx
    return run(registers or saved_regs(),src=str(source),level=3,
               tx=tx,fsk=fsk,rx_hz=rx_hz,keys=keys,
               write_us=150,count=40_000_000,power_off_us=power_off_us)


def test_generator_exact_wire_order_and_inverse():
    words=(0x1234,0xa5c3,0xffff,0x0000)
    bits=bits_from_words(words)
    assert bits[:16]=="0011010000010010"
    assert words_from_bits(bits)==words
    with pytest.raises(ValueError):
        words_from_bits("10101010")
    with pytest.raises(ValueError):
        words_from_bits("0"*15+"z")


def test_default_probe_has_sacrificial_preamble_and_long_runs():
    words,segments=make_probe_vector(word_count=64,seed=0xACE1)
    assert len(words)==64
    assert words[:16]==(0x5555,)*16
    assert words[16:20]==(0,)*4
    assert words[20:24]==(0xffff,)*4
    assert words[24:28]==(0x0f0f,)*4
    assert words[28:32]==(0xf0f0,)*4
    assert segments[0]["bits"]==256
    assert segments[-1]["start_bit"]==512


def test_manifest_header_hash_and_config_are_reproducible():
    header,doc=build_vector()
    assert HEADER.read_text()==header
    assert json.loads(MANIFEST.read_text())==doc
    assert doc["payload_words"]==64
    assert doc["baud_register_72"]==0xc194
    assert doc["tone_fsk_gain_register_70"]==0x00b0
    assert doc["payload_sha256_le_words"] in header
    assert "unchanged" in doc["reg_40"]


def test_custom_bits_and_parameter_guards():
    custom="0011010000010010"*32
    header,report=build_vector(word_count=32,baud=1200,
                               data_bits=custom,gain=16)
    assert report["payload_bits_msb_each_byte_lower_byte_first"]==custom
    assert report["segments"]==[dict(name="custom",start_bit=0,bits=512)]
    assert "#define FSK_LAB_BAUD 1200u" in header
    for opts in ({"word_count":31},{"word_count":65},{"gain":15},
                 {"gain":97},{"baud":6000},{"frequency_hz":144390000},
                 {"word_count":32,"data_bits":"01"}):
        with pytest.raises(ValueError):
            build_vector(**opts)


def test_vector_cli_create_only_and_explicit_overwrite(tmp_path):
    h=tmp_path/"known.h"
    m=tmp_path/"known.json"
    args=[sys.executable,str(ROOT/"tools/k6_fsk_lab_vector.py"),
          "--header",str(h),"--manifest",str(m)]
    p=subprocess.run(args,capture_output=True,text=True,timeout=10)
    assert p.returncode==0,p.stderr
    before=h.read_bytes()
    p=subprocess.run(args,capture_output=True,text=True,timeout=10)
    assert p.returncode!=0
    assert h.read_bytes()==before
    p=subprocess.run(args+["--overwrite"],capture_output=True,text=True,timeout=10)
    assert p.returncode==0
    assert h.read_bytes()==before


@need_clang
def test_default_module_is_inert_with_no_transmit_side_effects():
    code,entry=k6mod.build(str(SRC))
    assert entry==0 and len(code)<512
    assert k6mod.source_level(str(SRC))==3
    tx={}
    ret,lines,after,w=run_m0(SRC,tx=tx)
    assert ret==-9
    assert lines==["fsklab inert: fixture+watchdog required"]
    assert not tx.get("log") and not tx.get("w")


@need_clang
def test_enabled_module_compiles_under_budget(enabled_source):
    code,entry=k6mod.build(str(enabled_source))
    assert entry==0 and len(code)<4096
    assert k6mod.source_level(str(enabled_source))==3


@need_clang
@pytest.mark.parametrize("fixture,watchdog",[(0,0),(1,0),(0,1)])
def test_enable_refuses_to_compile_without_two_acks(tmp_path,fixture,watchdog):
    src=tmp_path/"fsklab-refuse.c"
    src.write_text("#define FSK_LAB_ENABLE 1\n"+
                   f"#define FSK_LAB_FIXTURE_ACK {fixture}\n"+
                   f"#define FSK_LAB_WATCHDOG_ACK {watchdog}\n"+
                   f'#include "{SRC}"\n')
    with pytest.raises(Exception):
        k6mod.build(str(src))


@need_clang
def test_emulated_known_frame_fifo_and_cleanup(enabled_source):
    tx={}
    fsk=FSKChipModel()
    baseline=saved_regs()
    ret,lines,after,w=run_m0(enabled_source,tx=tx,fsk=fsk,registers=baseline)
    assert ret==64, (ret,lines)
    assert lines[-1]=="fsklab known frame sent; RX restored"
    assert tx["log"][0][:2]==("tx_on",2600)
    assert [c[0] for c in tx["log"]].count("tx_on")==1
    assert [c[0] for c in tx["log"]].count("tx_off")==1
    assert tx["log"][-1][0]=="tx_off"
    assert any(c[0]=="tx_pa" and c[1]==0 for c in tx["log"])
    assert any(c[0]=="tx_pa" and c[1]==1 for c in tx["log"])
    assert len(fsk.fifo)==64
    expected = words_from_bits(
        json.loads(MANIFEST.read_text())["payload_bits_msb_each_byte_lower_byte_first"])
    assert tuple(fsk.fifo)==expected
    assert (0x72,0xc194) in w
    assert (0x70,0x00b0) in w
    assert (0x5d,0x7f00) in w
    assert (0x59,0x08f8) in w
    assert not any(reg in (0x30,0x33,0x36,0x37,0x40) for reg,_ in w)
    for reg in TX_REGS:
        assert after.get(reg,0)==baseline.get(reg,0), (reg,after.get(reg))


@need_clang
@pytest.mark.parametrize("kwargs,result",[
    ({"rx_hz":144390000},-2),
    ({"registers":{**saved_regs(),0:0x4819}},-6),
    ({"registers":{**saved_regs(),0x67:400}},-7),
    ({"registers":{**saved_regs(),0x30:0xC1FE}},-2),
    ({"registers":{**saved_regs(),0x3f:0x8000}},-8),
])
def test_guarded_refusals_never_key(enabled_source,kwargs,result):
    tx={}
    ret,lines,after,w=run_m0(enabled_source,tx=tx,**kwargs)
    assert ret==result
    assert not tx.get("log")
    assert not tx.get("w")


@need_clang
def test_transmit_refusal_does_not_write_registers(enabled_source):
    tx={"on_ret":-1}
    ret,lines,after,w=run_m0(enabled_source,tx=tx)
    assert ret==-5
    assert tx["log"][0][0]=="tx_on"
    assert not tx.get("w")


@need_clang
def test_no_completion_times_out_and_unkeys(enabled_source):
    tx={}
    fsk=FSKChipModel(complete=False)
    regs=saved_regs()
    ret,lines,after,w=run_m0(enabled_source,tx=tx,fsk=fsk,registers=regs)
    assert ret==-14
    assert lines[-1]=="fsklab aborted; verify RX, no retry"
    assert tx["log"][-1][0]=="tx_off"
    assert len(fsk.fifo)==64
    for reg in TX_REGS:
        assert after.get(reg,0)==regs.get(reg,0)


@need_clang
def test_key_abort_unkeys_and_restores(enabled_source):
    tx={}
    fsk=FSKChipModel()
    baseline=saved_regs()
    ret,lines,after,w=run_m0(enabled_source,tx=tx,fsk=fsk,
                             registers=baseline,keys=((120000,2000000),))
    assert ret==-4
    assert tx["log"][-1][0]=="tx_off"
    assert lines[-1]=="fsklab aborted; verify RX, no retry"
    for reg in TX_REGS:
        assert after.get(reg,0)==baseline.get(reg,0)


def test_source_contract_does_not_bypass_guarded_radio_controls():
    s=SRC.read_text()
    for contract in ("FSK_LAB_ENABLE", "FSK_LAB_FIXTURE_ACK",
                     "FSK_LAB_WATCHDOG_ACK", "FSK_LAB_TX_LIMIT_MS",
                     "api->tx_on(", "api->tx_pa(0)",
                     "api->tx_off()", "api->key_down()", "km_power_switch_off",
                     "FSK_LAB_FREQUENCY_HZ != 433500000u"):
        assert contract in s
    for forbidden in ("fd_write(0x30", "fd_write(0x33",
                      "fd_write(0x36", "fd_write(0x37",
                      "fd_write(0x40", "api->cal_write",
                      "api->fl_prog","api->fl_erase"):
        assert forbidden not in s
