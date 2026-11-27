"""Bounded *synthetic* GP0 half-buffer interrupt scheduler for Cortex-M0 timing studies.

This replaces the smooth per-8kHz PCM ADC cycle charge with discrete 8-group
PDM IRQ9 events at the firmware's 87,260/8 Hz cadence. It DOES NOT execute the
real ARM ISR or infer its mean cost from a TIM17 peak measurement.

When a CPU slice crosses N scheduled interrupts, those N interrupt costs
extend elapsed time, potentially bringing more IRQs due. Wall sleeps let the
IRQ operate inside the sleep and do not bill their CPU time a second time.
The GP0 ADC's generated PCM count follows completed PDM groups, not an
independent idealized 8kHz timer.
"""
from __future__ import annotations
import math

CPU_HZ = 96_000_000
PDM_GROUPS_PER_SECOND = 87_260
GROUPS_PER_IRQ = 8
PCM_HZ = 8_000
PCM_BLOCK_SAMPLES = 80
PCM_BLOCKS = 4


class GP0InterruptBudget:
    """Virtual GP0 ISR schedule, with 8 PDM groups per handled half-buffer."""

    def __init__(self, start_cycles: float, irq_cycles: float, *,
                 actual_group_rate_hz: float=PDM_GROUPS_PER_SECOND):
        # This is the physically occurring PDM group rate, not the
        # firmware's fixed 87260Hz divisor. They must remain independent.
        if (not math.isfinite(actual_group_rate_hz) or
                not 70_000 <= actual_group_rate_hz <= 100_000):
            raise ValueError("GP0 actual PDM group rate must be finite and plausible")
        self.actual_group_rate_hz = float(actual_group_rate_hz)
        self.period_cycles = CPU_HZ * GROUPS_PER_IRQ / self.actual_group_rate_hz
        if not math.isfinite(start_cycles) or start_cycles < 0:
            raise ValueError("invalid GP0 capture start")
        if (not math.isfinite(irq_cycles) or irq_cycles < 0 or
                irq_cycles >= self.period_cycles):
            raise ValueError("GP0 ISR cost must be finite and shorter than IRQ period")
        self.start_cycles = start_cycles
        self.irq_cycles = irq_cycles
        self.irq_count = 0
        self.charged_cycles = 0.0

    def count_due(self, end_cycles: float) -> int:
        if end_cycles < self.start_cycles:
            return 0
        return max(0, int(math.floor(
            (end_cycles - self.start_cycles) * self.actual_group_rate_hz /
            (CPU_HZ * GROUPS_PER_IRQ) + 1e-9)))

    @property
    def pcm_samples(self) -> int:
        # The physical PDM IRQ clock can vary, but the *firmware* is still
        # dividing by its nominal constant 87260 and labeling output 8kHz.
        # For real observed rates != nominal, the produced sample clock drifts.
        return self.irq_count * GROUPS_PER_IRQ * PCM_HZ // PDM_GROUPS_PER_SECOND

    def advance(self, now_cycles: float, cpu_cycles: float, *, wall_sleep=False) -> float:
        """Return time passed, including IRQ CPU contention (except during sleep)."""
        if not math.isfinite(now_cycles) or not math.isfinite(cpu_cycles) or cpu_cycles < 0:
            raise ValueError("nonfinite or negative work interval")
        end = now_cycles + cpu_cycles
        if wall_sleep:
            # An ISR runs during this explicitly requested wall-time delay.
            # Callers cannot use this mode for zero-duration foreground work.
            self.irq_count = max(self.irq_count, self.count_due(end))
            return cpu_cycles

        # Each additional interrupt extends the foreground's completion
        # time. Fixed per-IRQ cost is a scenario, not measured mean time.
        for _ in range(1000):
            count = self.count_due(end)
            pending = count - self.irq_count
            if pending <= 0:
                return end - now_cycles
            self.irq_count = count
            extra = pending * self.irq_cycles
            self.charged_cycles += extra
            end += extra
        raise RuntimeError("GP0 IRQ scheduling did not converge")
