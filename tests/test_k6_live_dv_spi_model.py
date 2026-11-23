"""Offline SPI-table-backed slim Sotto + guarded native-FSK scheduler regression.

The actual codebook data and IVF deck are read through the *same abstract
SPI callback* the K6 would use, but this is a host simulation with public
tables: no radio, SDR, PTT, serial or firmware/SPI write.
"""
from __future__ import annotations
import hashlib
import math
from pathlib import Path
import struct
import subprocess

import pytest

from tools.k6_live_dv_spi_deck import build_deck,SLIM_CELLS
from tools.k6_live_dv_spi_m0_size_probe import build as build_arm
from tests.test_k6_live_dv_guard_pipeline import (
    ROOT,SOTTO,SOURCE_PCM,REF_SLIM,compile_code,run_fixture,verify_packets
)

EXPECTED_DECK='5d05fa9fb0194eee04356c3cb4b15471c780d0a915ee67063ed91a54e64ce9f6'


@pytest.fixture(scope='module')
def spi_host(tmp_path_factory):
    o=tmp_path_factory.mktemp('spi-host-compile')
    executable=o/'live_sotto_spi_model'
    code=['clang','-std=gnu11','-O2','-Wall','-Wextra','-Werror',
          '-DDV_LIVE_FLASH_MODEL','-DSOTTO_V36','-DSOTTO_PITCH_THIRD',
          '-DFX_NARROW_MUL',
          '-include',str(ROOT/'c/k6/mod/dv_live_slim_spi.h'),
          '-I',str(SOTTO),str(ROOT/'tools/k6_live_dv_guarded_host.c'),
          str(ROOT/'tools/k6_live_dv_spi_model.c'),
          str(ROOT/'c/k6/mod/dv_live_slim_spi.c'),
          str(SOTTO/'sotto_fx_slim.c'),str(SOTTO/'fx_tables.c'),
          '-lm','-o',str(executable)]
    compile_code(code)
    return executable


@pytest.fixture(scope='module')
def deck():
    result=build_deck(SLIM_CELLS.read_text())
    assert len(result)==4182
    assert hashlib.sha256(result).hexdigest()==EXPECTED_DECK
    return result


def run_spi_model(executable,deck_data,pcm,tmp_path):
    tmp_path.chmod(0o700)
    voice=tmp_path/'input.raw'
    voice.write_bytes(pcm)
    voice.chmod(0o600)
    table=tmp_path/'deck.bin'
    table.write_bytes(deck_data)
    table.chmod(0o600)
    out=tmp_path/'guarded.bin'
    compressed=tmp_path/'bits.bit'
    timeline=tmp_path/'timeline.csv'
    run=subprocess.run([str(executable),str(voice),str(out),
                        str(compressed),str(timeline),str(table)],
                       capture_output=True,text=True,timeout=20)
    return run,out,compressed,timeline


def test_private_K6_mic_bit_exact_with_external_spi_tables(spi_host,deck,tmp_path):
    if not SOURCE_PCM.exists() or not REF_SLIM.exists():
        pytest.skip('private K6 mic fixture missing')
    r,frames,bits,timeline=run_spi_model(spi_host,deck,SOURCE_PCM.read_bytes(),tmp_path)
    assert r.returncode==0,r.stderr
    compressed=bits.read_bytes()
    assert compressed==REF_SLIM.read_bytes()
    assert hashlib.sha256(compressed).hexdigest()==(
        '8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5')
    assert verify_packets(frames.read_bytes(),compressed)==4
    assert frames.read_bytes()[-2:]==b'\x5a\xa5'
    assert len(timeline.read_text().splitlines())==5


def test_synthetic_audio_external_spi_table_codec_equivalence(spi_host,deck,tmp_path):
    sample=b''.join(struct.pack('<h',int(4000*math.sin(2*math.pi*(370*n/8000))))
                    for n in range(80*84))
    r,frames,bits,timeline=run_spi_model(spi_host,deck,sample,tmp_path)
    assert r.returncode==0,r.stderr
    assert verify_packets(frames.read_bytes(),bits.read_bytes())>=2
    assert not any(x in r.stderr for x in ('microphone raw','flash failure'))


def test_malformed_or_corrupted_spi_deck_aborts_before_audio_output(spi_host,deck,tmp_path):
    b=bytearray(deck)
    b[-7]^=0x40
    r,frame,bits,timeline=run_spi_model(spi_host,bytes(b),
                                       struct.pack('<h',1)*80*12,tmp_path)
    assert r.returncode!=0
    assert 'deck checksum' in r.stderr.lower()
    assert not frame.exists() and not bits.exists() and not timeline.exists()


def test_wrong_spi_deck_configuration_rejected_before_audio(spi_host,deck,tmp_path):
    b=bytearray(deck)
    b[8]=31
    r,frame,bits,timeline=run_spi_model(spi_host,bytes(b),
                                       struct.pack('<h',1)*80*12,tmp_path)
    assert r.returncode!=0
    assert not frame.exists()


def test_cm0_core_explicitly_not_flashable_and_fits_model(tmp_path):
    report=build_arm(tmp_path/'probe')
    assert 7000<=report['linked_code_and_readonly_bytes']<=10000
    assert report['linked_bss_bytes']==0
    assert report['linked_mutable_data_bytes']==0
    assert report['optimistic_remaining_arena_bytes']>=2000
    assert report['flashable'] is False
    assert report['runtime_proof'] is False
    assert report['unsafe_to_deploy'] is True
