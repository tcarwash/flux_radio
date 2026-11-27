#!/usr/bin/env python3
"""Fixed-gain RTL-SDR RECEIVE-ONLY sweep, intended for attended K6 PTT checks.

Run baseline while radio PTT released, then TX sweep while operator manually
holds PTT, same RF coupling, same gain. No radio serial, GPIO, PTT, flashing,
or SDR transmit calls. Each frequency bin is retuned and sampled once: the
sweep can *miss* intermittent RF and cannot prove no RF exists.
"""
from __future__ import annotations
import argparse, ctypes as C, json, hashlib, math, time
from pathlib import Path
import numpy as np
from scipy.signal import welch

FS=1_024_000
READ_BYTES=262_144
# Sweep 430..456MHz in 0.8MHz steps, each center +/-0.35MHz is high-confidence
# IF region. This covers common repeater splits near 443.500.
CENTERS=tuple(range(430_000_000, 456_000_001, 800_000))

def read_bins(*,device: int=0,gain_db: float=20,centers=CENTERS):
    if not 0<=device<=15 or not 0<=gain_db<=40 or not centers:
        raise ValueError("invalid sweep configuration")
    lib=C.CDLL("librtlsdr.so.0")
    args={
      "open":([C.POINTER(C.c_void_p),C.c_uint]),
      "close":([C.c_void_p]),
      "set_sample_rate":([C.c_void_p,C.c_uint32]),
      "set_center_freq":([C.c_void_p,C.c_uint32]),
      "get_center_freq":([C.c_void_p]),
      "set_tuner_gain_mode":([C.c_void_p,C.c_int]),
      "set_tuner_gain":([C.c_void_p,C.c_int]),
      "reset_buffer":([C.c_void_p]),
      "read_sync":([C.c_void_p,C.c_void_p,C.c_int,C.POINTER(C.c_int)]),
    }
    for name,types in args.items():
        f=getattr(lib,"rtlsdr_"+name);f.argtypes=types
        f.restype=C.c_uint32 if name=="get_center_freq" else C.c_int
    h=C.c_void_p()
    if lib.rtlsdr_open(C.byref(h),device):
        raise RuntimeError("RTL-SDR unavailable")
    rows=[]
    try:
        for n,*a in (
          ("set_sample_rate",FS),
          ("set_tuner_gain_mode",1),
          ("set_tuner_gain",int(round(gain_db*10))),
        ):
            if getattr(lib,"rtlsdr_"+n)(h,*a):
                raise RuntimeError("RTL-SDR setup failure: "+n)
        buffer=(C.c_ubyte*READ_BYTES)()
        got=C.c_int()
        for center in centers:
            if lib.rtlsdr_set_center_freq(h,center):
                raise RuntimeError("tune failed "+str(center))
            actual=int(lib.rtlsdr_get_center_freq(h))
            if abs(actual-center)>200:
                raise RuntimeError("tuner readback mismatch")
            time.sleep(.045)
            if lib.rtlsdr_reset_buffer(h):
                raise RuntimeError("reset failed")
            if lib.rtlsdr_read_sync(h,buffer,READ_BYTES,C.byref(got)) or got.value!=READ_BYTES:
                raise RuntimeError("IQ read failed")
            raw=np.ctypeslib.as_array(buffer).reshape(-1,2).astype(np.float32)
            x=((raw[:,0]-127.5)+1j*(raw[:,1]-127.5))/128.
            x=np.asarray(x,dtype=np.complex64)
            f,P=welch(x,fs=FS,nperseg=8192,return_onesided=False)
            # Exclude DC and gross tuner-edge artifacts, but not 128k spurs:
            # baseline sweep allows those spurs to cancel in matched comparison.
            use=(abs(f)>25_000)&(abs(f)<355_000)
            p=np.maximum(P[use],1e-20)
            freqs=f[use]
            best=np.argsort(p)[-5:][::-1]
            rows.append({"center_hz":center,"actual_center_hz":actual,
                "power":float(np.mean(abs(x)**2)),
                "median_psd":float(np.median(p)),
                "peaks":[{"rf_hz":round(float(center+freqs[k]),1),
                          "offset_hz":round(float(freqs[k]),1),
                          "psd":float(p[k])} for k in best]})
            print(f"BIN {center} ({len(rows)}/{len(centers)})",flush=True)
    finally:
        lib.rtlsdr_close(h)
    return rows


def compare(baseline: dict,tx:dict) -> list:
    a=baseline["bins"];b=tx["bins"]
    if len(a)!=len(b) or len(a)!=len(CENTERS) or baseline["gain_db"]!=tx["gain_db"]:
        raise ValueError("not matched baseline/TX scans")
    r=[]
    for x,y in zip(a,b):
        if x["center_hz"]!=y["center_hz"]:
            raise ValueError("centers differ")
        ratio=10*math.log10(max(y["power"],1e-20)/max(x["power"],1e-20))
        # frequency-local PSD contrast: not an absolute carrier detector.
        # If a candidate frequency is already present in baseline, warning.
        # Channel frequency response/tuning can change between sweeps.
        peaks=[]
        for p in y["peaks"]:
            peaks.append({**p,"contrast_to_local_median_db":round(
                10*math.log10(max(p["psd"],1e-20)/max(y["median_psd"],1e-20)),2)})
        r.append({"center_hz":x["center_hz"],"power_gain_db":round(ratio,3),"peaks":peaks})
    return sorted(r,key=lambda k:k["power_gain_db"],reverse=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode",choices=("baseline","tx"),required=True)
    p.add_argument("--out",type=Path,required=True)
    p.add_argument("--baseline",type=Path)
    p.add_argument("--gain-db",type=float,default=20.)
    a=p.parse_args()
    if a.out.exists():p.error("output exists")
    if a.mode=="tx" and not a.baseline:p.error("TX comparison needs baseline")
    d={"schema":1,"mode":a.mode,"rx_only":True,"radio_control":False,
       "sample_rate":FS,"gain_db":a.gain_db,"frequency_range_hz":[min(CENTERS),max(CENTERS)],
       "attenuation_and_dummy_load_must_be_verified_by_operator":True,
       "scan_is_not_simultaneous_and_may_miss_short_rf":True,"bins":read_bins(gain_db=a.gain_db)}
    if a.mode=="tx":
        base=json.loads(a.baseline.read_text())
        d["comparison"]=compare(base,d)
        print("STRONGEST_TX_DIFFERENCES",json.dumps(d["comparison"][:8],indent=2),flush=True)
    with a.out.open("x") as file:file.write(json.dumps(d,indent=2,sort_keys=True)+"\n")
    print("FINISHED",a.mode,len(d["bins"]),"bins",flush=True)

if __name__=="__main__":main()
