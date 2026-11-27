"""Periodic GP0 timing model: event cadence and fail-closed integration checks."""
import math
import numpy as np
import pytest
from pathlib import Path
from tools.k6_gp0_irq_timing import (
    CPU_HZ, GP0InterruptBudget, PDM_GROUPS_PER_SECOND,
    GROUPS_PER_IRQ, PCM_HZ,
)
from tools.k6_sotto_virtual_link import prepare, trial
from sim.k6emu.rf.virtual_link import Channel

ROOT = Path(__file__).resolve().parents[1]


def test_measured_irq_count_matches_240ms_physical_window():
    period = CPU_HZ * GROUPS_PER_IRQ / PDM_GROUPS_PER_SECOND
    sim = GP0InterruptBudget(1000.0, 3488)
    sim.advance(1000.0, 2619 * period + 0.01, wall_sleep=True)
    assert sim.irq_count == 2619
    assert sim.pcm_samples == (2619 * 8 * 8000) // 87260
    assert sim.charged_cycles == 0


def test_irq_steals_cycles_and_can_make_more_irqs_due():
    period = CPU_HZ * GROUPS_PER_IRQ / PDM_GROUPS_PER_SECOND
    sim = GP0InterruptBudget(0, 3488)
    passed = sim.advance(0, 10 * period)
    assert sim.irq_count > 10
    assert passed == pytest.approx(10 * period + 3488 * sim.irq_count)
    assert sim.charged_cycles == pytest.approx(3488 * sim.irq_count)
    assert sim.count_due(passed) == sim.irq_count


def test_no_drift_and_invalid_schedule_rejected():
    for cost in (math.nan, math.inf, -1, 8802, 100000):
        with pytest.raises(ValueError):
            GP0InterruptBudget(0, cost)
    p=GP0InterruptBudget(0, 2000)
    for i in range(100):
        # 1ms wall sleeps emulate 8kHz PCM capture with no extra CPU bill.
        p.advance(i * CPU_HZ / 1000, CPU_HZ / 1000, wall_sleep=True)
    assert p.irq_count == 1090
    assert p.pcm_samples == 799


def test_model_rejects_double_charge_and_wrong_module():
    artifact={"gp0":True}
    with pytest.raises(ValueError,match="adc_cycles=0"):
        trial(artifact, gp0_irq_cycles=3000, adc_cycles=2000)
    with pytest.raises(ValueError,match="GP0 module"):
        trial({"gp0":False},gp0_irq_cycles=3000)
    with pytest.raises(ValueError,match="shorter than IRQ period"):
        trial(artifact,gp0_irq_cycles=9000)


@pytest.fixture(scope="module")
def gp0_code(tmp_path_factory):
    i = np.arange(3280)
    pcm=(9000*np.sin(2*np.pi*320*i/8000)).astype("<i2")
    return prepare(ROOT,tmp_path_factory.mktemp("gp0-physical-irq"),pcm=pcm,gp0=True)


def test_compiled_gp0_module_runs_on_periodic_irq_sim(gp0_code):
    r=trial(gp0_code, Channel(), cpi=2.0, gp0_irq_cycles=2300,
            spi_us=6.19213, spi_byte_us=.940394, fd_us=60,
            fsk_cooperative_model=True)
    assert r["gp0_virtual_irq_count"] is not None
    assert r["gp0_virtual_irq_count"] > 0
    # IRQs that occur during explicit foreground sleeps consume wall time
    # already paid by the delay; they must not be charged twice.
    assert 0 < r["gp0_virtual_irq_charged_cycles"] <= r["gp0_virtual_irq_count"] * 2300
    assert r["pcm_overflow_ms"] is None if not r["pcm_lost"] else r["pcm_overflow_ms"] >= 0
    assert not r["tx"]["active"]
