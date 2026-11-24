"""Behavioral direct 4800-baud 2FSK RX. No hardware CRC, TX, or payload oracle."""
from collections import deque
import numpy as np

SYNC, FULL, DONE = 0x0002, 0x1000, 0x2000
RX_EN, RX_CLEAR, TX_EN = 0x1000, 0x4000, 0x0800
FIFO_DEPTH = 8  # measured K6 RX depth; overflow policy is a model
BAUD = 4800
PHASES = 8


class FskRx:
    """Single armed frame; acquire from preamble/sync, deliver words as time passes."""
    def __init__(self, rate):
        self.rate = rate
        self.armed = False
        self.configured = False
        self.unsupported = None
        self.fifo = deque()
        self.pending = self.latched = self.sync_status = 0
        self.frames = self.words = self.overruns = self.underflows = self.gaps = 0
        self.reset_demod()

    def reset_demod(self):
        self.buf = np.empty(0, dtype=np.float32)
        self.prev = None
        self.input_count = 0
        self.next_grid = 0
        self.locked = False
        self.bits = []
        self.received = 0

    def discontinuity(self):
        if self.armed:
            self.gaps += 1
            self.fifo.clear()
            self.pending = self.latched = self.sync_status = 0
            self.reset_demod()

    def write(self, address, value, regs):
        if address in (0x58, 0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x72) and self.armed and value != regs.get(address):
            self.discontinuity()
            self.armed = self.configured = False
            self.unsupported = "configuration changed while armed; rearm required"
        if address == 0x02 and value == 0:
            self.latched = self.pending
            self.pending = 0
        elif address == 0x59:
            if value & RX_CLEAR:
                self.fifo.clear()
                self.pending = self.latched = self.sync_status = 0
                self.reset_demod()
            if self.armed and (value ^ regs.get(0x59, 0)) & 0x04FF:
                self.discontinuity()
                self.armed = self.configured = False
            if not value & RX_EN or value & TX_EN:
                self.armed = self.configured = False
                self.unsupported = None
                self.reset_demod()
                return
            if not self.armed:
                self.reset_demod()
                self.unsupported = None
                n = ((regs.get(0x5D, 0) >> 8) & 255) + 1
                threshold = regs.get(0x5E, 0) & 7
                if (regs.get(0x58) != 0x00E1 or regs.get(0x72) != 0xC194
                        or regs.get(0x5C) != 0x5625 or value & 0x04FF != 0x00F8
                        or n % 2 or not threshold):
                    self.unsupported = "requires direct 4800 2FSK, CRC off, 16x55/4-byte sync, even length, threshold 1–7"
                    return
                self.length_words = n // 2
                self.threshold = threshold
                mark = bytes([0x55])*16 + regs.get(0x5A, 0).to_bytes(2, "big") + regs.get(0x5B, 0).to_bytes(2, "big")
                self.marker = np.unpackbits(np.frombuffer(mark, dtype=np.uint8))
                template = self.marker.astype(np.float64)*2-1
                self.template = template-template.mean()
                self.norm = float(self.template @ self.template)
                self.armed = self.configured = True

    def read(self, address):
        if not self.configured:
            return None
        if address == 0x02:
            return self.pending or self.latched
        if address == 0x0B:
            return self.sync_status
        if address == 0x5F:
            if self.fifo:
                return self.fifo.popleft()
            self.underflows += 1
            return 0
        return None

    def feed(self, hz):
        if not self.armed or not len(hz):
            return
        # Continuous interpolation onto an eight-phase symbol grid, independent
        # of source read boundaries. Retain the previous input for interpolation.
        x = np.asarray(hz, dtype=np.float32)
        start = self.input_count
        if self.prev is not None:
            x = np.concatenate(([self.prev], x))
            start -= 1
        self.input_count += len(hz)
        self.prev = x[-1]
        step = self.rate / (BAUD * PHASES)
        end = int(np.floor((self.input_count-1)/step))+1
        if end <= self.next_grid:
            return
        pos = np.arange(self.next_grid, end)*step-start
        grid = np.interp(pos, np.arange(len(x)), x).astype(np.float32)
        self.next_grid = end
        self.buf = np.concatenate((self.buf, grid))
        if not self.locked:
            self.acquire()
        if self.locked:
            self.payload()

    def acquire(self):
        # Compare every symbol phase. Require a complete public preamble+sync
        # and one symbol of lookahead before choosing the earliest strong lock.
        m = len(self.marker)
        span = (m-1)*PHASES+1
        if len(self.buf) < span+PHASES:
            return
        candidates = []
        for phase in range(PHASES):
            x = self.buf[phase::PHASES].astype(np.float64)
            if len(x) < m+1:
                continue
            sums = np.r_[0., np.cumsum(x)]
            squares = np.r_[0., np.cumsum(x*x)]
            var = np.maximum(squares[m:]-squares[:-m]-(sums[m:]-sums[:-m])**2/m, 1e-9)
            corr = np.correlate(x, self.template, "valid")/np.sqrt(var*self.norm)
            for k in np.flatnonzero(abs(corr[:-1]) >= .84):
                prefix = x[k:k+m]
                lo = float(np.median(prefix[self.marker == 0]))
                hi = float(np.median(prefix[self.marker == 1]))
                if abs(hi-lo) < 550:
                    continue
                pol = 1 if hi > lo else -1
                mid = (lo+hi)/2
                errors = np.count_nonzero(((prefix-mid)*pol>0) != self.marker)
                if errors <= 6:
                    candidates.append((phase+k*PHASES, abs(corr[k]), mid, pol))
        if not candidates:
            self.buf = self.buf[-(span+2*PHASES):].copy()
            return
        earliest = min(c[0] for c in candidates)
        start, _, self.mid, self.pol = max((c for c in candidates if c[0] <= earliest+PHASES),
                                           key=lambda c:c[1])
        self.locked = True
        self.pending |= SYNC
        self.sync_status = 0x40 if self.pol > 0 else 0x80
        self.buf = self.buf[start+m*PHASES:].copy()

    def payload(self):
        while len(self.buf) and self.armed:
            # One sample per symbol; the fixed nominal clock is intentional.
            bit = int((self.buf[0]-self.mid)*self.pol > 0)
            if len(self.buf) < PHASES:
                break
            self.buf = self.buf[PHASES:]
            self.bits.append(bit)
            if len(self.bits) == 16:
                data = np.packbits(self.bits).tobytes()
                word = int.from_bytes(data, "little")
                self.bits.clear()
                self.received += 1
                self.words += 1
                if len(self.fifo) == FIFO_DEPTH:
                    self.overruns += 1
                else:
                    self.fifo.append(word)
                    if len(self.fifo) == self.threshold:
                        self.pending |= FULL
                if self.received == self.length_words:
                    self.pending |= DONE
                    self.frames += 1
                    self.armed = False
                    self.reset_demod()

    def status(self):
        return {"armed": self.armed, "configured": self.configured, "locked": self.locked, "fifo_words": len(self.fifo),
                "frames": self.frames, "words": self.words, "overruns": self.overruns,
                "underflows": self.underflows, "gaps": self.gaps, "unsupported": self.unsupported}
