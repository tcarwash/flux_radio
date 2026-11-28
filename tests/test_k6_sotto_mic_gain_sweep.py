"""A separate no-FSK per-gain test module; preserve Sotto MIC slot30."""
from pathlib import Path
import re
from modem import k6mod

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_gain_sweep.c'
CAL=ROOT/'c/k6/mod/sotto_mic_cal.c'
INC=ROOT/'c/k6/mod/sotto_mic_cal_math.inc'


def test_shared_1khz_estimator_identical_to_qualified_mic_cal():
    qualified=CAL.read_text()
    shared=INC.read_text().split('\n',1)[1]
    expected=qualified[qualified.index('static char *cat('):qualified.index('static void message(')]
    assert shared.replace('static __attribute__((unused)) char *signed_num(',
                          'static char *signed_num(')==expected


def test_gain_sweep_bounded_safety_and_hardware_api():
    s=SRC.read_text()
    assert 'K6_MODULE_NAME("MIC SWEEP")' in s
    assert '#define K6_MODULE_LEVEL 30' in s
    assert '_Static_assert(sizeof(pcm_ring_t)==644' in s
    assert 'gains[NGAINS]={31,24,16,8,0}' in s
    assert 'SETTLE_MS=120,MEASURE_MS=520,TX_LEASE_MS=4500' in s
    assert 'a->mic_stream(0,ring,sizeof(*ring))' in s
    assert 'a->fd_write(0x7d,set)' in s
    assert '(a->fd_read(0x7d)&31u)!=gains[i]' in s
    assert 'reg_base|gains[i]' in s
    assert 'a->mic_stream(1,0,0)' in s
    assert 'a->tx_off()' in s
    assert 'if(ring->lost){ret=-10;break;}' in s
    assert 'a->mic_stream(3,0,0)!=1' in s
    assert 'if(ret)return ret;' in s
    assert 'show(a,results,0)' in s
    assert s.index('a->mic_stream(1,0,0)')<s.index('show(a,results,0)')
    assert not re.search(r'\ba->(tx_pa|fsk_async|fl_prog|fl_erase)\s*\(',s)
    assert not re.search(r'\ba->fd_write\(0x(?:30|33|36|37)',s)


def test_gain_sweep_compiles_as_pic_arm_module():
    code,entry=k6mod.build(str(SRC))
    assert k6mod.bin_entry(code)==entry
    assert k6mod.module_name(code)=='MIC SWEEP'
    assert len(code)<6000


def test_gain_sweep_real_c_module_with_mock_isr_and_radio(tmp_path):
    """Actual module C in a host harness: all gains, 1k, RF/cleanup/rejection."""
    import subprocess
    fixture=ROOT/'tests/data/k6/sotto_mic_gain_sweep_host.c'
    exe=tmp_path/'mic_gain_sweep_host'
    subprocess.run(['clang','-O2','-std=c11',str(fixture),'-lm','-o',str(exe)],
                   check=True,capture_output=True,text=True)
    run=subprocess.run([str(exe)],check=True,capture_output=True,text=True,timeout=15)
    assert run.stdout.count('gain=')==5
    assert 'SWEEP + CLEANUP PASS' in run.stdout
    denied=subprocess.run([str(exe),'deny'],check=True,capture_output=True,text=True,timeout=15)
    assert 'GAIN REJECTION CLEANUP PASS' in denied.stdout
