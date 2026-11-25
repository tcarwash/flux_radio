"""Opt-in realistic linked ARM API30 route-only test against explicit image."""
import os
from pathlib import Path
import pytest
from tools.k6_sotto_gp0_route_arm_gate import run
ROOT=Path(__file__).resolve().parents[1]

def test_linked_arm_mic_route_only():
    elf=os.getenv('K6_SIM_SOTTO_RX31_ELF')
    image=os.getenv('K6_SIM_SOTTO_RX31_IMAGE')
    if not elf or not image or os.getenv('K6_SIM_SOTTO_MIC')!='1':
        pytest.skip('Explicit API30 opt-in image/ELF required')
    r=run(Path(elf),Path(image),ROOT)
    assert r['result']=='PASS' and r['no_TIM17_irq_or_pcm_ring']
    assert r['route_only_op7_accepts_with_tx_lease']
    assert r['pa_restored'] and not r['physical_gp0_tested']
