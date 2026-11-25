"""Zero-hardware regressions for the Sotto 1 kHz RF fixture and TX lease probe.

Physical observations are preserved in bench logs, not asserted as emulator
behavior: the simulator currently does not reproduce the API29 TX refusal.
"""
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from modem import k6mod
from tools.k6_sotto_virtual_link import Channel, prepare, trial
from tests.test_k6_sotto_tone import pcm_fixture

ROOT=Path(__file__).resolve().parents[1]
FIXTURE=ROOT/"c/k6/mod/sotto_tone_fixture_tx.c"
LEASE=ROOT/"c/k6/mod/sotto_txlease_probe.c"
TONE_BITS=ROOT/"c/k6/mod/sotto_tone_bits.h"


def test_preencoded_1k_pcm_frames_match_independent_native_encoder():
    with TemporaryDirectory(prefix="tone-compare-") as td:
        reference=prepare(ROOT,Path(td),pcm=pcm_fixture(),tone=True)["reference"]
    code=TONE_BITS.read_text()
    stored=bytes(int(x,16) for x in re.findall(r"0x([0-9a-fA-F]{2})",code))
    assert len(reference)==len(stored)==48
    assert stored==reference


def test_opt_in_tone_fixture_compiled_arm_virtual_rf_crc():
    default,_=k6mod.build(str(FIXTURE))
    assert len(default)<250 and k6mod.module_name(default)=="SOTTO 1K FX"
    armed,entry=k6mod.build(str(FIXTURE),cflags=[
        *k6mod.CFLAGS,"-DSOTTO_1K_FIXTURE_ARMED=1"])
    assert entry==16 and len(armed)<2000 and k6mod.module_name(armed)=="SOTTO 1K FX"
    assert "#define SOTTO_1K_FIXTURE_ARMED 0" in FIXTURE.read_text()
    with TemporaryDirectory(prefix="tone-fixture-") as td:
        art=prepare(ROOT,Path(td),pcm=pcm_fixture(),tone=True)
        art.update(code=armed,entry=entry)
        received=trial(art,Channel(),cpi=1.5,spi_us=10,capture_codec=True)
    assert received["module_return"]==8
    assert received["codec_frames"]==8
    assert received["codec_exact"]
    assert received["session"]==dict(valid=3,invalid=0,complete=True,offset_2=0)
    assert received["received"]==3
    assert received["tx"]["completed"]==3
    assert not received["tx"]["active"]
    assert received["wire_decoder_agrees"]


def test_txlease_default_is_inert_and_api_29_needs_no_codec():
    default,_=k6mod.build(str(LEASE))
    armed,entry=k6mod.build(str(LEASE),cflags=[
        *k6mod.CFLAGS,"-DK6_TXLEASE_ARMED=1"])
    assert k6mod.module_name(default)==k6mod.module_name(armed)=="TXLEASE PROB"
    assert len(default)<100 and len(armed)<400 and entry==16
    source=LEASE.read_text()
    assert "#define K6_MODULE_LEVEL 29" in source
    assert "#define K6_TXLEASE_ARMED 0" in source
    assert "tx_on(1200u)" in source
    assert "delay_us(250000u)" in source
    assert "tx_off()" in source
