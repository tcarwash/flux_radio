"""Compare successful vs failing FSK control path, without exposing audio."""
from pathlib import Path
import pytest
from tools.k6_fsk_tx_source_parity import PASSED,FAILED,compare,signature


def test_real_sources_agree_on_native_fsk_control_sequence():
    if not (PASSED.exists() and FAILED.exists()):pytest.skip('Failed private source not on this bench')
    r=compare()
    assert r['control_path_signatures_equal']
    assert all(r['matched_fields'].values())
    assert r['success_frame_count']==8 and r['failed_frame_count']==4
    assert not r['captured_voice_bytes_logged']
    assert r['no_hardware_access']


def test_signature_does_not_expose_armed_voice_word_array():
    s=signature(PASSED)
    assert s['voice_bytes_excluded_from_signature']
    assert 'sotto_packet_words' not in str(s)
    assert len(s['register_writes'])>=17
    assert s['register_writes'][0]==('R_CONTROL','0x00f8u')


def test_detects_control_change_in_mirror_copy(tmp_path):
    source=PASSED.read_text()
    target=tmp_path/'fake.c'
    target.write_text(source.replace('W(R_CONTROL,0x80f8u);','W(R_CONTROL,0x80f0u);'))
    a,b=signature(PASSED),signature(target)
    assert a['register_writes']!=b['register_writes']
