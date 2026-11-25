"""Real Cortex-M0 generated-tone module through virtual FSK IQ receiver.

No radio, UART, physical SDR, mic, or hardware RF used by this test.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
from modem import k6mod
from tools.k6_sotto_module import build
from tools.k6_sotto_virtual_link import Channel, prepare, trial

ROOT=Path(__file__).resolve().parents[1]
TONE8=(0,11585,16384,11585,0,-11585,-16384,-11585)
BLOCKS=32


def pcm_fixture():
    return np.tile(np.asarray(TONE8,dtype="<i2"),BLOCKS*10)


def test_1khz_pcm_reference_is_exact_320ms_and_in_phase():
    a=pcm_fixture()
    assert len(a)==BLOCKS*80==2560
    assert a[0]==a[-8]==0
    assert np.array_equal(a[0:80],a[80:160])
    assert np.max(a)==16384 and np.min(a)==-16384
    spec=np.abs(np.fft.rfft(a.astype(float)))
    assert np.argmax(spec)==320  # 320 cycles / 320 ms = 1 kHz


def test_inert_default_build_never_references_transmitter():
    code,entry=k6mod.build(str(ROOT/"c/k6/mod/sotto_tone_tx.c"))
    assert entry==16
    assert k6mod.module_name(code)=="SOTTO TONE"
    assert len(code)<1500
    source=(ROOT/"c/k6/mod/sotto_tone_tx.c").read_text()
    assert '#define SOTTO_TONE_TX_ARMED 0' in source
    assert 'mic_stream(' not in source
    assert 'a->tx_on(9000u)' in source
    assert 'a->rx_hz()!=433500000u' in source


def test_full_generated_tone_cortex_m0_to_virtual_iq():
    with tempfile.TemporaryDirectory(prefix="sotto-tone-virtual-") as td:
        art=prepare(ROOT,Path(td),pcm=pcm_fixture(),tone=True)
        assert k6mod.module_name(art["code"])=="SOTTO TONE"
        assert len(art["reference"])>=12
        result=trial(art,Channel(),cpi=1.5,spi_us=10,capture_codec=True,
                     capture_rf_frames=True)
        assert result["module_return"]==result["codec_frames"]>0,result
        assert result["codec_exact"],result
        assert result["session"]["complete"],result
        assert result["session"]["invalid"]==0,result
        assert result["tx"]["completed"]==result["received"]>=3,result
        assert not result["tx"]["active"],result
        assert not any(result["tx"][k] for k in ("aborted","overflows","rejected")),result
        assert result["received_codec_bytes"]==art["reference"]
        assert all(len(f)==66 for f in result["received_physical_frames"])
