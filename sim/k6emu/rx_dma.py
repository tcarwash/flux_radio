"""Cycle-clocked GPIOB IDR -> DMA1 channel 1 receive model.

The caller supplies one-bit PB1 samples at the capture rate; this models DMA
register progression, not yet the FD6818B analog IF or timer-trigger wiring.
"""
from __future__ import annotations

DMA_BASE = 0x40020000
GPIOB_IDR = 0x48000410
MEASURED_FREE_RUNNING_HZ = 5_710_000  # gp0dma run 7; approximate, configuration-specific


class GpioRxDma:
    def __init__(self, write_byte, samples=(), sample_rate=85800, cpu_hz=96_000_000):
        self.write_byte = write_byte
        self.samples = iter(samples)
        if sample_rate <= 0 or cpu_hz <= 0:
            raise ValueError("clock rates must be positive")
        self.sample_rate = sample_rate
        self.cpu_hz = cpu_hz
        self.regs = {}
        self.remaining = 0
        self.initial = 0
        self.index = 0
        self.phase = 0
        self.transfers = 0
        self.elapsed_cycles = 0
        self.exhausted = False

    @classmethod
    def measured_free_running(cls, write_byte, samples=(), cpu_hz=96_000_000):
        """Use measured unpaced DMA throughput; NOT an APRS sample clock."""
        return cls(write_byte, samples, MEASURED_FREE_RUNNING_HZ, cpu_hz)

    @classmethod
    def measured_tx_microphone(cls, write_byte, samples=(), *,
                               measurement="pcm", stimulus="1khz",
                               cpu_hz=96_000_000, calibration=None):
        """Opt-in B-chip TX GP0/PB1 transfer rate from measured MIC GP0 runs.

        Different source than the legacy RX path's 5.71MS/s nominal value;
        this is a measured *single-run* sample clock, not a new global chip
        constant. The caller must supply actual one-bit waveform samples.
        """
        from .rf.physical_calibration import Gp0MicCalibration
        observed = (calibration or Gp0MicCalibration()).evidence(measurement,stimulus)
        result = cls(write_byte,samples,observed["raw_dma_hz"],cpu_hz=cpu_hz)
        result.measured_provenance = observed["source"]
        result.measured_sha256 = observed["sha256"]
        result.measurement_only = True
        return result

    def write(self, address, value):
        offset = address - DMA_BASE
        if offset == 0x08:
            old = self.regs.get(offset, 0)
            self.regs[offset] = value
            if value & 1 and not old & 1:
                self.initial = self.regs.get(0x0c, 0) & 0xffff
                self.remaining = self.initial
                self.index = 0
                self.phase = 0
        elif offset == 0x0c:
            self.remaining = value & 0xffff
            self.regs[offset] = self.remaining
        elif offset == 0x04:
            self.regs[0] = self.regs.get(0, 0) & ~(value & 0x0f)
        else:
            self.regs[offset] = value

    def read(self, address):
        offset = address - DMA_BASE
        return self.remaining if offset == 0x0c else self.regs.get(offset, 0)

    def advance(self, cycles):
        if cycles < 0:
            raise ValueError("cycles must be nonnegative")
        self.elapsed_cycles += cycles
        ccr = self.regs.get(0x08, 0)
        if not ccr & 1 or not self.initial or self.exhausted:
            return
        self.phase += cycles * self.sample_rate
        due, self.phase = divmod(self.phase, self.cpu_hz)
        if due == 0:
            return
        if self.regs.get(0x10) != GPIOB_IDR:
            return
        for _ in range(due):
            try:
                bit = int(next(self.samples)) & 1
            except StopIteration:
                self.exhausted = True
                break
            address = self.regs.get(0x14, 0) + (self.index if ccr & (1 << 7) else 0)
            self.write_byte(address, bit << 1)
            self.transfers += 1
            self.remaining -= 1
            self.index += 1
            if self.remaining == self.initial // 2:
                self.regs[0] = self.regs.get(0, 0) | 0x04
            if self.remaining == 0:
                self.regs[0] = self.regs.get(0, 0) | 0x02
                if ccr & (1 << 5):
                    self.remaining = self.initial
                    self.index = 0
                else:
                    self.regs[0x08] &= ~1
                    break
