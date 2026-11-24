#!/usr/bin/env python3
"""Flux FSK-05 receive-only RTL-SDR capture and channel baseline report.

Never sends K6 commands, uses a serial port, changes radio state, or keys TX.
A clean SDR baseline is NOT proof of a 50-ohm dummy load or safe attenuation.
Capture I/Q for a future attended, externally authorized conducted test.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
from pathlib import Path
import time

import numpy as np
from scipy.signal import welch

CAPTURE_CENTER_HZ = 433_750_000
TEST_FREQUENCY_HZ = 433_500_000
FS_HZ = 1_024_000
DEFAULT_BUFFER_BYTES = 262_144
MAX_CAPTURE_SECONDS = 12.0


def summarize_raw_iq(raw: np.ndarray, *, center_hz=CAPTURE_CENTER_HZ,
                     rf_hz=TEST_FREQUENCY_HZ, fs=FS_HZ,
                     gain_tenth_db=0) -> dict:
    """Relative, *uncalibrated* receive metrics; never a TX authorization."""
    raw = np.asarray(raw)
    if raw.ndim != 1 or raw.dtype != np.uint8 or raw.size < 32768 or raw.size % 2:
        raise ValueError("expected sufficiently long interleaved uint8 I/Q")
    if not isinstance(center_hz, int) or not isinstance(rf_hz, int) or fs <= 0:
        raise ValueError("invalid frequency/sample rate")
    relative_hz = rf_hz - center_hz
    if abs(relative_hz) >= fs * .45:
        raise ValueError("test frequency outside guarded capture passband")
    # Process up to ~2 seconds for a bounded preflight spectral estimate.
    n = min(raw.size // 2, fs * 2)
    z = (raw[0:2*n:2].astype(np.float32)-127.5 +
         1j*(raw[1:2*n:2].astype(np.float32)-127.5))
    adc_extreme = float(np.mean((raw[:2*n] <= 1) | (raw[:2*n] >= 254)))
    actual_rms = float(np.sqrt(np.mean(np.abs(z)**2)))
    _, power = welch(z, fs=fs, nperseg=8192, noverlap=4096,
                     return_onesided=False, scaling="density")
    frequencies = np.fft.fftfreq(8192, 1 / fs)
    # Mask DC (RTL-SDR bias/center artifacts) and compare local noise median.
    noise_bins = (np.abs(frequencies - relative_hz) <= 60_000)
    center_bins = np.abs(frequencies-relative_hz) <= 30_000
    if not noise_bins.any() or not center_bins.any():
        raise ValueError("invalid FFT band selection")
    local_noise = max(float(np.median(power[noise_bins])), 1e-20)
    peak_idx = int(np.argmax(np.where(center_bins, power, -1)))
    peak_db = 10.0*math.log10(max(float(power[peak_idx]), 1e-20)/local_noise)
    measured_peak_hz = center_hz + float(frequencies[peak_idx])
    # This occupancy check is intentionally conservative: it is advisory,
    # not a calibrated measurement of interference or channel availability.
    noisy = peak_db > 18.0 or adc_extreme > 0.01
    return {
        "analysis": "uncalibrated SDR baseline, not an RF emissions test",
        "source": "receive-only RTL-SDR I/Q",
        "capture_center_hz": center_hz,
        "target_rf_hz": rf_hz,
        "sample_rate_hz": fs,
        "manual_gain_tenth_db": gain_tenth_db,
        "target_channel_peak_hz_estimate": round(measured_peak_hz, 1),
        "target_channel_peak_above_local_median_db": round(peak_db, 2),
        "i_q_rms_relative_adc_counts": round(actual_rms, 3),
        "adc_extreme_sample_fraction": round(adc_extreme, 7),
        "potential_occupied_or_overloaded": bool(noisy),
        "channel_clear_proven": False,
        "calibrated_rf_power_dbm": None,
        "dummy_load_verified": False,
        "attenuation_verified": False,
        "independent_watchdog_verified": False,
        "allows_transmission": False,
        "caveats": [
            "No calibrated sensitivity, RF power, frequency accuracy, harmonic or emission-mask conclusion.",
            "Quiet channel and no ADC clipping do not demonstrate a connected dummy load.",
            "RTL-SDR tuner frequency error and front-end/IF artifacts affect peak frequency.",
            "Separate physical load, coupler, attenuation, legal operating conditions and watchdog checks are mandatory."
        ]
    }


class RtlCapture:
    """A local RTL-SDR handle; always closes on exceptions."""
    def __init__(self, *, device_index=0, center_hz=CAPTURE_CENTER_HZ,
                 fs=FS_HZ, gain_tenth_db=0):
        self.center_hz = center_hz
        self.fs = fs
        self.gain_tenth_db = gain_tenth_db
        self.device_index = device_index
        self.lib = ctypes.CDLL("librtlsdr.so.0")
        self.lib.rtlsdr_get_device_count.restype = ctypes.c_uint
        for name, types, restype in (
            ("open",[ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint],ctypes.c_int),
            ("close",[ctypes.c_void_p],ctypes.c_int),
            ("set_center_freq",[ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int),
            ("set_sample_rate",[ctypes.c_void_p,ctypes.c_uint32],ctypes.c_int),
            ("set_tuner_gain_mode",[ctypes.c_void_p,ctypes.c_int],ctypes.c_int),
            ("set_tuner_gain",[ctypes.c_void_p,ctypes.c_int],ctypes.c_int),
            ("reset_buffer",[ctypes.c_void_p],ctypes.c_int),
            ("read_sync",[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_int,
                          ctypes.POINTER(ctypes.c_int)],ctypes.c_int),
        ):
            fn = getattr(self.lib,"rtlsdr_"+name)
            fn.argtypes = types
            fn.restype = restype
        if hasattr(self.lib, "rtlsdr_set_bias_tee"):
            self.lib.rtlsdr_set_bias_tee.argtypes = [ctypes.c_void_p,ctypes.c_int]
            self.lib.rtlsdr_set_bias_tee.restype = ctypes.c_int
        self.handle = ctypes.c_void_p()

    def __enter__(self):
        count = int(self.lib.rtlsdr_get_device_count())
        if self.device_index < 0 or self.device_index >= count:
            raise RuntimeError(f"RTL-SDR device {self.device_index} not found ({count} available)")
        if self.lib.rtlsdr_open(ctypes.byref(self.handle), self.device_index):
            raise RuntimeError("RTL-SDR device unavailable or already owned")
        try:
            for func, argument in (
                ("set_center_freq",self.center_hz),
                ("set_sample_rate",self.fs),
                ("set_tuner_gain_mode",1),
                ("set_tuner_gain",self.gain_tenth_db),
            ):
                if getattr(self.lib,"rtlsdr_"+func)(self.handle,argument):
                    raise RuntimeError(f"RTL-SDR failed to configure {func}")
            if hasattr(self.lib,"rtlsdr_set_bias_tee"):
                if self.lib.rtlsdr_set_bias_tee(self.handle,0):
                    raise RuntimeError("RTL-SDR bias-tee disable failed")
            if self.lib.rtlsdr_reset_buffer(self.handle):
                raise RuntimeError("RTL-SDR reset_buffer failed")
        except Exception:
            self.__exit__(None,None,None)
            raise
        return self

    def read(self, seconds: float) -> np.ndarray:
        if not 0 < seconds <= MAX_CAPTURE_SECONDS:
            raise ValueError("capture seconds must be between 0 and 12")
        nblocks=math.ceil(seconds*self.fs*2/DEFAULT_BUFFER_BYTES)
        tmp=ctypes.create_string_buffer(DEFAULT_BUFFER_BYTES)
        got=ctypes.c_int()
        buffers=[]
        for _ in range(nblocks):
            result = self.lib.rtlsdr_read_sync(self.handle,tmp,DEFAULT_BUFFER_BYTES,
                                               ctypes.byref(got))
            if result or got.value != DEFAULT_BUFFER_BYTES:
                raise RuntimeError("RTL-SDR capture failed or incomplete (no partial file)")
            buffers.append(np.frombuffer(tmp.raw,dtype=np.uint8).copy())
        return np.concatenate(buffers)

    def __exit__(self, exc_type, exc, tb):
        if self.handle.value:
            self.lib.rtlsdr_close(self.handle)
            self.handle = ctypes.c_void_p()


def save_capture(raw: np.ndarray, prefix: Path, *, summary: dict) -> tuple[Path,Path]:
    iq_path=prefix.with_suffix(".npy")
    report_path=prefix.with_suffix(".json")
    if iq_path.exists() or report_path.exists():
        raise FileExistsError("capture outputs already exist; choose a new --out-prefix")
    # Do not create half of an existing pair on a name collision.
    prefix.parent.mkdir(parents=True,exist_ok=True)
    stored=np.asarray(raw,dtype=np.uint8)
    if not stored.flags.c_contiguous:
        stored=np.ascontiguousarray(stored)
    meta=dict(summary)
    meta.update({
        "capture_only":True,
        "rf_transmission_triggered":False,
        "samples_complex":int(len(stored)//2),
        "captured_duration_seconds":round(len(stored)/(2*summary["sample_rate_hz"]),6),
        "iq_raw_u8_sha256":hashlib.sha256(stored.tobytes()).hexdigest(),
        "file_format":"numpy .npy of uint8 interleaved I then Q",
        "file_name":iq_path.name,
    })
    np.save(iq_path,stored,allow_pickle=False)
    try:
        report_path.write_text(json.dumps(meta,indent=2)+"\n")
    except Exception:
        iq_path.unlink(missing_ok=True)
        raise
    return iq_path,report_path


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-prefix",type=Path,required=True)
    parser.add_argument("--seconds",type=float,default=4.0)
    parser.add_argument("--device-index",type=int,default=0)
    parser.add_argument("--center-hz",type=int,default=CAPTURE_CENTER_HZ)
    parser.add_argument("--target-rf-hz",type=int,default=TEST_FREQUENCY_HZ)
    parser.add_argument("--sample-rate-hz",type=int,default=FS_HZ)
    parser.add_argument("--gain-tenth-db",type=int,default=0)
    args=parser.parse_args(argv)
    if args.out_prefix.suffix:
        parser.error("provide --out-prefix without an extension")
    if (args.out_prefix.with_suffix(".npy").exists() or
        args.out_prefix.with_suffix(".json").exists()):
        parser.error("capture output already exists; refusing to overwrite")
    if not 0 < args.seconds <= MAX_CAPTURE_SECONDS:
        parser.error("capture length must be >0 and <=12 seconds")
    if args.sample_rate_hz not in (FS_HZ,):
        parser.error("this first preflight is fixed to the archived 1024000 S/s setting")
    if args.gain_tenth_db != 0:
        parser.error("this first preflight requires fixed 0 dB RTL-SDR tuner gain")
    if args.center_hz != CAPTURE_CENTER_HZ or args.target_rf_hz != TEST_FREQUENCY_HZ:
        parser.error("this first preflight is fixed to the 433.500/433.750 MHz lab fixture")
    try:
        with RtlCapture(device_index=args.device_index,
                        center_hz=args.center_hz,fs=args.sample_rate_hz,
                        gain_tenth_db=args.gain_tenth_db) as radio:
            data=radio.read(args.seconds)
        summary=summarize_raw_iq(data,center_hz=args.center_hz,
                                 rf_hz=args.target_rf_hz,
                                 fs=args.sample_rate_hz,
                                 gain_tenth_db=args.gain_tenth_db)
        paths=save_capture(data,args.out_prefix,summary=summary)
    except (ValueError,OSError,RuntimeError,FileExistsError) as exc:
        parser.error(str(exc))
    print("RECEIVE-ONLY capture complete; NO TRANSMISSION performed")
    print("IQ:",paths[0])
    print("Report:",paths[1])
    print("Potential occupied/overloaded:",summary["potential_occupied_or_overloaded"])
    print("Target channel peak above local median (dB):",
          summary["target_channel_peak_above_local_median_db"])
    print("RF fixture verified: NO; transmission permitted by this tool: NO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
