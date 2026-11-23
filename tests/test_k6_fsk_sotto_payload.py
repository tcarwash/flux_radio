"""FSK-11 frozen real Sotto packets and offline pilot RF bit extraction."""
import hashlib
import json
from pathlib import Path
import numpy as np
from tools.k6_fsk_sotto_payload_bench import frozen_build,MANIFEST,SOURCE
from tools.k6_fsk_sotto_payload_analyze import expected_packets,rf_recover_bits
from modem import pager


def test_armed_source_and_packets_frozen():
    result=frozen_build()
    assert result['code_bytes']<4096
    assert result['source_sha256']==hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    payloads,prof,m=expected_packets()
    assert len(payloads)==2
    assert all(prof.unpack(p) is not None for p in payloads)
    assert [prof.unpack(p)['seq'] for p in payloads]==[0,1]
    assert [len(prof.unpack(p)['frames']) for p in payloads]==[8,8]


def test_sotto_true_encoded_sample_matches_frozen_packets():
    m=json.loads(MANIFEST.read_text())
    sample_path=Path('/storage5/dev/flux-mic-digital-voice/sotto/docs/samples/rates/male_libri3436_15s__1200.bit')
    # Local material exists for this conducted research fixture. Manifest
    # and packet CRC are still sufficient when another host lacks the sample.
    payloads,prof,_=expected_packets()
    rx=pager.Assembler(prof)
    for pkt in payloads:rx.push(pkt)
    x,good=rx.frames(33)
    assert good.sum()==16
    if sample_path.exists():
        sample=sample_path.read_bytes()
        assert hashlib.sha256(sample).hexdigest()==m['source_sha256']
        assert x[:96]==sample[:96]


def test_corruption_fails_existing_crc():
    payloads,prof,_=expected_packets()
    p=bytearray(payloads[0]);p[40]^=1
    assert prof.unpack(p) is None


def test_recovery_from_synthetic_complex_fsk_training_fit():
    payloads,_,_=expected_packets()
    fs=128000
    t0=0.25
    true_bits=np.unpackbits(np.frombuffer(payloads[0],dtype=np.uint8))
    n=int(.7*fs)
    idx=np.floor((np.arange(n)/fs-t0)*4800).astype(int)
    bits=true_bits[np.clip(idx,0,511)]
    freq=np.where(bits,1200.,-1200.)
    iq=np.exp(2j*np.pi*np.cumsum(freq)/fs)
    fit=dict(locked=True,training_bits=256,candidate_baud_estimate=4800.,
        detected_first_payload_bit_center_s=t0+.5/4800.,
        median_training_frequency_bit0_hz_relative=-1200.,
        median_training_frequency_bit1_hz_relative=1200.)
    result=rf_recover_bits(iq,fit)
    assert np.array_equal(result,true_bits)
