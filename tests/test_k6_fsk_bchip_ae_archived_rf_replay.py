"""Supporting archived REAL RF replay; does NOT replace target emulation or new RF.

The historical FSK09S-C capture uses a different 49th word (not our 0xA55A
sacrificial guard) and a different payload in its second frame, so it MUST
fail the newly frozen FSK09S-H strict payload score. This exercises framing
on real complex SDR samples and verifies the receiver refuses relabeling.
"""
from pathlib import Path
import hashlib
import pytest
from tools.k6_fsk_iq_edges import read_iq
from tools.k6_fsk_bchip_ae_analyze import score_iq

IQ=Path('/home/tyler/flux-bench/tx-injection-20261122/fsk09s-oneword/fsk09s-oneword-run01.npy')
SHA='7981d591aa03d028f99c6db89e33fa0e13964acf881251951ead62bb2f547ff4'


def test_blind_49word_iq_replay_detects_historical_frames_but_rejects_wrong_fixture():
    if not IQ.is_file():pytest.skip('Historical physical IQ only available on original bench')
    assert hashlib.sha256(IQ.read_bytes()).hexdigest()==SHA
    rf=read_iq(IQ,duration=2.0,offset=280500.)
    result=score_iq(rf)
    assert result['frames_found']==2
    assert len(result['frames'])==2
    assert all(f['sync_correlation']>.99 for f in result['frames'])
    assert result['result']=='RF_PAYLOAD_FAIL'
    assert not any(f['strict_pass'] for f in result['frames'])
    # Historical frame0's first 48 words match the new public vector,
    # but its frame49 was NOT the prospective 0xA55A guard.
    assert result['frames'][0]['data_payload_sha256']==result['expected_48word_data_sha256']
    # Historical frame1 used an independent source vector, not FSK09S-H.
    assert result['frames'][1]['data_payload_sha256']!=result['expected_48word_data_sha256']
