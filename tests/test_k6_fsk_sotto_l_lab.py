"""FSK-10 64-byte native FSK protocol/freezer regression; never access hardware."""
import hashlib
from pathlib import Path
from tools.k6_fsk_sotto_l_analyze import truth
from tools.k6_fsk_sotto_l_bench import frozen_build
from modem import pager

ROOT=Path(__file__).resolve().parents[1]

def test_bench_frozen_source_and_compiled_image():
    snap=frozen_build()
    assert snap['compiled_bytes']<4096
    assert len(snap['source_sha256'])==64 and len(snap['code_sha256'])==64

def test_protocol_module_is_inert_by_default():
    orig=(ROOT/'c/k6/mod/fsk_sotto_l_boundary.c').read_text()
    armed=(ROOT/'c/k6/mod/fsk_sotto_l_boundary_armed.c').read_text()
    assert '#define FSK_BOUNDARY_ENABLE 0' in orig
    assert '#define FSK_BOUNDARY_ENABLE 1' in armed
    assert '#define FSK_BOUNDARY_WATCHDOG_ACK 1' in armed
    assert '#define FSK_BOUNDARY_FIXTURE_ACK 1' in armed
    assert '#define FSK_MAX_TX_MS 1900u' in armed
    assert armed.count('W(R_LENGTH,0x3f00u);')==2
    assert '#define FSK_WORDS 32u' in armed
    assert '#define FSK_NUM_FRAMES 2u' in armed
    assert '#define FSK_FREQUENCY_HZ 433500000u' in armed

def test_two_known_frames_have_256_heldout_bits():
    a,b=truth()
    for m in (a,b):
        assert m['payload_words']==32
        assert m['payload_bits']==512
        assert m['native_baud']==4800
        assert len(bytes.fromhex(m['payload_bytes_hex']))==64
        assert hashlib.sha256(bytes.fromhex(m['payload_bytes_hex'])).hexdigest()==m['payload_sha256_le_words']
    assert a['payload_sha256_le_words']!=b['payload_sha256_le_words']
    assert bytes.fromhex(a['payload_bytes_hex'])[-8:]==bytes(8)
    assert bytes.fromhex(b['payload_bytes_hex'])[:8]==bytes(8)

def test_actual_existing_pager_l_is_64_bytes_eight_codec_slots():
    p=pager.profile('L','1200',depth=1,parity=False)
    assert p.payload==64
    assert p.slots==8
    assert p.slots*6==48
