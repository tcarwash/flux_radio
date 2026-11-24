"""Connect the behavioral receive model to K6 register transactions (not TX)."""
import time

from .chip import ChipModel
from .sources import open_source


class RFBackend:
    REGISTERS = frozenset((0x67, 0x65, 0x63, 0x0C))

    def __init__(self, source):
        self.chip = ChipModel(source)
        self.source = source
        self.closed = False
        self.last_rssi_raw = None
        self._pace = None
        if source.live:
            self.chip.start()

    def read(self, address, regs, seconds):
        if address not in self.REGISTERS or self.closed:
            return None
        if self.chip.error:
            return {0x67: 0, 0x65: 127, 0x63: 255, 0x0C: regs.get(0x0C, 0) & ~2}[address]
        hz = ((regs.get(0x39, 0) << 16) | regs.get(0x38, 0)) * 10
        # Ignore reset/uninitialized frequency. Advance old tuning before a retune.
        if not 18_000_000 <= hz <= 1_300_000_000:
            return None
        if self.chip.tuned_hz is None:
            self.chip.tune(hz)
        # Batch tiny virtual-time reads to avoid an FFT for every bus transaction.
        if seconds - self.chip.t >= 0.001:
            self.chip.advance(seconds)
        if hz != self.chip.tuned_hz:
            self.last_rssi_raw = None
        self.chip.tune(hz)
        value = self.chip.read_reg(address, regs)
        if address == 0x67:
            self.last_rssi_raw = value
        return value

    def pace(self, seconds):
        if not self.source.live:
            return
        now = time.monotonic()
        if self._pace is None:
            self._pace = (seconds, now)
        ahead = seconds - self._pace[0] - (now - self._pace[1])
        if ahead > 0:
            # Board cycles include accelerated virtual delays; don't stall the UI for them.
            time.sleep(min(ahead, .02))
        if abs(ahead) > .25:
            self._pace = (seconds, time.monotonic())

    def status(self):
        c = self.chip
        with c.lock:
            return {"source": self.source.describe(), "live": self.source.live,
                    "tuned_hz": c.tuned_hz, "center_hz": self.source.center_hz,
                    "sample_rate_hz": self.source.rate, "floor_dbm": c.floor_dbm,
                    "noise_raw": c.noise_raw(), "glitch_raw": c.glitch_raw(),
                    "squelch_open": c.sq and not c.error, "error": c.error, "receive_only": True,
                    "calibrated": False,
                    "rssi_dbm": (self.last_rssi_raw / 2 - c.rssi_offset
                                 if self.last_rssi_raw is not None and not c.error else None),
                    "replay_position": getattr(self.source, "pos", None),
                    "replay_samples": getattr(self.source, "length", None),
                    "replay_eof": bool(hasattr(self.source, "length") and not self.source.loop
                                       and self.source.pos == self.source.length)}

    def close(self):
        if not self.closed:
            self.closed = True
            self.chip.stop()


def create_backend(spec, **kwargs):
    source = open_source(spec, **kwargs)
    try:
        return RFBackend(source)
    except BaseException:
        source.close()
        raise


