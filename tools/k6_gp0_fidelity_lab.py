#!/usr/bin/env python3
"""Offline, CRC-gated GP0 popcount fidelity sweep with genuine firmware Rust.

Reads private RF-decoded source groups, but writes only non-reversible
statistics and hashes. Candidate filters are generated in TEMPORARY HOST RUST
source: no K6 firmware changes, image builds, PTT, serial, USB or SPI actions.
The real Rust Decimator and dec_push functions remain intact except for a
bounded, marked candidate prefilter replacing only the unfiltered x=raw path.
All candidates are hypothetical until independently linked/timed on ARM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from scipy.optimize import minimize_scalar

from tools.k6_gp0_noise_filter_model import SOURCE, build_runner, compile_variants, run
from tools.k6_gp0_popcount_air import parse_physical, N_SAMPLES, SAMPLES_PER_PACKET

RAW_TONES = (
    ("gen-500-20261127-03", 500, False),
    ("gen-1000-20261127-01", 1000, True),
    ("gen-2800-20261127-01", 2800, False),
)
OFF_SOURCE = "gen-off-20261127-01"
# Integer symmetric low-pass kernels, suggested for *offline* study only.
FIR_CANDIDATES = {
    "fir3_triangle": (1, 2, 1),
    "fir5_binomial": (1, 4, 6, 4, 1),
    "fir7_binomial": (1, 6, 15, 20, 15, 6, 1),
    "fir5_box": (1, 1, 1, 1, 1),
    "fir7_box": (1, 1, 1, 1, 1, 1, 1),
    "fir9_box": (1, 1, 1, 1, 1, 1, 1, 1, 1),
}
# IIR alpha = 2**(-shift); each stage uses signed Q7 state.
IIR_CANDIDATES = {
    "iir1_half": (1, 1),
    "iir2_half": (2, 1),
    "iir1_quarter": (1, 2),
    "iir2_quarter": (2, 2),
}
GROUP_DENOMINATOR = 87260
GROUP_WEIGHT = 7500
TRIM_PCM = 32  # ~4ms startup settling, applied identically to every filter


def load_groups(root: Path, name: str, partial: bool) -> tuple[np.ndarray, dict]:
    """Use only complete raw files or the longest *contiguous* verified RF run."""
    if partial:
        framed = (root / (name + "-accepted66.bin")).read_bytes()
        if not framed or len(framed) % 66:
            raise ValueError("invalid CRC-approved RF frame file length")
        packets = [parse_physical(framed[i:i+66]) for i in range(0,len(framed),66)]
        if not all(packets):
            raise ValueError("frame failed independent CRC validation")
        identities = {(p["token"],p["capture_100us"],p["samples"],p["fs"]) for p in packets}
        if len(identities) != 1:
            raise ValueError("mixed sessions or timing")
        by_seq: dict[int, bytes] = {}
        for p in packets:
            if p["seq"] in by_seq and by_seq[p["seq"]] != p["payload"]:
                raise ValueError("conflicting packet")
            by_seq[p["seq"]] = p["payload"]
        chunks: list[list[int]] = []
        for seq in sorted(by_seq):
            if not chunks or seq != chunks[-1][-1]+1:
                chunks.append([])
            chunks[-1].append(seq)
        seqs = max(chunks,key=lambda xs:(len(xs),-xs[0]))
        data = b"".join(by_seq[i] for i in seqs)
        rate = N_SAMPLES*10000/packets[0]["capture_100us"]
        sample = np.frombuffer(data,dtype=np.uint8).copy()
        assert len(sample) == sum(len(by_seq[i]) for i in seqs)
        details = dict(complete=False,crc_verified_packets=len(by_seq),
                       verified_run=[seqs[0],seqs[-1]],all_missing=sorted(set(range(86))-set(by_seq)),
                       sample_sha256=hashlib.sha256(data).hexdigest(),
                       timing_ticks_100us=packets[0]["capture_100us"],groups_per_second=rate)
    else:
        manifest=json.loads((root/(name+".json")).read_text())
        data=(root/(name+".bin")).read_bytes()
        if len(data)!=N_SAMPLES or manifest["crc_accepted"] != 86 or not manifest["complete_popcounts"]:
            raise ValueError("not a complete CRC-verified capture")
        if hashlib.sha256(data).hexdigest()!=manifest["raw_popcount_sha256"]:
            raise ValueError("raw capture digest does not match receiver")
        sample=np.frombuffer(data,dtype=np.uint8).copy()
        rate = N_SAMPLES*10000/manifest["capture_duration_ticks_100us"]
        details=dict(complete=True,crc_verified_packets=86,verified_run=[0,85],
                     sample_sha256=hashlib.sha256(data).hexdigest(),
                     timing_ticks_100us=manifest["capture_duration_ticks_100us"],groups_per_second=rate)
    if len(sample)<800 or np.any(sample>64):
        raise ValueError("too few verified groups or invalid popcount")
    details["source"] = name
    details["verified_raw_groups"] = int(len(sample))
    return sample,details


def candidate_source(base: str, *, kernel: tuple[int,...] | None = None,
                     iir: tuple[int,int] | None = None) -> str:
    """Modify a *temporary* source copy of actual firmware Rust, not repo code."""
    if (kernel is None)==(iir is None):
        raise ValueError("select exactly one experimental filter")
    if kernel is not None and (len(kernel)>11 or not kernel or
                               sum(kernel)<=0 or any(c<0 or c>32 for c in kernel)):
        raise ValueError("invalid kernel")
    if iir is not None and (not 1<=iir[0]<=3 or not 1<=iir[1]<=3):
        raise ValueError("invalid iir")
    decl="    fs:u32,phase:u32,area:i32,dc:i32,samples:u32,clipped:u32,"
    init="    fs:0,phase:0,area:0,dc:0,samples:0,clipped:0,"
    old_x="    let x=raw;"
    old_shift="    let num=mag<<8;"
    if any(base.count(key)!=1 for key in (decl,init,old_x,old_shift)):
        raise ValueError("firmware decimator changed: unsupported source shape")
    base=base.replace(decl,decl+"\n    lab_taps:[i8;11],lab_head:u8,lab_lp1:i32,lab_lp2:i32,lab_lp3:i32,",1)
    base=base.replace(init,init+"\n    lab_taps:[0;11],lab_head:0,lab_lp1:0,lab_lp2:0,lab_lp3:0,",1)
    if kernel is not None:
        n=len(kernel);den=sum(kernel)
        code=f"""    let x={{
        const N:usize={n}; const DEN:i32={den};
        const W:[i32;N]=[{",".join(map(str,kernel))}];
        let h=d.lab_head as usize;
        d.lab_taps[h]=raw as i8;
        d.lab_head=if h+1==N{{0}}else{{(h+1) as u8}};
        let mut acc=0i32;
        let mut j=0usize;
        while j<N{{
            let pos=(h+N-j)%N;
            acc+=W[j]*(d.lab_taps[pos] as i32);
            j+=1;
        }}
        // Q7, same units/range as the production AA3 filter.
        acc*128/DEN
    }};"""
    else:
        stages, shift=iir
        code="    let x={\n"
        for k in range(stages):
            f=f"lab_lp{k+1}"
            prev="(raw<<7)" if k==0 else f"d.lab_lp{k}"
            code+=f"        d.{f}+=({prev}-d.{f})>>{shift};\n"
        code+=f"        d.lab_lp{stages}\n    }};"
    base=base.replace(old_x,code,1).replace(old_shift,"    let num=mag<<1;",1)
    return base


def late_box_source(base: str, n: int) -> str:
    """Host-only O(1) running-sum FIR with the divisor deferred to PCM rate."""
    if n not in (5,7):
        raise ValueError("only measured small boxcar candidates")
    decl="    fs:u32,phase:u32,area:i32,dc:i32,samples:u32,clipped:u32,"
    init="    fs:0,phase:0,area:0,dc:0,samples:0,clipped:0,"
    old_q="    let q=(if d.fs==FS {gp0_div_87260(num)} else {udiv(num,d.fs)}) as i32;"
    for token in (decl,init,"    let x=raw;",old_q):
        if base.count(token)!=1:
            raise ValueError("firmware changed; do not generate unchecked candidate")
    base=base.replace(decl,decl+"\n    late_taps:[i8;7],late_head:u8,late_sum:i16,",1)
    base=base.replace(init,init+"\n    late_taps:[0;7],late_head:0,late_sum:0,",1)
    base=base.replace("    let x=raw;",f"""    let x={{
        let h=d.late_head as usize;
        let old=d.late_taps[h] as i16;
        d.late_taps[h]=raw as i8;
        d.late_head=if h+1=={n}{{0}}else{{(h+1) as u8}};
        d.late_sum=d.late_sum+(raw as i16)-old;
        d.late_sum as i32
    }};""",1)
    base=base.replace("    let num=mag<<8;","    let num=mag;",1)
    base=base.replace(old_q,f"""    let q={{
        let whole=if d.fs==FS {{gp0_div_87260(mag)}} else {{udiv(mag,d.fs)}};
        let rem=mag-whole*d.fs;
        let fraction=if d.fs==FS {{gp0_div_87260(rem<<8)}} else {{udiv(rem<<8,d.fs)}};
        (((whole<<8)+fraction)/{n}) as i32
    }};""",1)
    return base


def compile_lab(where: Path) -> dict[str,Path]:
    progs = compile_variants(where)
    actual=build_runner(SOURCE.read_text())
    candidates = {name:dict(kernel=k) for name,k in FIR_CANDIDATES.items()}
    candidates.update({name:dict(iir=spec) for name,spec in IIR_CANDIDATES.items()})
    candidates.update({"fir5_box_late":dict(late=5),"fir7_box_late":dict(late=7)})
    for name,opts in candidates.items():
        source=where/(name+".rs")
        if "late" in opts:
            source.write_text(late_box_source(actual,opts["late"]))
        else:
            source.write_text(candidate_source(actual,**opts))
        exe=where/name
        p=subprocess.run(["rustc","--edition=2021","-O",
            "--cfg","k6_sotto_gp0_fast_isr","--cfg","k6_sotto_gp0_clock_7500",
            str(source),"-o",str(exe)],capture_output=True,text=True,timeout=45)
        if p.returncode:
            raise RuntimeError(f"rustc failed ({name}): {p.stderr[:1500]}")
        progs[name]=exe
    return progs


def fit_tone(x:np.ndarray,fs:float,hz:float,*,trim:int=TRIM_PCM) -> dict:
    if not math.isfinite(fs) or fs<=2*hz or x.ndim!=1 or len(x)-trim<120:
        raise ValueError("bad tone fixture")
    y=x[trim:].astype(float)
    t=np.arange(len(y),dtype=float)/fs
    def cost(f:float):
        basis=np.column_stack((np.sin(2*np.pi*f*t),np.cos(2*np.pi*f*t),np.ones(len(y))))
        c=np.linalg.lstsq(basis,y,rcond=None)[0]
        err=y-basis@c
        return float(np.mean(err*err)),float(math.hypot(*c[:2])),float(np.std(err))
    opt=minimize_scalar(lambda f:cost(f)[0],bounds=(hz*.97,hz*1.03),method="bounded")
    _,peak,residual=cost(opt.x)
    return dict(fitted_hz=round(float(opt.x),4),tone_peak_fs=peak,
                residual_rms_fs=residual,snr_db=20*math.log10(max(peak,1e-12)/(math.sqrt(2)*max(residual,1e-12))))


def evaluate(root:Path)->dict:
    inputs = [(name,hz,*load_groups(root,name,partial)) for name,hz,partial in RAW_TONES]
    off,offinfo=load_groups(root,OFF_SOURCE,True)
    out = dict(schema=1,offline_only=True,device_contact=False,
        source="CRC-verified physical K6 GP0 popcounts; exact current Rust decimator + temporary Q7 candidates",
        control=offinfo,tones={name:{"frequency_hz":hz,**meta} for name,hz,counts,meta in inputs},
        filters={},synthetic_probes={},warnings=[
          "Only 32-44ms continuous raw windows; no direct speech intelligibility measurement",
          "The 1kHz and generator-OFF recordings contain CRC holes: longest verified spans only",
          "Candidate Rust edits are host-side what-ifs, NOT linked ARM firmware or timing qualified",
          "Sample-level input PDM phase (64 individual bits) is irrecoverable from 64-bit popcounts",
          "Tone selection/evaluation use the same captures; not an independently held-out dataset",
          "Prospective 7500 clock weight modeled, not flashed; radio build remains unchanged",
          "Raw RF files, PCM speech files and listening previews remain local and private",
        ])
    with tempfile.TemporaryDirectory(prefix="gp0-fidelity-lab-") as tmp:
        progs=compile_lab(Path(tmp))
        # Explicitly SYNTHETIC tones injected into an actual verified OFF noise
        # fragment; these are stress probes, NOT physical voice-band captures.
        synth={}
        for hz in (2000,3200):
            t=np.arange(len(off))/offinfo["groups_per_second"]
            mixed=np.clip(np.rint(off.astype(float)+3.2*np.sin(2*np.pi*hz*t)),0,64).astype(np.uint8)
            synth[str(hz)]=mixed
            out["synthetic_probes"][str(hz)]={"constructed_from":"verified generator-OFF popcounts plus generated 3.2-count sine",
                "not_a_physical_tone_measurement":True,"input_groups":len(mixed),"candidate_modes":{}}
        for mode,exe in progs.items():
            row={"tones":{}}
            for name,hz,groups,meta in inputs:
                pcm=run(exe,groups).astype(float)/32768
                rate=meta["groups_per_second"]*GROUP_WEIGHT/GROUP_DENOMINATOR
                row["tones"][str(hz)]=fit_tone(pcm,rate,hz)
            pcm0=run(exe,off).astype(float)/32768
            row["off_pcm_rms_fs"]=float(np.std(pcm0[TRIM_PCM:]))
            row["output_sample_count_off"]=len(pcm0)
            for fstr,counts in synth.items():
                pcm=run(exe,counts).astype(float)/32768
                rate=offinfo["groups_per_second"]*GROUP_WEIGHT/GROUP_DENOMINATOR
                out["synthetic_probes"][fstr]["candidate_modes"][mode]=fit_tone(pcm,rate,float(fstr))
            out["filters"][mode]=row
    baseline=out["filters"]["legacy"]
    for fstr,data in out["synthetic_probes"].items():
        b=data["candidate_modes"]["legacy"]
        for mode,fit in data["candidate_modes"].items():
            fit["snr_gain_db"]=round(fit["snr_db"]-b["snr_db"],4)
            fit["tone_attenuation_db"]=round(20*math.log10(fit["tone_peak_fs"]/b["tone_peak_fs"]),4)
    for mode,row in out["filters"].items():
        for freq,r in row["tones"].items():
            b=baseline["tones"][freq]
            r["snr_gain_db"]=round(r["snr_db"]-b["snr_db"],4)
            r["tone_attenuation_db"]=round(20*math.log10(max(r["tone_peak_fs"],1e-15)/max(b["tone_peak_fs"],1e-15)),4)
            r["tone_peak_fs"]=round(r["tone_peak_fs"],7)
            r["residual_rms_fs"]=round(r["residual_rms_fs"],7)
        gains=[r["snr_gain_db"] for r in row["tones"].values()]
        # Conservative exploratory score: maximize the worst verified band SNR,
        # without claiming speech intelligibility (no speech-reference available).
        row["worst_band_snr_gain_db"]=round(min(gains),4)
        row["mean_band_snr_gain_db"]=round(float(np.mean(gains)),4)
        row["qualifies_preliminary_gate"]=(
            min(gains)>=-.20 and
            row["tones"]["2800"]["tone_attenuation_db"]>=-1.0 and
            row["tones"]["500"]["snr_gain_db"]>=0 and
            row["tones"]["1000"]["snr_gain_db"]>=0)
        row["off_pcm_rms_fs"]=round(row["off_pcm_rms_fs"],7)
    return out


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-root",type=Path,required=True,help="private CRC-verified source directory")
    ap.add_argument("--json-out",type=Path,required=True,help="NEW metrics-only report; never overwrite")
    args=ap.parse_args()
    if args.json_out.exists():ap.error("output exists")
    result=evaluate(args.raw_root)
    args.json_out.parent.mkdir(parents=True,exist_ok=True)
    with args.json_out.open("x") as f:
        f.write(json.dumps(result,indent=2,sort_keys=True)+"\n")
    for name,r in sorted(result["filters"].items(),key=lambda v:v[1]["worst_band_snr_gain_db"],reverse=True):
        print(f"{name:19s} minSNR {r['worst_band_snr_gain_db']:+6.2f}dB mean {r['mean_band_snr_gain_db']:+6.2f}dB "
              f"upper_loss {r['tones']['2800']['tone_attenuation_db']:+6.2f}dB "
              f"offRMS {r['off_pcm_rms_fs']:.5f} {'PASS' if r['qualifies_preliminary_gate'] else 'hold'}")

if __name__=="__main__":
    main()
