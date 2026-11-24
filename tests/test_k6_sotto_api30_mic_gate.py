"""Actual ARM API30 microphone start, 8kHz modeled TIM17 ISR and teardown."""
import os
from pathlib import Path
import pytest

from tools.k6_sotto_api30_mic_gate import run

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("abandon", [False,True],ids=["normal-stop","abandon-module-cleanup"])
def test_linked_arm_api30_mic_sampler_and_cleanup(abandon):
    image=os.environ.get("K6_SIM_SOTTO_RX31_IMAGE")
    elf=os.environ.get("K6_SIM_SOTTO_RX31_ELF")
    if not image or not elf or os.environ.get("K6_SIM_SOTTO_MIC")!="1":
        pytest.skip("Explicit API30 opt-in image/ELF required")
    r=run(Path(elf),Path(image),ROOT,leave_active=abandon)
    assert r["result"]=="PASS"
    assert r["real_ARM_module"] and not r["physical_pa11_tested"]
    assert not r["real_audio_8khz_tested"] and r["simulated_only"]
    assert r["module_return"]==(11 if abandon else 10)
    assert r["abandoned_mic_on_module_return"]==abandon
    assert r["radio_enter_tx_calls"]==1
    assert r["pa_restored"] and r["tim17_irq_restored"]
    assert r["mic_ring_ptr_cleared"] and r["tim17_timer_stopped"]
    assert r["ram0_live_mirrors"]>0
    if not abandon:
        sample=r["modeled_8khz_samples"]
        assert r["arm_tim17_isr_invoked"]
        assert sample["produced"]>=1 and sample["lost"]==0
        assert sample["pcm_nonzero_samples"]>=30
        assert sample["sample_invocations"]==95
        assert sample["peak_virtual_cycles"]<12000
    else:
        assert not r["arm_tim17_isr_invoked"]
