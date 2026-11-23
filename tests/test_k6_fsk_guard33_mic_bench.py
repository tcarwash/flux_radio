"""FSK-14 private mic Sotto guard finite RF preflight and exact scoring."""
from pathlib import Path
import subprocess
import pytest
from tools.k6_fsk_guard33_mic_bench import frozen_build,SOURCE,MANIFEST,PACKETS
from tools.k6_fsk_guard33_mic_score import assess

LOCAL_PRIVATES=(SOURCE,MANIFEST,PACKETS)


@pytest.mark.skipif(not all(p.exists() for p in LOCAL_PRIVATES),reason='original private K6 mic source is not present')
def test_frozen_mic_transmitter_compiled_hash_and_crc():
    frozen=frozen_build()
    assert frozen['compiled_module_bytes']==1206
    assert frozen['packet_count']==4
    assert frozen['words_per_frame']==33
    assert len(frozen['expected_packet_hashes'])==4
    assert frozen['packet_archive_sha256'].startswith('d81956')


def test_no_rf_without_explicit_exclusive_operator_lease():
    p=subprocess.run(['python3','tools/k6_fsk_guard33_mic_bench.py',
                      '--prefix','/tmp/fsk14-refused-unarmed'],
                     capture_output=True,text=True,timeout=15)
    assert p.returncode!=0
    assert 'exclusive operator-approved' in p.stderr.lower()


@pytest.mark.skipif(not all(p.exists() for p in LOCAL_PRIVATES),reason='original private K6 mic source is not present')
def test_all_four_crc_valid_and_guarded_offsets_pass():
    hashes=frozen_build()['expected_packet_hashes']
    frames=[dict(valid=True,ambiguous=False,offset_bytes=(i%2)*2,
                 sync_errors=0,selected_baud=4800,
                 msg_id=93,seq=i,total_codec_frames=26,codec_frames=8,
                 packet_sha256=h) for i,h in enumerate(hashes)]
    rx=dict(frames_detected=4,crc_valid=4,crc_ambiguous=0,frames=frames)
    scored=assess(rx,hashes)
    assert scored['result']=='STRICT_RX_PASS'
    assert scored['original_mic_audio_packet_bits_checked']==2048
    assert scored['packet_offsets']=={'normal':2,'prefix_displaced_guard':2}
    modified=dict(rx,frames=[dict(r) for r in frames])
    modified['frames'][2]['packet_sha256']='0'*64
    assert assess(modified,hashes)['result']=='FAIL_OR_INCONCLUSIVE'
    modified=dict(rx,frames=[dict(r) for r in frames])
    modified['frames'][3]['seq']=0
    assert assess(modified,hashes)['result']=='FAIL_OR_INCONCLUSIVE'
    modified=dict(rx,frames=[dict(r) for r in frames])
    modified['frames'][1]['total_codec_frames']=32
    assert assess(modified,hashes)['result']=='FAIL_OR_INCONCLUSIVE'


@pytest.mark.skipif(not all(p.exists() for p in LOCAL_PRIVATES),reason='original private K6 mic source is not present')
def test_eight_packet_public_fsk13_capture_does_not_masquerade_as_mic():
    path=Path('/home/tyler/flux-bench/tx-injection-20261122/fsk13-guard33/fsk13-guard33-analysis-run01.json')
    if not path.exists():pytest.skip('original FSK13 public IQ analysis unavailable')
    import json
    rx=json.loads(path.read_text())
    assert assess(rx,frozen_build()['expected_packet_hashes'])['result']=='FAIL_OR_INCONCLUSIVE'
