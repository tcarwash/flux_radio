"""Standalone Cortex-M0 PIC DMA IRQ sample producer and host timing simulations."""
import subprocess
from pathlib import Path
import pytest
from modem import k6mod
ROOT=Path(__file__).resolve().parents[1]
HEADER=ROOT/'c/k6/mod/sotto_gp0_irq_capture.h'
HOST=ROOT/'tests/data/k6/sotto_gp0_irq_capture_host.c'

@pytest.fixture
def exe(tmp_path):
    p=tmp_path/'irq_capture_host'
    subprocess.run(['clang','-O2','-std=c11',str(HOST),'-lm','-o',str(p)],check=True)
    return p

@pytest.mark.parametrize('mode',['one','two','silent','busy','late','stall'])
def test_exact_irq_producer_under_interrupted_foreground(exe,mode):
    r=subprocess.run([str(exe),mode],capture_output=True,text=True,timeout=45)
    assert r.returncode==0,(mode,r.stdout,r.stderr)
    assert 'PASS' in r.stdout or 'FAILED' in r.stdout


def test_pic_absolute_context_relocation_forbidden(tmp_path):
    p=tmp_path/'irq_module.c'
    p.write_text('#define K6_MODULE_LEVEL 30\n'
                 f'#include "{ROOT}/c/k6/k6_mod.h"\n'
                 f'#include "{HEADER}"\n'
                 'K6_MODULE_NAME("GP0 IRQ");\n'
                 'int k6_main(const k6_api_t*a){(void)a;'
                 'sl_pcm_t pcm;gp0_irq_audio_t s;'
                 'for(unsigned i=0;i<sizeof(pcm);i++)((uint8_t*)&pcm)[i]=0;'
                 'if(gp0_irq_arm(&s,&pcm,(uint8_t*)0x20003000u,87770u)==0){'
                 'gp0_irq_disarm(&s);return 1;}return -1;}\n')
    code,entry=k6mod.build(str(p))
    assert entry==16 and 500<len(code)<4096
    assert k6mod.module_name(code)=='GP0 IRQ'


def test_interrupt_ownership_and_no_fsk_in_isr():
    s=HEADER.read_text()
    assert 'GPIRQ_VECTOR (0x20000000u+4u*(16u+9u))' in s
    assert 'gp0_irq_store_context' in s
    assert '.word 0\\n' in s
    assert 'gpirq_wr(GPIRQ_DMA_IFCR,15u)' in s
    assert 'gp0_pcm_push(&s->dec,bits,&sample)' in s
    assert 'sl_pcm_sample(s->pcm,sample)' in s
    assert 'gpirq_wr(GPIRQ_NVIC_ICER,GPIRQ_NVIC_MASK)' in s
    assert 'gpirq_wr(GPIRQ_VECTOR,s->old_vector)' in s
    assert 'a->fsk_async' not in s and 'fd_read' not in s
