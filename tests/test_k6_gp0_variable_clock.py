"""Physical GP0 group clock vs fixed 87260 divisor: offline regression.

Only synthetic timing. Previous RFIC captures measured group rates 84960 and
87770 in distinct sessions. Neither was measured during the latest tone WAVs.
"""
import math
import numpy as np
import pytest
from pathlib import Path
from tools.k6_gp0_irq_timing import GP0InterruptBudget,CPU_HZ,PDM_GROUPS_PER_SECOND
from tools.k6_sotto_virtual_link import trial,prepare
from sim.k6emu.rf.virtual_link import Channel

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("actual_hz",[84960,87260,87770,83400])
def test_virtual_raw_clock_and_fixed_firmware_pcm_divisor_are_independent(actual_hz):
    s=GP0InterruptBudget(0,2000,actual_group_rate_hz=actual_hz)
    s.advance(0,CPU_HZ*.5,wall_sleep=True)
    expected_irq=math.floor(actual_hz*.5/8+1e-9)
    assert s.irq_count==expected_irq
    assert s.pcm_samples==(expected_irq*8*8000)//87260
    expected_output_rate=8000*actual_hz/87260
    # A whole DMA IRQ contains eight PDM groups and PCM is integer-counted.
    # At only 0.5 s, that quantization can lose nearly 2 samples = 4/s.
    assert 0 <= expected_output_rate-s.pcm_samples/.5 < 4
    assert s.charged_cycles==0
    assert abs(s.period_cycles-CPU_HZ*8/actual_hz)<1e-6


@pytest.mark.parametrize("bad",[0,-1,69999,100001,float("nan"),float("inf")])
def test_invalid_physical_group_clocks_fail_closed(bad):
    with pytest.raises(ValueError,match="actual PDM group rate"):
        GP0InterruptBudget(0,2000,actual_group_rate_hz=bad)


def test_offline_sim_refuses_rate_change_without_irq_model():
    with pytest.raises(ValueError,match="requires periodic"):
        trial({"gp0":True},gp0_actual_group_rate_hz=84960)
    with pytest.raises(ValueError,match="GP0 module"):
        trial({"gp0":False},gp0_irq_cycles=2000,gp0_actual_group_rate_hz=84960)


@pytest.fixture(scope="module")
def image(tmp_path_factory):
    i=np.arange(3280)
    pcm=(9000*np.sin(2*np.pi*500*i/8000)).astype('<i2')
    return prepare(ROOT,tmp_path_factory.mktemp("gp0-variable-clock"),pcm=pcm,gp0=True)


def test_compiled_sotto_virtual_link_reports_physical_clock_distinction(image):
    r=trial(image,Channel(),cpi=2.0,gp0_irq_cycles=2300,
            gp0_actual_group_rate_hz=84960,
            spi_us=6.19213,spi_byte_us=.940394,fd_us=60,
            fsk_cooperative_model=True)
    assert r["gp0_actual_group_rate_hz"]==84960
    assert r["gp0_effective_pcm_rate_hz"]==round(8000*84960/87260,2)
    assert r["gp0_virtual_irq_count"] is not None
    assert not r["tx"]["active"]
    assert not r["lease_expired"] or r["module_return"]<=0
