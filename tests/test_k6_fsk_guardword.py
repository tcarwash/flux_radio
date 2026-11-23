"""Guard-word L64 finite-FSK modem mitigation, OFFLINE only."""
from pathlib import Path
import numpy as np
from modem import pager
from tools.k6_fsk_guardword_rx import GUARD_WORD, select_l64, receive
from tests.test_k6_fsk_sotto_sync_rx import synth_capture
from tools.k6_sotto_fsk_offline import source_frames
import pytest


def sample_packets():
    profile=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    return profile.pack(source_frames(24),msg_id=99)


def wrap(packet: bytes, artifact: bool) -> bytes:
    assert len(packet)==64
    guard=GUARD_WORD.to_bytes(2,'little')
    # The modem is programmed for 33 words and 33 are preloaded:
    # no anomaly: whole L64 packet + guard
    # prefix anomaly: 0x00F8 + whole L64 packet; hardware drops guard
    return (b'\xf8\x00'+packet) if artifact else packet+guard


def test_valid_packet_with_and_without_prefix():
    packets=sample_packets()
    assert len(packets)==3
    for i,p in enumerate(packets):
        for prefix in (False,True):
            result=select_l64(wrap(p,prefix))
            assert result['valid']
            assert result['offset_bytes']==(2 if prefix else 0)
            assert result['displaced_prefix']==prefix
            assert result['msg_id']==99 and result['seq']==i
            assert bytes.fromhex(result['packet_hex'])==p


def test_corrupted_crc_never_accepted():
    p=sample_packets()[0]
    for prefix in (False,True):
        frame=bytearray(wrap(p,prefix))
        frame[10]^=0x80
        result=select_l64(bytes(frame))
        assert not result['valid']


def test_reject_wrong_length():
    with pytest.raises(ValueError):
        select_l64(sample_packets()[0])


def test_synthetic_air_framing_recovers_mixed_prefix_modes():
    pkts=sample_packets()
    rf=[wrap(pkt,i%2==1) for i,pkt in enumerate(pkts)]
    iq=synth_capture(rf,seed=13,noise=.035)
    result=receive(iq)
    assert result['frames_detected']==len(pkts)
    assert result['crc_valid']==len(pkts)
    assert result['crc_ambiguous']==0
    assert [f['offset_bytes'] for f in result['frames']]==[0,2,0]
    assert [bytes.fromhex(f['packet_hex']) for f in result['frames']]==pkts


def test_noise_no_false_crc():
    rng=np.random.default_rng(779)
    iq=rng.standard_normal(80000)+1j*rng.standard_normal(80000)
    result=receive(iq)
    assert result['crc_valid']==0


def test_guard_not_whitening_or_part_of_original_l64():
    assert GUARD_WORD not in (0x00f8,0x5555)
    p=sample_packets()[0]
    assert select_l64(p+GUARD_WORD.to_bytes(2,'little'))['valid']
