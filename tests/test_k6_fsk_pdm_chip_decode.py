"""End-to-end FSK PDM native-chip decoder tests; no connected hardware."""
from pathlib import Path
import copy
import json
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_fsk_pdm_chip_decode import decode,validate_manifest,RAW_FS


@pytest.fixture(scope="module")
def manifest():
    return json.loads((ROOT/"docs/k6-fsk-pdm300-vector.json").read_text())


def simulate_iq(manifest, *, first_bit_s=.8208, offset_hz=-280_500,
                native_baud=4800, deviation_hz=1200, noise=.30,
                amplitude=5.,seed=417):
    """U8 I/Q with known RFIC prefix and PDM payload plus keyed RF overhead."""
    truth=np.fromiter((int(b) for b in manifest["payload_bits"]),dtype=np.uint8)
    n=int(1.46*RAW_FS)
    t=np.arange(n,dtype=np.float64)/RAW_FS
    ix=np.floor((t-first_bit_s)*native_baud).astype(int)
    within=(ix>=0)&(ix<len(truth))
    val=truth[np.clip(ix,0,len(truth)-1)]
    # Model the short chip-generated preamble as an unmodulated carrier.
    freq=np.where(within,deviation_hz*(2*val.astype(float)-1),0.)
    freq+=offset_hz
    phase=np.cumsum(freq)*2*np.pi/RAW_FS
    keyed=(t>=first_bit_s-.0249)&(t<first_bit_s+len(truth)/native_baud)
    rng=np.random.default_rng(seed)
    wave=np.where(keyed,amplitude,0.)*np.exp(1j*phase)
    wave+=noise*(rng.normal(size=n)+1j*rng.normal(size=n))
    raw=np.empty(n*2,dtype=np.uint8)
    raw[::2]=np.clip(np.rint(wave.real+127.5),0,255).astype(np.uint8)
    raw[1::2]=np.clip(np.rint(wave.imag+127.5),0,255).astype(np.uint8)
    return raw


@pytest.mark.parametrize("start,seed",[(.8208,17),(.8121,18),(.8276,19)])
def test_synthetic_native_chip_and_symbol_holdout(manifest,start,seed):
    result=decode(simulate_iq(manifest,first_bit_s=start,seed=seed),manifest)
    assert result["pilot_correlation"]>.94
    assert abs(result["first_fifo_bit_time_s"]-start)<.0001
    assert result["native_baud_estimate"]==4800
    assert result["native_heldout_bits"]==448
    assert result["native_heldout_bit_errors"]==0,result
    assert result["symbol_heldout_symbols"]==28
    assert result["symbol_heldout_errors"]==0,result
    assert result["learned_training_chip_counts"]==[1,3,5,7,9,11,13,15]
    assert result["no_heldout_truth_used_in_sync_or_training"]


def test_changed_heldout_truth_is_scored_not_used_for_sync(manifest):
    raw=simulate_iq(manifest)
    normal=decode(raw,manifest)
    changed=copy.deepcopy(manifest)
    changed["symbols"][20]=7 if changed["symbols"][20]!=7 else 0
    changed["symbols"][27]=6 if changed["symbols"][27]!=6 else 1
    altered=decode(raw,changed)
    assert altered["native_heldout_bit_errors"]==normal["native_heldout_bit_errors"]
    assert altered["pilot_correlation"]==normal["pilot_correlation"]
    assert altered["first_fifo_bit_time_s"]==normal["first_fifo_bit_time_s"]
    assert altered["recovered_heldout_symbols"]==normal["recovered_heldout_symbols"]
    assert altered["symbol_heldout_errors"]>=normal["symbol_heldout_errors"]+2


@pytest.mark.parametrize("mutation",[
    {"payload_sha256_le_words":"0"*64},
    {"payload_bits":"0"*1024},
    {"native_baud":2400},
    {"symbol_baud":600},
    {"symbols":[1]*44},
])
def test_broken_manifest_rejected(manifest,mutation):
    bad=dict(manifest,**mutation)
    with pytest.raises((ValueError,TypeError)):
        validate_manifest(bad)


def test_noise_cannot_acquire_false_burst(manifest):
    rng=np.random.default_rng(113)
    raw=rng.integers(80,175,size=int(1.5*RAW_FS)*2,dtype=np.uint8)
    with pytest.raises(ValueError,match="burst|pilot"):
        decode(raw,manifest)


def test_rejects_invalid_iq(manifest):
    with pytest.raises(ValueError,match="interleaved"):
        decode(np.zeros(300,dtype=np.uint8),manifest)


def test_decoder_cannot_access_live_rf(manifest):
    src=(ROOT/"tools/k6_fsk_pdm_chip_decode.py").read_text()
    for phrase in ("import serial", "from k6_live", "RtlCapture(",
                   "tx_on(", "run_module(", "ptt_burst(", "fl_prog"):
        assert phrase not in src
