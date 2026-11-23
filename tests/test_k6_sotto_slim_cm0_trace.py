"""Cortex-M0 instruction model trace of real incremental slim Sotto C API."""
from pathlib import Path
import csv
import json
import subprocess
import numpy as np
import pytest
from tools.k6_pa11_sotto_queue_sim import simulate,read_cm0_trace

BASE=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-build-20261123')
VENV=BASE/'.venv-cm0/bin/python'
ELF=BASE/'slim.elf'
CM0=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed/cm0/run.py')
HOST=Path('/storage5/dev/flux-mic-digital-voice/sotto/c/fixed/sotto_slim_enc')
REAL=Path('/home/tyler/flux-bench/micvoice-20261123/sotto-slim-cm0-10ms-trace-20261123/arm-cm0-10ms-private-trace.csv')

CAPTURE={'samples':8164,'turns':200,'timing_ticks_100us':10261,
         'measured_sample_rate_hz':7956.33953805672}


@pytest.mark.skipif(not (VENV.exists() and ELF.exists() and CM0.exists() and HOST.exists()),
                    reason='Unicorn virtual environment or ARM bench not available')
def test_real_armv6m_instruction_model_matches_host_on_synthetic_audio(tmp_path):
    t=np.arange(4*320)/8000
    pcm=tmp_path/'pcm.raw'
    pcm.write_bytes(np.rint(4500*np.sin(2*np.pi*660*t)).astype('<i2').tobytes())
    baseline=tmp_path/'host.bit'
    subprocess.run([str(HOST),str(pcm),str(baseline)],check=True,timeout=25,capture_output=True)
    out=tmp_path/'emulated'
    subprocess.run([str(VENV),str(Path(__file__).resolve().parents[1]/'tools/k6_sotto_slim_cm0_trace.py'),
        '--elf',str(ELF),'--pcm',str(pcm),'--cm0-run',str(CM0),
        '--baseline',str(baseline),'--out-dir',str(out)],check=True,timeout=120,capture_output=True)
    assert (out/'arm-cm0-encoded-private.bit').read_bytes()==baseline.read_bytes()
    r=json.loads((out/'arm-cm0-summary.json').read_text())
    assert r['calls_10ms']==16 and r['encoded_frames']==4
    assert r['codec_state_bytes']==3148
    assert r['emulator_measured_stack_bytes']>=676
    assert r['radio_access'] is False
    assert r['external_spi_not_emulated'] is True
    rows=list(csv.DictReader((out/'arm-cm0-10ms-private-trace.csv').open()))
    assert len(rows)==16
    assert all(int(x['codec_cycles'])>0 for x in rows)


def test_saved_real_mic_104_call_arm_cycle_trace_drives_buffer_model():
    if not REAL.exists():pytest.skip('Private K6 mic Cortex-M0 trace not available')
    cycles,reads=read_cm0_trace(REAL)
    assert len(cycles)==104 and len(reads)==104
    assert sum(reads)==3542 and max(reads)==191
    assert max(cycles)==312662
    a=simulate(CAPTURE,turns=90,buffer_count=2,pattern='even',blocks=104,
               codebook_reads=reads,codec_cycles=cycles)
    b=simulate(CAPTURE,turns=90,buffer_count=3,pattern='even',blocks=104,
               codebook_reads=reads,codec_cycles=cycles)
    assert not a['no_buffer_overrun'] and a['first_buffer_overrun_block']==5
    assert b['no_buffer_overrun']
    assert b['completed_10ms_blocks']==104
    assert b['codec_workload_source']=='real_mic_slim_encode10_cortex_m0_instruction_emulator'
    assert b['spi_read_workload_source']=='actual_recorded_mic_slim_encode10_read_counts'
