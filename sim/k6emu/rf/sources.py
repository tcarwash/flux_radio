# Adapted from baitisj/blowfang@4a924aec08c1ee41487f0642820dc1c757f96ecf (MIT).
# Copyright (c) 2026 baitisj and contributors; see repository LICENSE.
"""RX-only IQ sources adapted from baitisj/blowfang SoapyFang (MIT)."""
from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass

import numpy as np

REF_DBM = -10.0                 # synthetic and generated-SigMF full scale (power 1.0), dBm
NOISE_BW_HZ = 25_000            # the floor's dBm is measured in this bandwidth
VOICE_TONE_HZ, VOICE_DEV_HZ = 1000.0, 2500.0     # a "voice" over: a steady FM test tone
CW_TONE_HZ, CW_DEV_HZ = 800.0, 2500.0            # a CW ident: keyed carrier with an MCW tone


class IqSource:
    """the interface: read(n) the next n samples; tune(hz) moves the window if it can"""
    rate = 0.0
    center_hz = 0.0
    dbm_offset = REF_DBM
    auto_floor_dbm = None
    live = False                # True: samples arrive in wall time (an SDR), not on demand
    tunable = False

    def read(self, n):
        raise NotImplementedError

    def skip(self, n):
        """drop n samples (a jump in virtual time); sources that can do it cheaply override"""
        while n > 0:
            k = min(n, 1 << 18)
            self.read(k)
            n -= k

    def tune(self, hz):
        return False

    def close(self):
        pass

    def describe(self):
        return type(self).__name__


@dataclass(frozen=True)
class Station:
    hz: float
    dbm: float
    kind: str = "voice"

    def keyed(self, seconds):
        return True


def default_band():
    return [Station(146_520_000, -73), Station(146_550_000, -92),
            Station(440_850_000, -78), Station(440_900_000, -96)]


class SyntheticSource(IqSource):
    """the Station list as I/Q. Stations are keyed at 1 ms resolution in sample time, so a
    source read in step with a board's virtual clock agrees with Station.keyed(board.seconds)."""
    tunable = True

    def __init__(self, band=None, rate=1_200_000, center_hz=440_000_000, floor_dbm=-125.0, seed=1):
        if band is None:
            band = default_band()
        self.band = band                    # shared, not copied: the workbench edits it live
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.floor_dbm = floor_dbm
        self.rng = np.random.default_rng(seed)
        self.n = 0                          # samples given so far: t = n / rate
        self.carrier_ph = {}                # per station: carrier phase, modulation phase
        self.mod_ph = {}

    def describe(self):
        return f"synthetic band ({len(self.band)} stations, floor {self.floor_dbm:g} dBm)"

    def tune(self, hz):
        self.center_hz = float(hz)
        return True

    def skip(self, n):
        self.n += int(n)

    def read(self, n):
        n = int(n)
        out = np.empty(n, dtype=np.complex64)
        seg = max(1, int(self.rate / 1000))             # keying resolution: 1 ms
        for a in range(0, n, seg):
            b = min(n, a + seg)
            out[a:b] = self._segment(self.n + a, b - a)
        self.n += n
        return out

    def _segment(self, n0, k):
        t0 = n0 / self.rate
        sigma2 = 10 ** ((self.floor_dbm - REF_DBM) / 10) * self.rate / NOISE_BW_HZ
        x = (self.rng.standard_normal(k) + 1j * self.rng.standard_normal(k)) * math.sqrt(sigma2 / 2)
        t = np.arange(k) / self.rate
        for i, s in enumerate(self.band):
            off = s.hz - self.center_hz
            if abs(off) > 0.5 * self.rate or not s.keyed(t0):
                continue
            amp = math.sqrt(10 ** ((s.dbm - REF_DBM) / 10))
            if s.kind == "voice":
                tone, dev = VOICE_TONE_HZ, VOICE_DEV_HZ
            elif s.kind == "cw":
                tone, dev = CW_TONE_HZ, CW_DEV_HZ
            else:
                tone, dev = 0.0, 0.0
            mph = self.mod_ph.get(i, 0.0)
            # FM by a tone: instantaneous phase = 2 pi off t + (dev / tone) sin(2 pi tone t)
            ph = self.carrier_ph.get(i, 0.0) + 2 * np.pi * off * t
            if tone:
                ph = ph + (dev / tone) * np.sin(mph + 2 * np.pi * tone * t)
                self.mod_ph[i] = (mph + 2 * np.pi * tone * k / self.rate) % (2 * np.pi)
            self.carrier_ph[i] = (self.carrier_ph.get(i, 0.0) + 2 * np.pi * off * k / self.rate) % (2 * np.pi)
            x += amp * np.exp(1j * ph)
        return x


def write_sigmf(base, iq, rate, center_hz, dbm_offset=REF_DBM, description="generated test signal"):
    """base.sigmf-data (cf32_le) and base.sigmf-meta; returns the meta path"""
    base = os.fspath(base)
    np.asarray(iq, dtype='<c8').tofile(base + ".sigmf-data")
    meta = {"global": {"core:datatype": "cf32_le", "core:sample_rate": float(rate),
                       "core:version": "1.0.0", "core:description": description,
                       "soapyfang:dbm_offset": float(dbm_offset)},
            "captures": [{"core:sample_start": 0, "core:frequency": float(center_hz)}],
            "annotations": []}
    with open(base + ".sigmf-meta", "w") as f:
        json.dump(meta, f, indent=1)
    return base + ".sigmf-meta"


class SigmfSource(IqSource):
    """Single-frequency memory-mapped SigMF replay, optionally looped."""
    DTYPES = {"cf32_le": (np.dtype("<c8"), 1.0), "ci16_le": (np.dtype("<i2"), 1 / 32768.0),
              "ci8": (np.dtype("i1"), 1 / 128.0)}

    def __init__(self, path, loop=False, dbm_offset=None):
        path = os.fspath(path)
        base = path.removesuffix(".sigmf-meta").removesuffix(".sigmf-data")
        with open(base + ".sigmf-meta") as f:
            meta = json.load(f)
        g = meta["global"]
        dt = g["core:datatype"]
        if dt not in self.DTYPES:
            raise ValueError(f"unsupported SigMF datatype {dt}")
        captures = meta.get("captures", [])
        if len(captures) != 1 or captures[0].get("core:sample_start", 0) != 0:
            raise ValueError("only single-frequency SigMF captures starting at sample 0 are supported")
        self.kind, self.scale = self.DTYPES[dt]
        self.stride = 1 if dt == "cf32_le" else 2
        size = os.path.getsize(base + ".sigmf-data")
        if not size or size % (self.kind.itemsize * self.stride):
            raise ValueError("empty or incomplete SigMF IQ sample")
        self.raw = np.memmap(base + ".sigmf-data", dtype=self.kind, mode="r")
        self.length = len(self.raw) // self.stride
        self.rate = float(g["core:sample_rate"])
        self.center_hz = float(captures[0]["core:frequency"])
        self.dbm_offset = float(dbm_offset if dbm_offset is not None else g.get("soapyfang:dbm_offset", REF_DBM))
        self.loop = loop
        self.pos = 0
        self.path = base

    def _slice(self, start, count):
        raw = self.raw[start*self.stride:(start+count)*self.stride]
        if self.stride == 1:
            return np.asarray(raw, dtype=np.complex64)
        return (raw[::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)) * self.scale

    def describe(self):
        return f"SigMF {os.path.basename(self.path)} ({self.length / self.rate:.1f} s, {'loop' if self.loop else 'once'})"

    def skip(self, n):
        self.pos = (self.pos + int(n)) % self.length if self.loop else min(self.length, self.pos + int(n))

    def read(self, n):
        out = np.zeros(int(n), dtype=np.complex64)
        written = 0
        while written < n:
            k = min(n - written, self.length - self.pos)
            if not k:
                if not self.loop:
                    break
                self.pos = 0
                continue
            out[written:written+k] = self._slice(self.pos, k)
            written += k
            self.pos += k
        return out

    def close(self):
        self.raw._mmap.close()


class NpySource(SigmfSource):
    """Replay archived complex IQ with explicit operator-supplied clock and center."""
    def __init__(self, path, rate, center_hz, loop=False, dbm_offset=None):
        self.raw = np.load(path, mmap_mode="r", allow_pickle=False)
        if not isinstance(self.raw, np.memmap) or self.raw.ndim != 1 or self.raw.dtype.kind != "c" or not len(self.raw):
            if hasattr(self.raw, "close"):
                self.raw.close()
            raise ValueError("NPY IQ must be a nonempty 1-D complex array")
        self.stride = 1
        self.length = len(self.raw)
        self.rate = float(rate)
        self.center_hz = float(center_hz)
        self.dbm_offset = REF_DBM if dbm_offset is None else float(dbm_offset)
        self.loop = loop
        self.pos = 0
        self.path = os.fspath(path)

    def describe(self):
        return f"NPY {os.path.basename(self.path)} ({self.length / self.rate:.1f} s, {'loop' if self.loop else 'once'})"


class SoapySource(IqSource):
    """a SoapySDR receiver. RX only: no transmit stream is ever set up. Its LO sits a quarter of
    the rate above the frequency asked for, so the LO leak and the DC spike stay off the channel
    the radio listens on. On close a Pluto's LO is parked at 2.4 GHz (docs/pluto-bench.md)."""
    live = True
    tunable = True

    def __init__(self, args="driver=rtlsdr", rate=2_400_000, center_hz=440_000_000, gain=None,
                 dbm_offset=None, channel=0, soapy=None):
        if soapy is None:
            import SoapySDR as soapy    # Arch: pacman -S soapysdr soapyplutosdr soapyrtlsdr
        self.lock = threading.RLock()
        self.soapy = soapy
        self.args = args
        self.ch = channel
        self.dev = soapy.Device(args)
        self.dev.setSampleRate(soapy.SOAPY_SDR_RX, channel, float(rate))
        self.rate = float(self.dev.getSampleRate(soapy.SOAPY_SDR_RX, channel))
        if gain is not None:
            self.dev.setGainMode(soapy.SOAPY_SDR_RX, channel, False)
            self.dev.setGain(soapy.SOAPY_SDR_RX, channel, float(gain))
        self.gain = gain
        # uncalibrated: full scale ~ REF_DBM less the gain, until set from a known signal
        self.dbm_offset = float(dbm_offset) if dbm_offset is not None else REF_DBM - float(gain or 0)
        self.center_hz = 0.0
        self.tune(center_hz)
        self.stream = self.dev.setupStream(soapy.SOAPY_SDR_RX, soapy.SOAPY_SDR_CF32, [channel])
        try:
            self.dev.activateStream(self.stream)
        except BaseException:
            self.dev.closeStream(self.stream)
            raise
        self.overflows = 0

    def describe(self):
        g = f", gain {self.gain:g} dB" if self.gain is not None else ""
        return f"SoapySDR {self.args} ({self.rate / 1e6:g} MS/s{g})"

    def tune(self, hz):
        with self.lock:
            lo = float(hz) + 0.25 * self.rate
            self.dev.setFrequency(self.soapy.SOAPY_SDR_RX, self.ch, lo)
            self.center_hz = float(self.dev.getFrequency(self.soapy.SOAPY_SDR_RX, self.ch))
            self.discard = int(0.07 * self.rate)
        return True

    def read(self, n):
        with self.lock:
            out = np.empty(int(n), dtype=np.complex64)
            got = 0
            # A bounded read; never fabricate valid samples after a disconnect.
            for _ in range(24):
                buf = out[got:]
                sr = self.dev.readStream(self.stream, [buf], len(buf), timeoutUs=50_000)
                ret = sr.ret if hasattr(sr, "ret") else sr[0]
                if ret > 0:
                    drop = min(self.discard, ret)
                    self.discard -= drop
                    if drop:
                        buf[:ret-drop] = buf[drop:ret].copy()
                    got += ret - drop
                    if got == n:
                        return out
                elif ret == getattr(self.soapy, "SOAPY_SDR_OVERFLOW", -4):
                    self.overflows += 1
                elif ret != getattr(self.soapy, "SOAPY_SDR_TIMEOUT", -1):
                    raise RuntimeError(f"SoapySDR read failed: {ret}")
            raise RuntimeError("SoapySDR read timed out or overflowed")

    def close(self):
        with self.lock:
            try:
                self.dev.deactivateStream(self.stream)
            finally:
                self.dev.closeStream(self.stream)


def open_source(spec, *, rate=None, center_hz=None, gain=None, dbm_offset=None, loop=False):
    if rate is not None and (not math.isfinite(rate) or not 48_000 <= rate <= 20_000_000):
        raise ValueError("RF IQ rate must be 48 kS/s to 20 MS/s")
    if center_hz is not None and (not math.isfinite(center_hz) or center_hz <= 0):
        raise ValueError("RF center must be positive and finite")
    if dbm_offset is not None and not math.isfinite(dbm_offset):
        raise ValueError("RF dBm offset must be finite")
    if spec.startswith("npy:"):
        if rate is None or center_hz is None or gain is not None:
            raise ValueError("NPY requires --rf-rate and --rf-center; gain does not apply")
        return NpySource(spec[4:], rate, center_hz, loop=loop, dbm_offset=dbm_offset)
    if spec == "synthetic":
        if gain is not None or dbm_offset is not None or loop:
            raise ValueError("synthetic RF has fixed calibration; gain, offset and loop do not apply")
        return SyntheticSource(rate=rate or 1_200_000, center_hz=center_hz or 440_850_000)
    if spec.startswith("sigmf:"):
        if rate is not None or gain is not None or center_hz is not None:
            raise ValueError("SigMF rate and center are in metadata; gain does not apply")
        return SigmfSource(spec[6:], loop=loop, dbm_offset=dbm_offset)
    if spec.startswith("soapy:"):
        if loop:
            raise ValueError("--rf-loop applies only to recording replay")
        return SoapySource(spec[6:], rate=rate or 2_400_000, center_hz=center_hz or 440_850_000,
                           gain=gain, dbm_offset=dbm_offset)
    raise ValueError("RF source must be synthetic, sigmf:PATH, npy:PATH or soapy:ARGS")
