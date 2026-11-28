"""A/B physical microphone gain-register-write diagnostic."""
from pathlib import Path
import re
import subprocess
from modem import k6mod

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'c/k6/mod/sotto_mic_ab.c'


def test_arm_pic_module_and_safety_guards():
    s=SRC.read_text()
    assert 'K6_MODULE_NAME("MIC A/B")' in s
    assert 'gains[NGAINS]={31,31,8}' in s
    assert 'i!=0 && a->fd_write(0x7d,set)<0' in s
    assert 'if(started)a->mic_stream(1,0,0)' in s
    assert 'a->tx_off()' in s
    assert 'if(ret)return ret;' in s
    assert not re.search(r'\ba->(tx_pa|fsk_async|fl_prog|fl_erase)\s*\(',s)
    code,entry=k6mod.build(str(SRC))
    assert k6mod.bin_entry(code)==entry
    assert k6mod.module_name(code)=='MIC A/B'
    assert len(code)<6000


def test_real_c_module_baseline_vs_write_and_denial(tmp_path):
    exe=tmp_path/'mic_ab_host'
    fixture=ROOT/'tests/data/k6/sotto_mic_ab_host.c'
    subprocess.run(['clang','-O2','-std=c11',str(fixture),'-lm','-o',str(exe)],
                   check=True,capture_output=True,text=True)
    x=subprocess.run([str(exe)],check=True,capture_output=True,text=True,timeout=15)
    assert x.stdout.count('stage=')==3
    assert 'SWEEP + CLEANUP PASS' in x.stdout
    denial=subprocess.run([str(exe),'deny'],check=True,capture_output=True,text=True,timeout=15)
    assert 'GAIN REJECTION CLEANUP PASS' in denial.stdout
