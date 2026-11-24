"""Connect the behavioral receive model to K6 register transactions (not TX)."""
import time

from .chip import ChipModel
from .sources import open_source


class RFBackend:
    REGISTERS = frozenset((0x67, 0x65, 0x63, 0x0C, 0x02, 0x0B, 0x5F))
    FSK_WRITES = frozenset((0x02, 0x38, 0x39, 0x3F, 0x58, 0x59, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72))

    def __init__(self, source):
        self.chip = ChipModel(source)
        self.source = source
        self.closed = False
        self.last_rssi_raw = None
        self._pace = None
        self._tune_dirty = False
        if source.live:
            self.chip.start()

    def _advance(self, regs, seconds, *, exact=False):
        hz = ((regs.get(0x39, 0) << 16) | regs.get(0x38, 0)) * 10
        if not 18_000_000 <= hz <= 1_300_000_000:
            return False
        c = self.chip
        if c.tuned_hz is None or self._tune_dirty:
            if hz != c.tuned_hz:
                self.last_rssi_raw = None
            c.tune(hz)
            self._tune_dirty = False
        if exact or seconds - c.t >= .001:
            c.advance(seconds)
        if hz != c.tuned_hz:
            self.last_rssi_raw = None
        c.tune(hz)
        return True

    def write(self, address, value, regs, seconds):
        if self.closed or address not in self.FSK_WRITES:
            return
        c = self.chip
        with c.lock:
            # Advance the old configuration before control or tuning changes.
            if c.fsk.armed or (address == 0x59 and value & 0x1000):
                self._advance(regs, seconds, exact=True)
            c.fsk.write(address, value, regs)
            if address in (0x38, 0x39) and value != regs.get(address):
                self._tune_dirty = True

    def read(self, address, regs, seconds):
        if address not in self.REGISTERS or self.closed:
            return None
        c = self.chip
        with c.lock:
            if c.error:
                return {0x67: 0, 0x65: 127, 0x63: 255, 0x0C: regs.get(0x0C, 0) & ~3,
                        0x02: 0, 0x0B: 0, 0x5F: 0}[address]
            if not self._advance(regs, seconds):
                return None
            if address in (0x02, 0x0B, 0x5F):
                return c.fsk.read(address)
            value = c.read_reg(address, regs)
            if address == 0x0C and c.fsk.configured:
                value = (value & ~1) | int(bool(c.fsk.pending & regs.get(0x3F, 0)))
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
                    "calibrated": False, "fsk_rx": c.fsk.status(),
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


