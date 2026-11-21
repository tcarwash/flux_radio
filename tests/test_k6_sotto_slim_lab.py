"""Pinned upstream Sotto 1200 slim encoder: artifact, bitstream, channel budget."""
from __future__ import annotations

import importlib.util
import json
import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tools/k6_sotto_slim_lab.py"
spec = importlib.util.spec_from_file_location("flux_sotto_slim_lab", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def _write_wav(path, pcm, fs=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(np.asarray(pcm, dtype="<i2").tobytes())


def test_upstream_submodule_pins_a_slim_encoder():
    assert m.SOTTO.joinpath("docs/sotto-slim.md").is_file()
    assert m.SOTTO.joinpath("c/fixed/sotto_slim.h").is_file()
    assert m.SOTTO.joinpath("c/fixed/sotto_fx_slim.c").is_file()
    header=m.SOTTO.joinpath("c/fixed/sotto_slim.h").read_text()
    for name in ("slim_enc_bytes", "slim_encode10", "slim_flush"):
        assert name in header
    implementation=m.SOTTO.joinpath("c/fixed/sotto_fx_slim.c").read_text()
    for hook in ("SLIM_CB_FETCH", "SLIM_IVF_CEN", "SLIM_IVF_CELL", "SLIM_DCT"):
        assert hook in implementation


def test_exact_codec_to_modem_budget():
    assert m.SUPERFRAME_BYTES * 8 / (m.SUPERFRAME_MS / 1000) == m.CODEC_BPS
    assert 600 * 3 == 1800
    assert 1800 * (1/2) < m.CODEC_BPS
    assert 1800 * (2/3) == m.CODEC_BPS  # no framing/pilots left!
    assert 1800 * (3/4) > m.CODEC_BPS


def test_sample_rate_validation(tmp_path):
    p=tmp_path/"stereo.wav"
    with wave.open(str(p), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(bytes(1600))
    with pytest.raises(ValueError, match="mono 16-bit"):
        m.read_mono_pcm16(p)
    p=tmp_path/"valid.wav"
    _write_wav(p, np.zeros(100,np.int16), 17534)
    pcm,fs=m.read_mono_pcm16(p)
    assert fs==17534
    assert pcm.size==100
    if importlib.util.find_spec("scipy"):
        y=m.resample_to_sotto(pcm,fs)
        assert len(y)==round(len(pcm)*8000/fs)


@pytest.fixture(scope="module")
def native_codec():
    if not shutil.which("make") or not shutil.which("cc"):
        pytest.skip("requires make and a C compiler")
    subprocess.run(["make", "-C", str(m.SOTTO / "c"), "-j2",
                    "fixed/sotto_slim_enc","fixed/sotto_dec_fx"],
                   check=True, capture_output=True)
    return m.SLIM, m.DECODER


def test_speech_encodes_as_normal_sotto_frames(tmp_path,native_codec):
    t=np.arange(1920)/8000
    x=np.round(9000*np.sin(2*np.pi*185*t)
              + 1700*np.sin(2*np.pi*870*t)).astype(np.int16)
    inp=tmp_path/"speech.wav"
    _write_wav(inp,x)
    slim,decoder=native_codec
    meta=m.encode_wav(inp,tmp_path/"one",slim=slim,decoder=decoder)
    assert meta["superframes"]==6
    assert meta["encoded_bytes"]==36
    assert meta["codec_bitrate_bps"]==1200
    assert meta["decoded_samples"]>=len(x)
    second=m.encode_wav(inp,tmp_path/"two",slim=slim)
    assert second["encoded_sha256"]==meta["encoded_sha256"]
    assert (tmp_path/"one.sotto").read_bytes()==(tmp_path/"two.sotto").read_bytes()
    assert (tmp_path/"one.decoded.wav").is_file()
    assert json.loads((tmp_path/"one.json").read_text())["mcu_codec_integrated"] is False
