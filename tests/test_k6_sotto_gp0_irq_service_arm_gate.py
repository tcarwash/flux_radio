"""Opt-in real linked ARM GP0 DMA IRQ9 start/stop test for TX-only firmware."""
import os
from pathlib import Path
import pytest
from tools.k6_sotto_gp0_irq_service_arm_gate import run
ROOT=Path(__file__).resolve().parents[1]

def test_gp0_irq_service_real_arm_clean_start_and_stop():
    elf=os.environ.get('K6_SIM_SOTTO_GP0_ELF')
    image=os.environ.get('K6_SIM_SOTTO_GP0_IMAGE')
    if not elf or not image or os.environ.get('K6_SIM_SOTTO_GP0')!='1':
        pytest.skip('Explicit API30 firmware image and ELF required')
    r=run(Path(elf),Path(image),ROOT)
    assert r['result']=='PASS'
    assert r['firmware_bytes']<=122880 and r['api_level']==30
    assert r['successful_irq_service_start_and_stop']
    assert r['route_restored'] and r['vector_restored']
    assert r['pcm_and_dma_pointers_cleared']
    assert not r['physical_dma_timing_tested'] and not r['actual_sotto_codec_tested']
