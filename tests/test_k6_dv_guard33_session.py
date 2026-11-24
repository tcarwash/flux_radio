"""FDV1 live-DV session/control protocol, STRICTLY OFFLINE.

No actual RF transmission, SDR device access, K6 flash or module execution.
The real Cortex-M0 Unicorn test is an included mandatory verification gate.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess

import pytest

from modem import pager
from tools.k6_dv_guard33_session import (
    BEGIN, END, Session, encode_control, decode_control,
    select_guarded, replay_onair, packet_in_observation
)
from tools.k6_dv_guard33_playout import write_private

ROOT = Path(__file__).resolve().parents[1]
UNICORN = Path("/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/.venv-cm0/lib/python3.13/site-packages")
SOURCE = Path("/home/tyler/flux-bench/micvoice-20261123/sotto-offline-run01/k6mic-sotto1200.bit")
PRIV_RX = Path("/home/tyler/flux-bench/micvoice-20261123/fsk14-mic-guard-private-20261123/fsk14-mic-guard-analysis-run01.json")
PROFILE = pager.profile("L", "1200", depth=1, short_last=True, parity=False)
TAG = 0x1234567890ABCDEF


def observation(physical: bytes, t: float):
    chosen = select_guarded(physical)
    assert chosen["valid"] and not chosen["ambiguous"]
    return dict(chosen, payload_start_s=t, selected_baud=4800,
                sync_errors=0, physical_hex=physical.hex())


def make_call(frames: bytes, *, tag: int = TAG, first_id: int = 112,
              interval: float = .32):
    assert len(frames) % 6 == 0 and frames
    chunks = [frames[i:i+48] for i in range(0,len(frames),48)]
    assert len(chunks)<=144 and first_id+len(chunks)-1<=255
    last_id = first_id+len(chunks)-1
    a = Session(BEGIN,tag,first_id)
    b = Session(END,tag,first_id,last_id,len(chunks[-1])//6,len(chunks))
    observations=[observation(encode_control(a),.50)]
    for i,chunk in enumerate(chunks):
        pkt = PROFILE.pack(chunk,msg_id=first_id+i)[0]
        physical = pkt + bytes([0x5A,0xA5])
        if i%2 == 0:
            physical=(bytes([0xF8,0]) + physical)[:66]
        observations.append(observation(physical,1.0+interval*i))
    observations.append(observation((bytes([0xF8,0])+encode_control(b))[:66],
                                    1.0+interval*len(chunks)))
    return dict(frames=observations)


def test_all_control_bytes_resolve_clearly_and_do_not_alias_existing_radio_messages():
    for s in [Session(BEGIN,TAG,112),
              Session(END,TAG,112,115,2,4),
              Session(BEGIN,1,255),
              Session(END,1,255,255,8,1)]:
        physical=encode_control(s)
        assert len(physical)==66 and physical[-2:] == bytes([0x5A,0xA5])
        assert decode_control(physical[:64]) == s
        assert PROFILE.unpack(physical[:64]) is None
        assert pager.parse_control(physical[:64]) is None
        a=select_guarded(physical)
        b=select_guarded((bytes([0xF8,0])+physical)[:66])
        assert a["packet_type"]==b["packet_type"]=="control"
        assert a["offset_bytes"]==0 and b["offset_bytes"]==2
        assert a["packet_hex"]==b["packet_hex"]


@pytest.mark.parametrize("bad",[
    Session(BEGIN,0,112), Session(BEGIN,TAG,112,last_id=113),
    Session(BEGIN,TAG,112,last_frames=1),Session(BEGIN,TAG,112,segments=1),
    Session(END,TAG,112,115,2,3),Session(END,TAG,250,255,2,7),
    Session(END,TAG,112,115,0,4),Session(END,TAG,112,115,9,4),
    Session(END,TAG,112,255,2,143),Session(END,TAG,255,254,8,1),
    Session(17,TAG,112),
])
def test_invalid_session_fields_rejected_without_transmission(bad):
    with pytest.raises(ValueError):
        encode_control(bad)


def test_reserved_data_crc_corruption_and_control_parser_fail_closed():
    base=encode_control(Session(END,TAG,112,115,2,4))
    for i in (0,1,2,3,4,11,12,13,14,15,16,20,54,61,62,63):
        corrupted=bytearray(base)
        corrupted[i]^=0x40
        assert decode_control(bytes(corrupted[:64])) is None
        assert not select_guarded(bytes(corrupted))["valid"]


def test_synthetic_live_voice_26_frames_with_over_air_metadata_and_guard_shift():
    voice = random.Random(21).randbytes(156)
    report=make_call(voice)
    plan=replay_onair(report,buffer_ms=160)
    assert plan["encoded_bytes"]==voice
    assert plan["total_codec_frames"]==26
    assert plan["on_time_codec_frames"]==26
    assert plan["concealed_codec_frames"]==0
    assert plan["expected_packets"]==4
    assert plan["onair_control_frames"]==2
    assert plan["session_control_not_authenticated"] is True
    assert plan["session_tag_hex"]==f"{TAG:016x}"


def test_out_of_order_and_identical_repeated_begin_end():
    voice=bytes([0x37]*156)
    report=make_call(voice)
    report["frames"] = list(reversed(report["frames"]))
    report["frames"].append(copy.deepcopy(report["frames"][0]))
    report["frames"].append(copy.deepcopy(report["frames"][-2]))
    p=replay_onair(report)
    assert p["encoded_bytes"]==voice
    assert p["onair_control_frames"]==4


def test_missing_middle_voice_packet_exact_loss_and_no_fabricated_frames():
    report=make_call(bytes([0x44]*156))
    report["frames"].pop(2)
    p=replay_onair(report)
    assert p["missing_packets"]==1
    assert p["concealed_codec_frames"]==8
    assert p["longest_concealment_ms"]==320
    assert p["encoded_bytes"][48:96]==bytes(48)


def test_missing_final_voice_packet_uses_received_end_length():
    report=make_call(bytes([0x4b]*156))
    report["frames"].pop(-2)
    p=replay_onair(report)
    assert p["expected_packets"]==4
    assert p["total_codec_frames"]==26
    assert p["missing_packets"]==1
    assert p["concealed_codec_frames"]==2
    assert p["encoded_bytes"][-12:]==bytes(12)


@pytest.mark.parametrize("side",["begin","end"])
def test_missing_a_control_refuses_infer_session_boundaries(side):
    report=make_call(b"A"*60)
    report["frames"].pop(0 if side=="begin" else -1)
    with pytest.raises(ValueError,match="Missing on-air"):
        replay_onair(report)


def test_begin_end_different_session_tag_or_id_rejected():
    row=make_call(b"A"*60)
    row["frames"][-1]=observation(encode_control(Session(END,TAG+1,112,113,2,2)),2)
    with pytest.raises(ValueError,match="tags"):
        replay_onair(row)
    row=make_call(b"A"*60)
    row["frames"][-1]=observation(encode_control(Session(END,TAG,113,114,2,2)),2)
    with pytest.raises(ValueError,match="IDs"):
        replay_onair(row)


def test_mixed_control_sessions_or_conflicting_duplicate_fail_closed():
    r=make_call(b"A"*60)
    r["frames"].append(observation(encode_control(Session(BEGIN,TAG+1,112)),.4))
    with pytest.raises(ValueError,match="Conflicting"):
        replay_onair(r)
    r=make_call(b"A"*60)
    r["frames"].append(observation(encode_control(Session(END,TAG,112,113,8,2)),2.5))
    with pytest.raises(ValueError,match="Conflicting"):
        replay_onair(r)


def test_bad_report_header_or_physical_shift_fails_independent_crc_check():
    r=make_call(b"A"*60)
    r["frames"][1]["msg_id"]=100
    with pytest.raises(ValueError,match="header"):
        replay_onair(r)
    r=make_call(b"A"*60)
    r["frames"][0]["physical_hex"]=(b"\x00"*66).hex()
    with pytest.raises(ValueError,match="Physical"):
        replay_onair(r)


def test_speech_before_begin_or_after_end_rejected():
    r=make_call(b"A"*60)
    r["frames"][1]["payload_start_s"]=.2
    with pytest.raises(ValueError,match="outside"):
        replay_onair(r)
    r=make_call(b"A"*60)
    r["frames"][1]["payload_start_s"]=10
    with pytest.raises(ValueError,match="outside"):
        replay_onair(r)


def test_old_fsk14_rf_recording_does_not_magically_claim_onair_session():
    if not PRIV_RX.exists():pytest.skip("Archived independent FSK14 RX unavailable")
    r=json.loads(PRIV_RX.read_text())
    with pytest.raises(ValueError,match="Missing on-air"):
        replay_onair(r)


@pytest.mark.skipif(not SOURCE.exists(),reason="Private original microphone codec absent")
def test_actual_26_recorded_k6_microphone_codec_frames_under_synthetic_session():
    voice=SOURCE.read_bytes()
    assert len(voice)==156
    report=make_call(voice)
    plan=replay_onair(report)
    assert plan["encoded_bytes"]==voice
    assert plan["on_time_codec_frames"]==26
    assert plan["concealed_codec_frames"]==0


def test_private_output_guards_apply_to_session_replay(tmp_path):
    plan=replay_onair(make_call(b"A"*48))
    tmp_path.chmod(0o700)
    summary=write_private(plan,tmp_path,"fdv1-test")
    assert summary["onair_begin_seen"] and summary["onair_end_seen"]
    for s in (".json",".good",".bit"):
        f=tmp_path/("fdv1-test"+s)
        assert f.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_private(plan,tmp_path,"fdv1-test")


def test_real_thumb_cortex_m0_unicorn_session_protocol_execution(tmp_path):
    script=ROOT/"tools/k6_dv_guard33_session_unicorn.py"
    if not UNICORN.is_dir():
        pytest.fail("Mandatory target emulator missing on Flux development host")
    env=dict(os.environ,PYTHONPATH=str(UNICORN))
    cp=subprocess.run(["python3",str(script),"--build-dir",str(tmp_path/"arm")],
                      cwd=ROOT,check=True,capture_output=True,text=True,env=env,
                      timeout=60)
    summary=json.loads(cp.stdout)
    assert summary["tested_valid_session_records"]==6
    assert summary["tested_guarded_normal_and_shifted"]==12
    assert summary["rejected_corrupt_control"] is True
    assert summary["all_arm_control_payloads_match_python"] is True
    assert summary["arm_control_text_rodata_bytes"]<=4096
    assert summary["emulator_stack_highwater_bytes"]>0
    assert summary["no_hardware_access"] is True

def test_blind_complex_iq_synthetic_six_burst_session_acquisition():
    """Public preamble/sync + 2FSK complex IQ -> unified parser -> voice.

    Synthetic baseband, NOT an independent radio/SDR capture. Neither the
    sync detector nor the control receiver is given true packet contents
    or start times.
    """
    import numpy as np
    from tools.k6_dv_guard33_session import receive_iq

    voice=random.Random(612).randbytes(156)
    s0=Session(BEGIN,TAG,112)
    s1=Session(END,TAG,112,115,2,4)
    bursts=[encode_control(s0)]
    for i in range(4):
        chunk=voice[48*i:48*(i+1)]
        pkt=PROFILE.pack(chunk,msg_id=112+i)[0] + bytes([0x5A,0xA5])
        if i%2==0:
            pkt=(bytes([0xF8,0])+pkt)[:66]
        bursts.append(pkt)
    bursts.append((bytes([0xF8,0])+encode_control(s1))[:66])
    fs,baud=128000,4800
    marker=bytes([0x55])*16+pager.SYNC

    def modulate(data:bytes):
        bits=np.unpackbits(np.frombuffer(marker+data,dtype=np.uint8))
        n=(len(bits)*fs+baud-1)//baud
        index=(np.arange(n,dtype=np.int64)*baud//fs).clip(max=len(bits)-1)
        freq=np.where(bits[index],+1800.,-1800.)
        phase=np.cumsum(2*np.pi*freq/fs)
        return np.exp(1j*phase).astype(np.complex64)

    # Separate bursts: one on-air control, four Sotto voice and final control.
    # A little additive noise ensures no artificial silent-background lock.
    rng=np.random.default_rng(711)
    iq=np.concatenate([np.zeros(int(fs*.18),dtype=np.complex64)]
                      + [z for p in bursts for z in
                         (modulate(p),np.zeros(int(fs*.13),dtype=np.complex64))])
    noise=(rng.normal(0,.003,len(iq))+1j*rng.normal(0,.003,len(iq)))
    x=receive_iq(iq+noise.astype(np.complex64))
    packets=[f for f in x["frames"] if f["valid"]]
    assert x["frames_detected"]==6, x["frames_detected"]
    assert len(packets)==6
    assert [p["packet_type"] for p in packets]==[
        "control","voice","voice","voice","voice","control"]
    assert [p["offset_bytes"] for p in packets]==[0,2,0,2,0,2]
    result=replay_onair(x,buffer_ms=160)
    assert result["encoded_bytes"]==voice
    assert result["total_codec_frames"]==26
    assert result["concealed_codec_frames"]==0
