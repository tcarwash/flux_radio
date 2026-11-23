"""TX24B public-sync FSK+REG40 independent synthetic receiver controls. No RF."""
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"tools"))
from k6_tx24b_fsk_reg40_analyze import (
    BAUD,LEVELS,KNOWN_BITS,PREAMBLE,SYNC,MARK_BITS,CENTRAL,
    SOURCE_PAYLOAD_BYTES,GUARD_BYTES,PREFIX_BYTES,
    score_frequency,find_known_frames,prefix_and_guard_candidates
)

def signal_fixture(*,model="monotone",prefix=True,noise=45.,fs=128000,
                   bad_frame=None):
    rng=np.random.default_rng(243)
    t=np.arange(0,1.85,1/fs)
    y=80.+rng.normal(0,noise,len(t))
    start=.115
    levels={0x00:.921,0x16:1.0,0x40:1.151,0x80:1.381}
    if model=="flat":
        levels={k:1. for k in levels}
    if model=="inverse":
        levels={0x00:1.35,0x16:1.,0x40:.94,0x80:.76}
    if model=="drift":
        levels[0x16]=1.
    for k,level in enumerate(LEVELS):
        scale=levels[level]
        if model=="drift" and k==4:
            scale*=1.3
        if model=="nonbase_replay" and k==7:
            scale*=.7
        if model=="weak" and k==1:
            scale=.10
        use_prefix=prefix and k%2==0
        # Exactly 66 physical bytes. B-chip PREFIX means +16 bits
        # displaces only final sacrificial word. No posthoc bit shifts.
        payload=(PREFIX_BYTES+SOURCE_PAYLOAD_BYTES if use_prefix else
                 SOURCE_PAYLOAD_BYTES+GUARD_BYTES)
        if model=="bad_guard" and k==3:
            payload=payload[:-2]+b'\x13\x37'
        if model=="payload_bitflip" and k==3:
            tmp=bytearray(payload)
            tmp[15]^=0x80
            payload=bytes(tmp)
        bits=np.concatenate((MARK_BITS,np.unpackbits(
            np.frombuffer(payload,dtype=np.uint8))))
        assert len(bits)==688
        if model=="bad_marker" and k==5:
            bits[:100]=1-bits[:100]
        frame_start=start+k*.175
        a=np.searchsorted(t,frame_start,side="left")
        b=np.searchsorted(t,frame_start+len(bits)/BAUD,side="left")
        symbol=np.floor((t[a:b]-frame_start)*BAUD).astype(int)
        symbol=np.clip(symbol,0,len(bits)-1)
        y[a:b]+=1550*scale*(2*bits[symbol].astype(float)-1)
    return y,fs

def test_stimulus_guards_and_source_pattern():
    assert len(LEVELS)==8 and LEVELS[0]==LEVELS[2]==LEVELS[4]==0x16
    assert set(LEVELS)=={0x00,0x16,0x40,0x80}
    assert len(MARK_BITS)==160 and len(KNOWN_BITS)==512
    assert len(CENTRAL)==256
    assert SOURCE_PAYLOAD_BYTES[:2]==b'\x00\x00'
    assert SOURCE_PAYLOAD_BYTES[-2:]==b'\xff\xff'
    assert GUARD_BYTES==b'\x5a\xa5'
    assert PREFIX_BYTES==b'\xf8\x00'

def test_public_sync_acquires_eight_fsk_frames_without_payload_training():
    f,fs=signal_fixture()
    frames=find_known_frames(f,fs=fs)
    assert len(frames)==8
    assert all(x["corr"]>.97 for x in frames)
    assert all(x["marker_errors"]==0 for x in frames)
    assert all(x["marker_fsk_separation_hz"]>2000 for x in frames)

def test_synthetic_composite_monotone_per_frame_passes_precommitted():
    f,fs=signal_fixture()
    score=score_frequency(f,fs=fs)
    assert score["candidate_native_FSK_REG40_effect_qualified"],score["conditions"]
    assert all(score["conditions"].values())
    assert score["heldout_bit_errors"]==0
    assert score["total_heldout_interior_bits"]==2048
    assert [x["prefix_displacement_bits"] for x in score["frames"]]==[
        16,0,16,0,16,0,16,0]
    ratios=score["normalized_by_level"]
    for lvl,value in ((0,.921),(22,1.),(64,1.151),(128,1.381)):
        assert float(ratios[str(lvl)])==pytest.approx(value,abs=.02)

@pytest.mark.parametrize("model,fail_key",[
    ("flat","slow_REG40_effect_monotone"),
    ("inverse","slow_REG40_effect_monotone"),
    ("drift","heldout_reference_stable"),
    ("nonbase_replay","repeated_nonbaseline_levels_stable")
])
def test_signal_negative_models_fail_without_fitting_holds(model,fail_key):
    f,fs=signal_fixture(model=model)
    report=score_frequency(f,fs=fs)
    assert not report["conditions"][fail_key],report["conditions"]
    assert not report["candidate_native_FSK_REG40_effect_qualified"]

def test_no_prefix_variant_supported_without_posthoc_bit_shift():
    f,fs=signal_fixture(prefix=False)
    score=score_frequency(f,fs=fs)
    assert score["candidate_native_FSK_REG40_effect_qualified"]
    assert all(x["prefix_displacement_bits"]==0 for x in score["frames"])

def test_invalid_guard_is_not_permitted_to_realign_payload():
    f,fs=signal_fixture(model="bad_guard")
    with pytest.raises(ValueError,match="unique predeclared"):
        score_frequency(f,fs=fs)

def test_only_supported_0_or_16_bit_guard_offsets_are_accepted():
    bits=np.unpackbits(np.frombuffer(SOURCE_PAYLOAD_BYTES+GUARD_BYTES,dtype=np.uint8))
    assert prefix_and_guard_candidates(bits)==[0]
    pref=np.unpackbits(np.frombuffer(PREFIX_BYTES+SOURCE_PAYLOAD_BYTES,dtype=np.uint8))
    assert prefix_and_guard_candidates(pref)==[16]
    assert prefix_and_guard_candidates(np.zeros(528,dtype=np.uint8))==[]
    with pytest.raises(ValueError,match="528"):
        prefix_and_guard_candidates(bits[:-8])

def test_real_archived_bchip_fsk13_public_sync_acquisition_read_only():
    """Replay previous FSK13 real RF IQ; does NOT measure REG40 effect."""
    import hashlib
    from scipy.signal import butter,sosfiltfilt
    from scipy.ndimage import uniform_filter1d
    from k6_fsk_iq_edges import read_iq
    p=(Path.home()/"flux-bench/tx-injection-20261122/"
       "fsk13-guard33/fsk13-guard33-run01.npy")
    if not p.exists():
        pytest.skip("archived physical FSK13 capture not on this host")
    assert hashlib.sha256(p.read_bytes()).hexdigest()==(
        "71113f0fdebf8927a1c78bc340b74de1c95e316e3cb78bcbb8dbf296681349e8")
    z=read_iq(p,duration=4.5,offset=280500.)
    z=sosfiltfilt(butter(3,8000,fs=128000,output="sos"),z)
    f=uniform_filter1d(
        np.angle(z[1:]*z[:-1].conj())*128000/(2*np.pi),size=5)
    frames=find_known_frames(f,fs=128000)
    assert len(frames)==8
    assert all(x["corr"]>.99 and x["marker_errors"]==0 for x in frames)
    assert all(1850<x["marker_fsk_separation_hz"]<2300 for x in frames)


def test_known_payload_is_not_used_to_locate_public_marker():
    file=(ROOT/"tools/k6_tx24b_fsk_reg40_analyze.py").read_text()
    locating=file.split("def find_known_frames")[1].split("def score_frequency")[0]
    assert "KNOWN_BITS" not in locating
    assert "SOURCE_PAYLOAD_BYTES" not in locating
    assert "prefix_and_guard_candidates" not in locating
    assert "REG40" not in locating
    assert "import serial" not in file
    assert "RtlCapture(" not in file

def test_fsk_discriminator_validates_trace_shape():
    with pytest.raises(ValueError,match="bad/missing"):
        score_frequency(np.zeros(2000),fs=128000)
