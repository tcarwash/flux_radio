"""Safety regression for fresh FSK09S-H timeout evidence collection.

Physical execution is NOT part of pytest; only the exact source and compiled
module are checked. Thumb/Unicorn exercises run separately using previous
independent emulator harness with this source path.
"""
from k6_fsk09s_packed_bench import preflight_source,SOURCE_INERT

def test_new_timeout_and_partial_report_are_bounded():
    code=SOURCE_INERT.read_text()
    assert '#define FSK_FRAME_TIMEOUT_MS 700u' in code
    assert '#define FSK_LEASE_MS 1900u' in code
    assert 'attempted_frames=k+1u;' in code
    assert 'if(restore && ret!=-15)' in code
    assert 'for(k=0;k<attempted_frames;k++)report(api,k,&trace[k]);' in code
    assert code.index('api->tx_off();')<code.index('if(restore && ret!=-15)')
    assert 'if(ret!=2*(int)FSK_WORDS)api->say("FSK09S H aborted. RX restored.");' in code
    assert 'W(R_FIFO,i==48u?0xa55au:fsk_lab_words[i]);' in code
    assert 'W(R_LENGTH,0x6100u)' in code

def test_exact_pinned_new_module_is_still_small_and_level3():
    d=preflight_source()
    assert d['code_bytes']==1710 and d['entry']==0 and d['level']==3
    assert d['code_sha256']=='1ceb9d27ac8597a1f5b686e9e64581f8939d552a0879a0e0aa8d33f63195df39'
