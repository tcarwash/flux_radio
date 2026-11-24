"""Offline-only finite native 4800 2FSK TX and a sampled virtual RF channel.

No SDR sink, hardware access, or claim of silicon-accurate TX behavior.
The sender records timestamped RF bursts; the receiver independently reads IQ.
"""
from dataclasses import dataclass
import math
import numpy as np

from .backend import RFBackend
from .sources import IqSource
from .fsk import BAUD

TX_DONE = 0x8000
TX_EN = 0x0800
TX_CLEAR = 0x8000
TX_CAPACITY = 64  # model assumption, not continuous refill support


@dataclass(frozen=True)
class Channel:
    deviation_hz: float = 1800
    offset_hz: float = 0
    clock_ppm: float = 0
    cn_db: float = 25  # carrier/noise power in 25 kHz
    signal_dbm: float = -70
    seed: int = 1
    prefix_every: int = 0  # explicit replay of observed 00f8 fault; 0 disables

    def __post_init__(self):
        if not all(math.isfinite(x) for x in (self.deviation_hz, self.offset_hz, self.clock_ppm,
                                             self.cn_db, self.signal_dbm)):
            raise ValueError("channel parameters must be finite")
        if not 0 < self.deviation_hz <= 10000 or abs(self.clock_ppm) > 100000:
            raise ValueError("deviation or clock error outside model range")
        if not -60 <= self.cn_db <= 100 or not -160 <= self.signal_dbm <= -10:
            raise ValueError("channel power outside model range")
        if self.prefix_every < 0 or self.seed < 0:
            raise ValueError("prefix cadence and seed must be nonnegative")


@dataclass
class Burst:
    start: float
    end: float
    hz: int
    bits: np.ndarray
    baud: float


class VirtualAir:
    def __init__(self, channel=Channel()):
        self.channel = channel
        self.bursts = []

    def emit(self, start, hz, marker, data):
        p = self.channel
        if p.prefix_every and (len(self.bursts)+1) % p.prefix_every == 0:
            data = b"\xf8\x00"+data[:-2]
        bits = np.unpackbits(np.frombuffer(marker+data, dtype=np.uint8)).astype(np.float64)*2-1
        baud = BAUD*(1+p.clock_ppm/1e6)
        burst = Burst(start, start+len(bits)/baud, hz, bits, baud)
        self.bursts.append(burst)
        return burst


class AirSource(IqSource):
    """Virtual-clock IQ source; deterministic noise independent of read chunk size."""
    def __init__(self, air, rate, center_hz):
        self.air = air
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.n = 0
        self.rng = np.random.default_rng(air.channel.seed)

    def describe(self):
        return "offline virtual FSK air"

    def read(self, n):
        p = self.air.channel
        amp = 10**((p.signal_dbm-self.dbm_offset)/20)
        sigma = amp*math.sqrt(self.rate/25000 * 10**(-p.cn_db/10)/2)
        noise = self.rng.standard_normal((n,2))*sigma
        x = (noise[:,0]+1j*noise[:,1]).astype(np.complex64)
        t = (self.n+np.arange(n))/self.rate
        for b in self.air.bursts:
            off = b.hz-self.center_hz+p.offset_hz
            if abs(off)+p.deviation_hz >= self.rate/2:
                continue
            mask = (t>=b.start)&(t<b.end)
            if not np.any(mask):
                continue
            dt = t[mask]-b.start
            pos = dt*b.baud
            idx = np.minimum(pos.astype(int),len(b.bits)-1)
            integral = np.r_[0.,np.cumsum(b.bits)]
            phase = 2*np.pi*(off*dt+p.deviation_hz/b.baud*(integral[idx]+b.bits[idx]*(pos-idx)))
            x[mask] += (amp*np.exp(1j*phase)).astype(np.complex64)
        self.n += n
        return x


class VirtualTxBackend(RFBackend):
    """Opt-in register-level TX endpoint. Ordinary RX backends cannot emit."""
    def __init__(self, air, rate, center_hz):
        super().__init__(AirSource(air, rate, center_hz))
        self.air = air
        self.fifo = []
        self.fifo_fault = False
        self.active = None
        self.pending = self.latched = 0
        self.completed = self.aborted = self.rejected = self.overflows = 0
        self.reason = None
        self.last_seconds = 0.0

    def advance_tx(self, seconds):
        if not math.isfinite(seconds) or seconds < self.last_seconds:
            raise ValueError("TX virtual time must be finite and monotonic")
        self.last_seconds = seconds
        if self.active is not None and seconds >= self.active.end:
            self.active = None
            self.completed += 1
            self.pending |= TX_DONE

    def abort(self, seconds):
        if self.active is not None:
            self.active.end = min(self.active.end, max(self.active.start, seconds))
            self.active = None
            self.aborted += 1

    def write(self, address, value, regs, seconds):
        if self.closed:
            return
        self.advance_tx(seconds)
        if address == 0x02 and value == 0:
            self.latched, self.pending = self.pending, 0
        if address == 0x5F and regs.get(0x3F,0)&0x8000:
            if self.active is not None or len(self.fifo)>=TX_CAPACITY:
                self.overflows += 1
                self.fifo_fault = True
            else:
                self.fifo.append(value&0xffff)
            return
        if address == 0x3F and not value&0x8000:
            self.abort(seconds)
        if address in (0x38,0x39,0x58,0x5A,0x5B,0x5C,0x5D,0x72) and value != regs.get(address):
            self.abort(seconds)
        if address == 0x59:
            if not value&TX_EN or value&TX_CLEAR:
                self.abort(seconds)
            if value&TX_CLEAR:
                self.fifo.clear()
                self.fifo_fault = False
                self.pending = self.latched = 0
            if value&TX_EN and not regs.get(0x59,0)&TX_EN:
                n = ((regs.get(0x5D,0)>>8)&255)+1
                hz = ((regs.get(0x39,0)<<16)|regs.get(0x38,0))*10
                if (not regs.get(0x3F,0)&0x8000 or value != 0x08f8
                    or regs.get(0x58)!=0xe1 or regs.get(0x72)!=0xc194
                    or regs.get(0x5C)!=0x5625 or self.fifo_fault or n!=2*len(self.fifo)
                    or not 18_000_000<=hz<=1_300_000_000):
                    self.rejected += 1
                    self.reason = "unsupported TX profile or incomplete preloaded FIFO"
                else:
                    marker = b"\x55"*16+regs.get(0x5A,0).to_bytes(2,"big")+regs.get(0x5B,0).to_bytes(2,"big")
                    data = b"".join(w.to_bytes(2,"little") for w in self.fifo)
                    self.fifo.clear()
                    self.reason = None
                    self.active = self.air.emit(seconds,hz,marker,data)
        super().write(address,value,regs,seconds)

    def read(self, address, regs, seconds):
        if self.closed:
            return None
        self.advance_tx(seconds)
        if regs.get(0x3F,0)&0x8000:
            if address == 0x0C:
                return (regs.get(address,0)&~1)|int(bool(self.pending&regs.get(0x3F,0)))
            if address == 0x02:
                return self.pending or self.latched
        return super().read(address,regs,seconds)

    def status(self):
        return {**super().status(), "virtual_tx_only": True, "fsk_tx": self.tx_status()}

    def tx_status(self):
        return dict(completed=self.completed, aborted=self.aborted, rejected=self.rejected,
                    overflows=self.overflows, active=self.active is not None,
                    fifo_words=len(self.fifo), reason=self.reason)

    def close(self):
        if self.active is not None:
            self.abort(self.last_seconds)
        super().close()
