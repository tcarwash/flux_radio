"""MIC CAL safely measures real PA11 PCM before encoder tuning."""
from pathlib import Path
from modem import k6mod

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_cal.c'

def test_mic_cal_is_bounded_pa_muted_read_only():
    s=SRC.read_text()
    assert 'K6_MODULE_NAME("MIC CAL")' in s
    assert '#define K6_MODULE_LEVEL 30' in s
    assert '_Static_assert(sizeof(pcm_ring_t)==644' in s
    assert 'a->tx_on(2000u)' in s
    assert 'a->mic_stream(0,r,sizeof(*r))' in s
    assert 'a->mic_stream(1,0,0)' in s
    assert 'a->tx_off()' in s
    assert 'r->consumed=(uint8_t)(r->consumed+1)' in s
    assert 'r->lost' in s
    assert 'a->lcd_flush()' in s
    assert not any(token in s for token in ('->tx_pa(1)', '->fsk_async', '->fl_prog', '->fl_erase'))

def test_mic_cal_compiles_small_independent_module():
    code,entry=k6mod.build(str(SRC))
    assert k6mod.bin_entry(code)==entry
    assert k6mod.module_name(code)=='MIC CAL'
    assert len(code)<6000, len(code)
