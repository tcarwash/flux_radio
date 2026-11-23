"""Strict offline FSK-13 full-eight-packet pass/fail scorer checks."""
from copy import deepcopy
from tools.k6_fsk_guard33_score import assess
from tools.k6_fsk_guard33_bench import frozen_build

def valid_report():
    hashes=frozen_build()['expected_packet_sha256']
    rows=[]
    for i,h in enumerate(hashes):
        rows.append(dict(valid=True,ambiguous=False,offset_bytes=(i%2)*2,
                         sync_errors=0,selected_baud=4800.,
                         msg_id=73,seq=i,codec_frames=8,packet_sha256=h))
    return dict(frames_detected=8,crc_valid=8,crc_ambiguous=0,frames=rows)

def test_mixed_prefix_receiver_can_strictly_pass():
    result=assess(valid_report(),frozen_build()['expected_packet_sha256'])
    assert result['result']=='STRICT_RX_PASS'
    assert result['frame_offsets']==dict(normal=4,prefix_displaced_guard=4)
    assert result['audio_packet_bits_validated']==4096

def test_corrupted_packet_even_crc_valid_is_rejected():
    data=valid_report()
    data['frames'][5]['packet_sha256']='0'*64
    assert assess(data,frozen_build()['expected_packet_sha256'])['result']=='FAIL_OR_INCONCLUSIVE'

def test_wrong_sequence_is_rejected():
    data=valid_report()
    data['frames'][7]['seq']=0
    assert assess(data,frozen_build()['expected_packet_sha256'])['result']=='FAIL_OR_INCONCLUSIVE'

def test_wrong_frame_count_is_rejected():
    data=valid_report()
    data['frames'].pop()
    assert assess(data,frozen_build()['expected_packet_sha256'])['result']=='FAIL_OR_INCONCLUSIVE'

def test_crc_ambiguity_is_rejected():
    data=valid_report()
    data['crc_ambiguous']=1
    data['frames'][4]['ambiguous']=True
    assert assess(data,frozen_build()['expected_packet_sha256'])['result']=='FAIL_OR_INCONCLUSIVE'

def test_bad_sync_rejected():
    data=valid_report()
    data['frames'][1]['sync_errors']=1
    assert assess(data,frozen_build()['expected_packet_sha256'])['result']=='FAIL_OR_INCONCLUSIVE'
