"""Run real PA11 GPIO source as compiled Thumb ARMv6-M, not a Python reimplementation."""
from pathlib import Path
import json
import subprocess
import pytest
from tools.k6_pa11_armv6m_emulate import run
from tools.k6_pa11_sotto_queue_sim import read_pa11_cycles,read_cm0_trace,simulate

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'tools/k6_pa11_armv6m_probe.c'
PA11=Path('/storage5/dev/flux-mic-digital-voice/c/k6/mod')
SOTTO=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed')
VENV=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123/.venv-cm0/bin/python')
PRIVATE=Path('/home/tyler/flux-bench/micvoice-20261123')
CAPTURE={'samples':8164,'turns':200,'timing_ticks_100us':10261,
         'measured_sample_rate_hz':7956.33953805672}


@pytest.mark.skipif(not (VENV.exists() and PA11.exists() and SOTTO.exists()),
                    reason='ARMv6-M emulator or original K6 PA11 source absent')
def test_compiled_actual_pa11_gpio_cortex_m0_cycles(tmp_path):
    import subprocess
    resource=subprocess.check_output(['clang','-print-resource-dir'],text=True).strip()
    obj=tmp_path/'pa11.o';elf=tmp_path/'pa11.elf'
    subprocess.run(['clang','--target=thumbv6m-none-eabi','-mcpu=cortex-m0','-mthumb',
        '-mfloat-abi=soft','-nostdinc','-isystem',resource+'/include',
        '-I',str(PA11),'-Oz','-ffunction-sections','-fdata-sections',
        '-c',str(SOURCE),'-o',str(obj)],check=True,timeout=30,capture_output=True)
    subprocess.run(['ld.lld','-T',str(SOTTO/'cm4/link.ld'),'--gc-sections',
        '-e','pa11_probe','-u','pa11_probe',str(obj),'-o',str(elf)],
        check=True,timeout=30,capture_output=True)
    result=tmp_path/'result.json'
    subprocess.run([str(VENV),str(ROOT/'tools/k6_pa11_armv6m_emulate.py'),
        '--elf',str(elf),'--cm0-run',str(SOTTO/'cm0/run.py'),
        '--out',str(result)],check=True,timeout=60,capture_output=True)
    r=json.loads(result.read_text())
    a={(x['idr_pa11'],x['turns']):x for x in r['scenarios']}
    assert r['radio_access'] is False
    for w,cycles in [(80,5445),(90,6115),(98,6651),(100,6785),(110,7455),(120,8125),(200,13485)]:
        assert a[(0,w)]['cycles']==cycles
        assert a[(1,w)]['cycles']==cycles
    assert a[(0,90)]['remaining_us_in_125us']==pytest.approx(61.302,abs=.01)
    assert a[(0,200)]['remaining_us_in_125us']<0
    assert a[(0,90)]['adc_return_value']==0
    assert a[(1,90)]['adc_return_value']==4095


def test_two_armv6m_traces_show_four_buffers_for_this_clip():
    pa11=PRIVATE/'pa11-m0-emulation-20261123/pa11-armv6m-cycles.json'
    codec=PRIVATE/'sotto-slim-cm0-10ms-trace-20261123/arm-cm0-10ms-private-trace.csv'
    if not (pa11.exists() and codec.exists()):pytest.skip('Private dual Cortex-M0 traces unavailable')
    adc=read_pa11_cycles(pa11);cyc,reads=read_cm0_trace(codec)
    assert adc[90]==6115 and adc[80]==5445
    assert len(cyc)==104 and sum(reads)==3542
    for w in (80,90):
        b3=simulate(CAPTURE,turns=w,buffer_count=3,pattern='even',blocks=104,
                    codebook_reads=reads,codec_cycles=cyc,adc_cycles_per_sample=adc[w])
        b4=simulate(CAPTURE,turns=w,buffer_count=4,pattern='even',blocks=104,
                    codebook_reads=reads,codec_cycles=cyc,adc_cycles_per_sample=adc[w])
        assert not b3['no_buffer_overrun']
        assert b4['no_buffer_overrun']
        assert b4['completed_10ms_blocks']==104
        assert b4['adc_workload_source']=='real_pa11_gpio_armv6m_instruction_emulator'
    b100=simulate(CAPTURE,turns=100,buffer_count=6,pattern='even',blocks=104,
        codebook_reads=reads,codec_cycles=cyc,adc_cycles_per_sample=adc[100])
    assert not b100['no_buffer_overrun']
    assert b100['completed_10ms_blocks']<104
