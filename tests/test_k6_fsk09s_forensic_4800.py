"""Saved-IQ 4800-bps fixed-clock forensic evidence; never touches radio."""
from pathlib import Path
import pytest
from tools.k6_fsk09s_forensic_4800 import FILES,analyze_capture,expected


def test_source_truth_vectors_are_full_frames():
    for case,(_,bytes_per_frame) in FILES.items():
        truth=expected(case)
        assert len(truth)==(2 if case=='C' else 1)
        assert all(len(x)==bytes_per_frame for x in truth)


def test_previously_recorded_four_cases_fixed_clock():
    if not all(path.is_file() for path,_ in FILES.values()):
        pytest.skip('Saved physical IQ not available outside conducting bench')
    results={k:analyze_capture(k,*v) for k,v in FILES.items()}
    assert [results[k]['detected_frames'] for k in 'CDEF']==[2,1,1,1]
    assert all(frame['acquired_baud']==4800 for r in results.values() for frame in r['frames'])
    assert all(r['acquisition_uses_payload_truth'] is False and r['no_hardware_operation']
               for r in results.values())
    # Two independent genuine passes: C fully-preloaded control and D 48+1.
    assert results['C']['frames'][0]['strict_full_frame_payload_bit_errors']==0
    assert results['D']['frames'][0]['strict_full_frame_payload_bit_errors']==0
    # Three negative strict tests. After one-word shift, all remaining
    # payload words match EXACTLY, including actual mid-TX words.
    for case,frame_idx,words in [('C',1,48),('E',0,63),('F',0,59)]:
        r=results[case]['frames'][frame_idx]
        assert r['first_word_le']=='f800'
        assert r['strict_full_frame_payload_bit_errors']>0
        assert r['posthoc_one_word_shift_bit_errors']==0
        assert r['posthoc_one_word_shift_matching_words']==words
        assert not r['posthoc_accepted_as_pass']
