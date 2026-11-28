#!/usr/bin/env python3
"""Bounded, RX-ONLY RTL-SDR collection for a supervised GP0 PCM AIR test.

This *only* receives IQ. No radio serial port, firmware/module install, GPIO,
PTT, file deletion, or background receiver. Run instead of the Sotto Monitor:
the same RTL-SDR USB device cannot be opened by both programs.

Start capture *before* the operator launches GP0 PCM AIR, hold the radio's
physical PTT until the guarded 67-packet RF dump finishes (~11-13s). The exact
pre-Sotto PCM is reconstructed only when all CRC-valid packets arrive.

Example (operator-supervised only):
 python tools/k6_gp0_pcm_air_rx.py --seconds 19 --fine-offset-hz -31300 \
   --output-prefix recovery/pcm_air/real-gp0-1k-01
The -31300Hz fine offset is an earlier monitor setting, NOT guaranteed for a
new session. The raw captured RF IQ is retained to investigate decoding.
"""
from __future__ import annotations
import argparse
import ctypes as C
import json
import math
from pathlib import Path
import sys
import time

# Direct `python tools/k6_gp0_pcm_air_rx.py` puts tools/, not the repository
# root, on sys.path. Keep package imports working with both CLI and pytest.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.signal import resample_poly
from tools.k6_gp0_pcm_air import parse_iq_complex64,assemble,write_wav_new

RATE=1_024_000
IF_OFFSET=125_000
OUTPUT_FS=128_000
QUALIFIED_HZ=443_500_000
READ_BYTES=262144


class RtlReceiver:
    def __init__(self,device=0):
        if not isinstance(device,int) or not 0<=device<=15:
            raise ValueError("RTL device index out of range")
        self.lib=C.CDLL("librtlsdr.so.0")
        self.handle=C.c_void_p()
        prototypes={
            "open":([C.POINTER(C.c_void_p),C.c_uint]),
            "close":([C.c_void_p]),
            "set_sample_rate":([C.c_void_p,C.c_uint32]),
            "set_center_freq":([C.c_void_p,C.c_uint32]),
            "set_tuner_gain_mode":([C.c_void_p,C.c_int]),
            "reset_buffer":([C.c_void_p]),
            "read_sync":([C.c_void_p,C.c_void_p,C.c_int,C.POINTER(C.c_int)]),
        }
        for name,args in prototypes.items():
            fn=getattr(self.lib,"rtlsdr_"+name)
            fn.argtypes=args;fn.restype=C.c_int
        if self.lib.rtlsdr_open(C.byref(self.handle),device)!=0:
            raise RuntimeError("RTL-SDR unavailable or in use; stop Sotto Monitor first")
        try:
            self._ok("set_sample_rate",RATE)
            self._ok("set_center_freq",QUALIFIED_HZ-IF_OFFSET)
            self._ok("set_tuner_gain_mode",0)
            self._ok("reset_buffer")
        except Exception:
            self.close()
            raise

    def _ok(self,name,*args):
        if getattr(self.lib,"rtlsdr_"+name)(self.handle,*args):
            raise RuntimeError("RTL-SDR operation failed: "+name)

    def read(self,nbytes):
        if nbytes<=0 or nbytes%2:raise ValueError("IQ count must be positive/even")
        buf=(C.c_ubyte*nbytes)()
        n=C.c_int(0)
        rc=self.lib.rtlsdr_read_sync(self.handle,buf,nbytes,C.byref(n))
        if rc!=0 or n.value!=nbytes:
            raise RuntimeError("RTL-SDR short/failed IQ read")
        return bytes(buf)

    def close(self):
        if self.handle and self.handle.value:
            self.lib.rtlsdr_close(self.handle)
            self.handle=C.c_void_p()


def downconvert_u8iq(raw_u8,*,offset_hz):
    """Convert RTL unsigned I/Q to bounded-memory complex64 shifted to DC.

    Only the output buffer and a small one-shot chunk are allocated, rather
    than complex128 arrays for an entire multi-second RF capture.
    """
    data=np.asarray(raw_u8)
    if data.dtype!=np.uint8 or data.ndim!=1 or not data.size or data.size%2:
        raise ValueError("invalid interleaved RTL unsigned I/Q")
    if not math.isfinite(offset_hz) or abs(offset_hz)>RATE/2:
        raise ValueError("digital tune offset outside RTL Nyquist")
    count=data.size//2
    out=np.empty(count,dtype=np.complex64)
    step=-2*np.pi*offset_hz/RATE
    for start in range(0,count,131072):
        end=min(count,start+131072)
        pairs=data[2*start:2*end].reshape(-1,2).astype(np.float32)
        out[start:end].real=(pairs[:,0]-127.5)/128.0
        out[start:end].imag=(pairs[:,1]-127.5)/128.0
        phase=(np.arange(start,end,dtype=np.float64)*step)
        out[start:end]*=np.exp(1j*phase).astype(np.complex64)
    return out


def capture_iq(receiver,seconds,*,fine_offset_hz):
    if not math.isfinite(seconds) or not 4<=seconds<=35:
        raise ValueError("capture duration must be 4–35 seconds")
    if not math.isfinite(fine_offset_hz) or abs(fine_offset_hz)>55000:
        raise ValueError("fine offset must be within ±55 kHz")
    nbytes=int(math.ceil(seconds*RATE*2/READ_BYTES))*READ_BYTES
    frames=[]
    start=time.monotonic()
    total=0
    while total<nbytes:
        if time.monotonic()-start>seconds*3+8:
            raise TimeoutError("RTL-SDR capture exceeded bounded wall deadline")
        buf=receiver.read(READ_BYTES)
        frames.append(np.frombuffer(buf,dtype=np.uint8).copy())
        total+=len(buf)
    data=np.concatenate(frames)
    # Mix in bounded blocks with complex64 storage, avoiding whole-file
    # float64/complex128 intermediate arrays (~GB at 19 seconds).
    tuned=downconvert_u8iq(data,offset_hz=IF_OFFSET+fine_offset_hz)
    del frames,data
    iq=resample_poly(tuned,1,8).astype(np.complex64)
    return iq,dict(rx_only=True,requested_seconds=seconds,
                   actual_iq_seconds=round(len(iq)/OUTPUT_FS,5),
                   original_sdr_rate_hz=RATE,post_tune_rate_hz=OUTPUT_FS,
                   if_offset_hz=IF_OFFSET,fine_offset_hz=fine_offset_hz,
                   measured_wall_elapsed_seconds=round(time.monotonic()-start,3),
                   rf_center_hz=QUALIFIED_HZ)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds",type=float,default=19)
    p.add_argument("--fine-offset-hz",type=float,default=-31300)
    p.add_argument("--device",type=int,default=0)
    p.add_argument("--output-prefix",type=Path,required=True,
                   help="NEW IQ .c64, output .wav and .json paths; refusal on overwrite")
    a=p.parse_args()
    stem=a.output_prefix
    iqpath=stem.with_suffix(".c64")
    wavpath=stem.with_suffix(".wav")
    jsonpath=stem.with_suffix(".json")
    for path in (iqpath,wavpath,jsonpath):
        if path.exists():
            raise FileExistsError("refusing to overwrite existing evidence "+str(path))
        if not path.parent.is_dir():
            raise ValueError("create output directory before capturing")
    receiver=RtlReceiver(a.device)
    try:
        iq,metadata=capture_iq(receiver,a.seconds,fine_offset_hz=a.fine_offset_hz)
    finally:
        receiver.close()
    # Preserve the IQ evidence even if packet receive is not complete.
    with iqpath.open("xb") as f:f.write(iq.tobytes())
    metadata.update(iq_file=iqpath.name)
    try:
        parsed,rfinfo=parse_iq_complex64(iqpath)
        summary,pcm=assemble(parsed)
        write_wav_new(wavpath,pcm)
        metadata.update(summary);metadata.update(rfinfo)
        metadata["complete_pcm"]=True
    except (ValueError,RuntimeError) as e:
        metadata.update(complete_pcm=False,receive_error=str(e))
    with jsonpath.open("x") as f:
        f.write(json.dumps(metadata,indent=2,sort_keys=True)+"\n")
    print(json.dumps(metadata,indent=2,sort_keys=True))
    if not metadata["complete_pcm"]:
        raise SystemExit(2)


if __name__=="__main__":main()
