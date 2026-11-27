#!/usr/bin/env python3
"""Bounded, RX-only RTL-SDR capture for a separately authorized K6 dummy-load test.

Uses the actually functioning librtlsdr, not the SoapySDR plugin. Never
issues commands to the radio or touches PTT. .cf32 is little-endian complex64
at 1,024,000 samples/s. Ready file appears only after actual USB IQ arrives.
"""
from __future__ import annotations

import argparse
import ctypes as C
import hashlib
import json
import time
from pathlib import Path

import numpy as np

RATE = 1_024_000
IF_OFFSET = 125_000
CHUNK_BYTES = 262_144  # exactly 131072 complex samples


class NativeRTL:
    def __init__(self, index: int, rf_hz: int, gain_db: float):
        self.lib = C.CDLL("librtlsdr.so.0")
        self.h = C.c_void_p()
        for name, args in (
            ("open", [C.POINTER(C.c_void_p), C.c_uint]),
            ("close", [C.c_void_p]),
            ("set_center_freq", [C.c_void_p, C.c_uint32]),
            ("get_center_freq", [C.c_void_p]),
            ("set_sample_rate", [C.c_void_p, C.c_uint32]),
            ("get_sample_rate", [C.c_void_p]),
            ("set_tuner_gain_mode", [C.c_void_p, C.c_int]),
            ("set_tuner_gain", [C.c_void_p, C.c_int]),
            ("reset_buffer", [C.c_void_p]),
            ("read_sync", [C.c_void_p, C.c_void_p, C.c_int, C.POINTER(C.c_int)]),
        ):
            fn = getattr(self.lib, "rtlsdr_" + name)
            fn.argtypes = args
            fn.restype = C.c_uint32 if name.startswith("get_") else C.c_int
        if self.lib.rtlsdr_open(C.byref(self.h), index):
            raise RuntimeError("RTL-SDR unavailable; no capture or radio operation was started")
        self.buffer = C.create_string_buffer(CHUNK_BYTES)
        try:
            for name, arg in (("set_sample_rate", RATE),
                              ("set_center_freq", rf_hz - IF_OFFSET),
                              ("set_tuner_gain_mode", 1),
                              ("set_tuner_gain", int(round(gain_db * 10)))):
                if getattr(self.lib, "rtlsdr_" + name)(self.h, arg):
                    raise RuntimeError("librtlsdr " + name + " failed")
            self.actual_rate = self.lib.rtlsdr_get_sample_rate(self.h)
            self.actual_center_hz = self.lib.rtlsdr_get_center_freq(self.h)
            if self.actual_rate != RATE or abs(self.actual_center_hz - (rf_hz - IF_OFFSET)) > 100:
                raise RuntimeError("RTL-SDR tuner/readback does not match requested settings")
            if self.lib.rtlsdr_reset_buffer(self.h):
                raise RuntimeError("RTL-SDR buffer reset failed")
        except BaseException:
            self.close()
            raise

    def read(self) -> np.ndarray:
        n = C.c_int()
        rc = self.lib.rtlsdr_read_sync(self.h, self.buffer, CHUNK_BYTES, C.byref(n))
        if rc or n.value != CHUNK_BYTES:
            raise RuntimeError(f"Incomplete RTL-SDR sample block: rc={rc}, bytes={n.value}")
        raw = np.frombuffer(self.buffer.raw, np.uint8).astype(np.float32)
        raw -= 127.5
        return ((raw[::2] + 1j * raw[1::2]) / 128).astype(np.complex64)

    def close(self) -> None:
        if self.h.value:
            self.lib.rtlsdr_close(self.h)
            self.h = C.c_void_p()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--ready", type=Path, required=True)
    p.add_argument("--seconds", type=float, default=9.0)
    p.add_argument("--freq-hz", type=int, default=443_500_000)
    p.add_argument("--gain", type=float, default=20.0)
    p.add_argument("--device-index", type=int, default=0)
    a = p.parse_args(argv)
    if not 0 < a.seconds <= 20 or a.freq_hz != 443_500_000 or not 0 <= a.device_index <= 15:
        p.error("Only bounded 443.500 MHz receive capture (<=20s) is allowed")
    if not 0 <= a.gain <= 40:
        p.error("RTL tuner gain must be 0-40 dB")
    meta = a.out.with_suffix(".json")
    if any(f.exists() for f in (a.out, a.ready, meta)):
        p.error("Refusing to overwrite existing capture or readiness evidence")
    dev = None
    stats = dict(frequency_hz=a.freq_hz, requested_center_hz=a.freq_hz-IF_OFFSET,
                 nominal_if_offset_hz=IF_OFFSET, sample_rate=RATE, gain_db=a.gain,
                 seconds_requested=a.seconds, format="little-endian complex64", device_index=a.device_index,
                 receiver_only=True, radio_control=False, samples=0,
                 complete=False, errors=[], sample_continuity_proved=False)
    try:
        dev = NativeRTL(a.device_index, a.freq_hz, a.gain)
        stats.update(actual_center_hz=dev.actual_center_hz, actual_sample_rate=dev.actual_rate)
        target = round(a.seconds * RATE)
        start = time.monotonic()
        with a.out.open("xb") as output:
            while stats["samples"] < target:
                block = dev.read()  # first verified USB samples before advertising READY
                count = min(len(block), target-stats["samples"])
                block[:count].tofile(output)
                stats["samples"] += count
                if not a.ready.exists():
                    a.ready.write_text(json.dumps(dict(ready=True, actual_center_hz=dev.actual_center_hz,
                        sample_rate=dev.actual_rate, first_samples=count)) + "\n")
                    print("SDR_CAPTURE_READY", a.out, flush=True)
                if time.monotonic()-start > a.seconds+6:
                    raise RuntimeError("Receiver acquisition exceeded wall-clock bound")
        stats["wall_seconds"] = round(time.monotonic()-start, 3)
        stats["file_bytes"] = a.out.stat().st_size
        assert stats["file_bytes"] == stats["samples"]*8
        stats["sha256"] = hashlib.sha256(a.out.read_bytes()).hexdigest()
        stats["complete"] = True
        print("SDR_CAPTURE_DONE", json.dumps(stats), flush=True)
    except Exception as exc:
        stats["errors"].append(str(exc))
        raise
    finally:
        if dev:
            dev.close()
        if a.out.exists():
            stats["file_bytes"] = a.out.stat().st_size
        meta.write_text(json.dumps(stats, indent=2)+"\n")

if __name__ == "__main__":
    main()
