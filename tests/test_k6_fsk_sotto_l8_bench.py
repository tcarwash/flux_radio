"""Pre-registered eight-frame native-FSK Sotto test source; never TX."""
import hashlib
import json
from pathlib import Path
from tools.k6_fsk_sotto_l8_bench import frozen_build, SOURCE, MANIFEST
from modem import pager


def test_eight_packet_armed_image_frozen():
    r=frozen_build()
    assert 0<r['code_bytes']<4096
    assert r['source_sha256']==hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def test_eight_packet_sequence_reconstructs_true_source():
    m=json.loads(MANIFEST.read_text())
    p=pager.profile('L','1200',depth=1,parity=False,short_last=True)
    packets=[bytes.fromhex(x) for x in m['payloads_hex']]
    assert len(packets)==8
    assembled=pager.Assembler(p)
    for data in packets:assembled.push(data)
    frames,good=assembled.frames(m['message_id'])
    assert good[:64].all()
    assert good.sum()==64
    sample=Path(m['sotto_source'])
    if sample.is_file():
        raw=sample.read_bytes()
        assert hashlib.sha256(raw).hexdigest()==m['sotto_source_sha256']
        assert frames[:64*6]==raw[:64*6]


def test_airtime_bounded_by_existing_firmware_lease():
    from tools.k6_sotto_fsk_offline import RF_RESTART_S_L
    nominal=8*(512/4800 + RF_RESTART_S_L)
    assert nominal<1.3
    assert '#define FSK_MAX_TX_MS 1900u' in SOURCE.read_text()
    assert '#define FSK_NUM_FRAMES 8u' in SOURCE.read_text()
