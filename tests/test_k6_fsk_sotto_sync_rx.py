"""Payload-independent K6 native-FSK packet sync, offline only."""
from pathlib import Path
import numpy as np
from modem import pager
from tools.k6_fsk_sotto_sync_rx import (FS, BAUD, FRAME_MARK, find_frames,
                                         analyze_file)
from tools.k6_sotto_fsk_offline import source_frames

FSK11_IQ=Path('/home/tyler/flux-bench/tx-injection-20261122/fsk11-sotto/fsk11-sotto-run01.npy')
FSK10_IQ=Path('/home/tyler/flux-bench/tx-injection-20261122/fsk10-l64/fsk10-l64-run01.npy')


def synth_capture(payloads, *, seed=3, noise=.03, rate=4800., gap_s=.0115,
                  start_s=.25, fs=FS):
    """Generate synthetic IQ only: ideal FM bits + optional gaussian IQ noise."""
    header=FRAME_MARK
    stream=[np.unpackbits(np.frombuffer(header+p,dtype=np.uint8)) for p in payloads]
    frame_len=len(stream[0])/rate+gap_s
    n=int((start_s+frame_len*len(stream)+.1)*fs)
    freq=np.zeros(n,dtype=np.float64)
    for i,bits in enumerate(stream):
        t_start=start_s+frame_len*i
        start_idx=int(round(t_start*fs))
        # Each bit is sampled by hardware at a continuously timed 4800bps
        # boundary. Quantization here is intentionally at the RF sample rate.
        idx=np.floor(np.arange(n-start_idx)/fs*rate).astype(int)
        active=idx<len(bits)
        count=int(np.count_nonzero(active))
        freq[start_idx:start_idx+count]=np.where(bits[idx[:count]]==1,1150.,-1150.)
    iq=np.exp(2j*np.pi*np.cumsum(freq)/fs)
    if noise:
        rng=np.random.default_rng(seed)
        iq=iq+noise*(rng.standard_normal(n)+1j*rng.standard_normal(n))
    return iq


def test_protocol_marker_is_only_public_preamble_and_sync():
    assert FRAME_MARK==bytes([0x55])*16+bytes.fromhex('076b3e36')


def test_synthetic_eight_unknown_packets_recover_and_crc():
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    payloads=p.pack(source_frames(8*8),msg_id=72)
    assert len(payloads)==8
    result=find_frames(synth_capture(payloads))
    assert result['crc_valid']==8
    assert [c['seq'] for c in result['accepted']]==list(range(8))
    assert all(c['msg_id']==72 for c in result['accepted'])
    assert result['byte_payloads']==payloads


def test_packet_header_changes_not_used_for_sync():
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    payloads=p.pack(source_frames(16),msg_id=49)
    result=find_frames(synth_capture(payloads,seed=88,noise=.10))
    assert result['crc_valid']==2
    assert result['byte_payloads']==payloads


def test_random_iq_noise_has_no_false_crc():
    rng=np.random.default_rng(5678)
    noise=rng.standard_normal(int(.6*FS))+1j*rng.standard_normal(int(.6*FS))
    result=find_frames(noise)
    assert result['crc_valid']==0


def test_real_sotto_capture_blind_crc_acceptance():
    if not FSK11_IQ.exists():return
    a=analyze_file(FSK11_IQ)
    assert a['crc_valid']==2
    assert a['frames_detected']==2
    assert [x['seq'] for x in a['packets']]==[0,1]
    assert all(x['crc_ok'] for x in a['packets'])
    assert a['reconstructed_messages']['33']['codec_frames_good']==16


def test_diagnostic_fsk_capture_blind_sync_but_no_sotto_crc():
    if not FSK10_IQ.exists():return
    a=analyze_file(FSK10_IQ)
    assert a['frames_detected']==2
    assert a['crc_valid']==0
    assert not a['reconstructed_messages']
