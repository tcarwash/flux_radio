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


def test_mic_cal_coherent_1k_is_phase_and_dc_independent(tmp_path):
    """Compile the actual C estimator on host; not a separately reimplemented formula."""
    import ctypes
    import math
    import subprocess
    src=SRC.read_text()
    math_src=src[src.index('static uint32_t absi('):src.index('static void message(')]
    wrapper='''
    #include <stdint.h>
    uint32_t __aeabi_uidiv(uint32_t a,uint32_t b){return b?a/b:0;}
    '''+math_src+'''
    uint32_t miccal_test_1k(const int32_t *phase,uint32_t cycles){
      return tone_1k_amp(phase,cycles);
    }
    '''
    lib_path=tmp_path/'miccal_math.so'
    subprocess.run(['clang','-std=c11','-shared','-fPIC','-O2',
                    '-x','c','-','-o',str(lib_path)],
                   input=wrapper,text=True,check=True,capture_output=True)
    lib=ctypes.CDLL(str(lib_path))
    lib.miccal_test_1k.argtypes=(ctypes.POINTER(ctypes.c_int32),ctypes.c_uint32)
    lib.miccal_test_1k.restype=ctypes.c_uint32

    def metric(amp,phase=0.0,dc=0):
        s=[0]*8
        # The firmware discards exactly the first 160 of 8000 samples.
        for i in range(160,8000):
            sample=round(dc+amp*math.sin(2*math.pi*(i/8)+phase))
            sample=min(32767,max(-32768,sample))
            s[(i-160)&7]+=sample
        return lib.miccal_test_1k((ctypes.c_int32*8)(*s),980)

    for phase in (0,.2,.8,1.45,2.7):
        assert abs(metric(1200,phase,0)-1200)<=5
        assert abs(metric(1200,phase,9000)-1200)<=5
    assert metric(0,0,0)==0
    assert metric(0,0,15000)==0
    # Same generator frequency, lower level should yield a lower result.
    assert 190<=metric(200,.9,12000)<=205
    assert 990<=metric(1000,.9,12000)<=1005


def test_mic_cal_display_reports_tone_and_clip_without_extra_radio_activity():
    s=SRC.read_text()
    assert 'p=cat(b,"1K=")' in s
    assert 'p=cat(p," CLIP=")' in s
    assert 'if(v==32767||v==-32768)clipped++;' in s
    assert 'phase_sum[tone_samples&7u]+=v;' in s
    assert 'if(samples>=160)' in s
    assert 'tone_1k_amp(phase_sum,tone_samples/8u)' in s
    assert 'a->lcd_text(3,b);a->lcd_flush();second_page=1;' in s
    assert s.index('a->mic_stream(1,0,0)')<s.index('tone_1k_amp(phase_sum')
