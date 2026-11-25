"""Codec-compatible streaming 8kHz PCM decimation at measured GP0 DMA clocks."""
import re
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
ROOT=Path(__file__).resolve().parents[1]
HDR=ROOT/'c/k6/mod/sotto_gp0_pcm.h'
HOST=ROOT/'tests/data/k6/sotto_gp0_pcm_host.c'

@pytest.fixture
def exe(tmp_path):
    p=tmp_path/'gp0_pcm'
    subprocess.run(['clang','-std=c11','-O2',str(HOST),'-lm','-o',str(p)],check=True)
    return p

@pytest.mark.parametrize('sample_rate',[84960,87770,85000,110000])
@pytest.mark.parametrize('freq',[0,1000,2000,2600])
def test_8khz_pcm_with_2s_of_signal(exe,sample_rate,freq):
    result=subprocess.run([str(exe),str(sample_rate),str(freq)],
                          text=True,capture_output=True,timeout=10,check=True)
    assert 'samples=16000' in result.stdout
    assert 'clipped=0' in result.stdout


def test_input_to_pcm_contract():
    s=HDR.read_text()
    assert 'gp0_pcm_init' in s and 'gp0_pcm_push' in s
    assert 'uint32_t fs' in s
    assert 'uint32_t samples' in s and 'uint32_t clipped' in s
    assert 'int16_t*out' in s
    assert 'gp0_pcm_udiv' in s
    assert '#include <stdlib.h>' not in s
    assert not re.search(r'\bfmod\(|\bsin\(|\bcos\(',s)


def test_cross_compiles_for_real_cortex_m0(tmp_path):
    module=tmp_path/'gp0_pcm_compile.c'
    module.write_text('#define K6_MODULE_LEVEL 30\n#include "'+str(ROOT/'c/k6/k6_mod.h')+'"\n'
                      '#include "'+str(HDR)+'"\n'
                      'K6_MODULE_NAME("GP0 PCM");\n'
                      'int k6_main(const k6_api_t*a){(void)a;gp0_pcm_t x;'
                      'int16_t y=0;gp0_pcm_init(&x,87770);'
                      'for(int i=0;i<100;i++)gp0_pcm_push(&x,(unsigned)(i&63),&y);'
                      'return (int)x.samples+(int)y;}\n')
    code,entry=k6mod.build(str(module))
    assert entry==16 and len(code)>100
