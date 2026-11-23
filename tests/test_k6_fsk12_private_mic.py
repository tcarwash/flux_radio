"""No-radio regression tests for PRIVATE FSK-12 microphone-origin RF fixture."""
import json
from pathlib import Path
import numpy as np
import pytest
from tools.k6_fsk12_mic_bench import (frozen_build,MANIFEST,FIXTURE,SOURCE,SOURCE_PACKETS)
from tools.k6_fsk12_private_fixture import attest_packets,source_from_packets,sha
from tools.k6_fsk12_mic_rx import score_iq
from tests.test_k6_fsk_sotto_sync_rx import synth_capture
from modem import pager



def test_public_manifest_contains_no_voice_bytes_or_packet_hex():
    m=json.loads(MANIFEST.read_text())
    assert m['private_payloads_not_committed'] is True
    assert m['hardware_lease_status']=='NOT_CLAIMED'
    assert m['physical_test_status']=='NOT_EXECUTED'
    assert len(m['per_packet_sha256'])==4
    assert all(len(x)==64 and all(c in '0123456789abcdef' for c in x) for x in m['per_packet_sha256'])
    assert 'packet_hex' not in m and 'payloads_hex' not in m and 'voice_bytes' not in m


def test_private_payloads_crc_and_code_match_frozen_hashes():
    if not SOURCE.exists():pytest.skip('Private mic audio is unavailable on this host')
    x=frozen_build()
    assert x['crc_valid']==4 and x['voice_superframes']==26
    assert x['compiled_code_bytes']==1206
    packets,compressed=attest_packets(SOURCE_PACKETS.read_bytes())
    assert len(packets)==4 and len(compressed)==26*6
    assert sha(compressed)==json.loads(MANIFEST.read_text())['mic_sotto_156_byte_compressed_sha256']
    # Rebuilding source content in memory must match the existing private image.
    generated=source_from_packets(packets)
    assert generated.replace('#define FSK_BOUNDARY_ENABLE 0','#define FSK_BOUNDARY_ENABLE 1').replace(
           '#define FSK_BOUNDARY_FIXTURE_ACK 0','#define FSK_BOUNDARY_FIXTURE_ACK 1').replace(
           '#define FSK_BOUNDARY_WATCHDOG_ACK 0','#define FSK_BOUNDARY_WATCHDOG_ACK 1')==SOURCE.read_text()


def test_synthetic_four_packets_blind_receiver_crc_and_compressed_match():
    if not SOURCE.exists():pytest.skip('Private mic audio is unavailable on this host')
    packets=[SOURCE_PACKETS.read_bytes()[64*i:64*(i+1)] for i in range(4)]
    rf=synth_capture(packets)
    result,bytes_rx,good=score_iq(rf,json.loads(MANIFEST.read_text()))
    assert result['result']=='PASS'
    assert result['crc_valid_frames']==4
    assert result['packet_sequences']==[0,1,2,3]
    assert len(bytes_rx)==156 and len(good)==26 and all(good)


def test_synthetic_single_corrupted_frame_gets_rejected():
    if not SOURCE.exists():pytest.skip('Private mic audio is unavailable on this host')
    stream=SOURCE_PACKETS.read_bytes()
    packets=[bytearray(stream[64*i:64*(i+1)]) for i in range(4)]
    packets[2][30]^=0x80
    rf=synth_capture([bytes(x) for x in packets])
    result,_,_=score_iq(rf,json.loads(MANIFEST.read_text()))
    assert result['result']=='FAIL_OR_INCONCLUSIVE'
    assert result['crc_valid_frames']==3
    assert result['packet_sequences']==[0,1,3]


def test_bench_requires_three_explicit_operator_confirmations():
    import subprocess,sys
    p=subprocess.run([sys.executable,'tools/k6_fsk12_mic_bench.py','--prefix','/tmp/NEVER_RAN_HARDWARE'],capture_output=True,text=True)
    assert p.returncode!=0
    assert 'dummy load + operator bench lease + explicit experiment queue' in p.stderr
