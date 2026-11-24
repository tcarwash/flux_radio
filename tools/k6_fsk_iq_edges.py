#!/usr/bin/env python3
"""Offline known-payload-assisted phase/FSK analysis of Flux archived SDR IQ.

Never accesses radios, serial ports, TX, or attached SDRs. Prior timing is
derived from a matched-payload search: not blind BER or calibrated TX deviation.
A phase-bridge residual includes receiver filtering and cannot prove a reset.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, resample_poly, sosfiltfilt
from k6_rf_matrix_decode import payload

RAW_RATE = 1_024_000
FS = 128_000
CARRIER_SHIFT = 280_375.0


def error_windows(errors):
    err = np.asarray(errors, dtype=bool)
    at = np.where(err)[0]
    return dict(all=int(err.sum()), first_32=int(err[:32].sum()),
                first_64=int(err[:64].sum()), first_168=int(err[:168].sum()),
                after_168=int(err[168:].sum()),
                last_error_bit=int(at[-1]) if len(at) else None)


def bridge_phase(ph, prev, following, edge, bit_samples):
    """Linear RF phase from each symbol interior, extrapolated to one time."""
    vals = []
    for center in (prev, following):
        a = int(np.ceil(center - .24*bit_samples))
        b = int(np.floor(center + .24*bit_samples)) + 1
        if a < 0 or b > len(ph) or b-a < 5:
            return None
        xx = np.arange(a, b, dtype=float) - edge
        mat = np.column_stack((xx, np.ones(len(xx))))
        vals.append(float(np.linalg.lstsq(mat, ph[a:b], rcond=None)[0][1]))
    return float(np.angle(np.exp(1j*(vals[1]-vals[0]))))


def analyze_iq(iq, bits, *, baud, first_center_s, fs=FS, settled_from=168):
    bits = np.asarray(bits)
    iq = np.asarray(iq)
    if (bits.ndim != 1 or len(bits) < 32 or not np.issubdtype(bits.dtype, np.integer)
            or np.any(bits > 1) or np.any(bits < 0)
            or iq.ndim != 1 or len(iq) < 100 or fs/baud < 12):
        raise ValueError("invalid bits, IQ, or sampling clock")
    bits = bits.astype(np.uint8)
    bit_samples = fs/baud
    centers = (first_center_s+np.arange(len(bits))/baud)*fs
    if centers[0] < 1 or centers[-1]+2*bit_samples >= len(iq):
        raise ValueError("known payload extends beyond I/Q")
    indices = np.rint(centers).astype(int)
    fraw = np.angle(iq[1:]*np.conj(iq[:-1]))*fs/(2*np.pi)
    smooth_samples = max(3, int(round(.2*bit_samples)))
    freq = uniform_filter1d(fraw,size=smooth_samples)
    fbits = freq[indices]
    settled_from = min(max(0, settled_from), len(bits)//3)
    stable = np.arange(settled_from,len(bits))
    lo = float(np.median(fbits[stable][bits[stable] == 0]))
    hi = float(np.median(fbits[stable][bits[stable] == 1]))
    if not np.isfinite([lo,hi]).all() or abs(hi-lo) < 10:
        raise ValueError("no resolvable FSK separation")
    midpoint = (lo+hi)/2
    polarity = 1 if hi > lo else -1
    errors = ((fbits-midpoint)*polarity > 0).astype(np.uint8) != bits
    amp = abs(iq)
    noise_end = min(int(fs*.5),int(centers[0])-int(bit_samples))
    floor = float(np.median(amp[max(0,noise_end//4):noise_end]))
    a = amp[indices]
    left,right = np.array_split(stable,2)
    def middle_rate(rows):
        return float((np.median(fbits[rows][bits[rows]==0])+
                      np.median(fbits[rows][bits[rows]==1]))/2)
    drift = middle_rate(right)-middle_rate(left)

    # Known isolated 000111 / 111000 edges after startup and high-SNR controls.
    limit = int(round(.7*bit_samples))
    offsets = np.arange(-limit,limit+1)
    transitions, controls = [], []
    for k in range(max(3,settled_from),len(bits)-3):
        if (not np.all(bits[k-3:k]==bits[k-1])
            or not np.all(bits[k:k+3]==bits[k])
            or min(a[k],a[k-1]) < max(floor*1.5,1e-8)):
            continue
        edge = .5*(centers[k-1]+centers[k])
        ix = np.rint(edge+offsets).astype(int)
        if ix.min()<0 or ix.max()>=len(freq):
            continue
        if bits[k]!=bits[k-1]:
            sign=1 if bits[k]==1 else -1
            curve=sign*polarity*(freq[ix]-midpoint)/(abs(hi-lo)/2)
            transitions.append((k,edge,curve))
        else:
            controls.append((k,edge))
    edge_shape={}
    edge_time=0.
    if len(transitions)>=3:
        curve=np.median(np.vstack([v[2] for v in transitions]),axis=0)
        times={}
        for level in (-.8,0.,.8):
            found=np.where(curve>=level)[0]
            if len(found):
                times[level]=float(offsets[found[0]])
        if len(times)==3:
            edge_time=times[0.]
            edge_shape={
                "rise_10_to_90_us_approx":round((times[.8]-times[-.8])*1e6/fs,2),
                "half_level_crossing_us_relative_bit_mid":round(edge_time*1e6/fs,2),
                "method":"median frequency transition over settled isolated edges"
            }
    phase={
        "isolated_transition_count":len(transitions),
        "same_bit_control_count":len(controls),
        "proves_phase_continuity":False,
        "caveat":"Linear RF phase bridge also includes analog filtering, timing and receiver noise."
    }
    if transitions and controls:
        ph=np.unwrap(np.angle(iq))
        def residuals(events):
            v=[]
            for k,edge,*_ in events:
                d=bridge_phase(ph,centers[k-1],centers[k],edge+edge_time,bit_samples)
                if d is not None:
                    v.append(abs(d))
            return np.asarray(v)
        tr,ctl=residuals(transitions),residuals(controls)
        if len(tr):
            phase["median_abs_transition_bridge_rad"]=round(float(np.median(tr)),4)
            phase["p90_abs_transition_bridge_rad"]=round(float(np.quantile(tr,.9)),4)
        if len(ctl):
            phase["median_abs_control_bridge_rad"]=round(float(np.median(ctl)),4)
    return {
        "known_assisted_center_s":round(first_center_s,8),
        "known_assisted_bit_rate_hz":baud,
        "smoothing_samples":smooth_samples,
        "settled_region_from_bit":settled_from,
        "fsk_mark_0_hz_baseband":round(lo,1),
        "fsk_mark_1_hz_baseband":round(hi,1),
        "fsk_mark_separation_hz":round(abs(hi-lo),1),
        "center_drift_hz_settled":round(drift,1),
        "median_startup_to_noise_amplitude":round(float(np.median(a[:32]))/max(floor,1e-9),2),
        "median_settled_to_noise_amplitude":round(float(np.median(a[stable]))/max(floor,1e-9),2),
        "simple_discriminator_error_windows":error_windows(errors),
        "simple_discriminator_settled_errors":int(errors[stable].sum()),
        "simple_discriminator_settled_bits":len(stable),
        "isolated_edge_shape":edge_shape,
        "phase_bridge_diagnostic":phase,
    }


def read_iq(path, *, duration=2.5, rate=RAW_RATE, fs=FS, offset=CARRIER_SHIFT):
    raw=np.load(path,mmap_mode="r")
    if raw.dtype!=np.uint8 or raw.ndim!=1 or len(raw)%2 or rate%fs:
        raise ValueError("expected interleaved U8 I/Q; integer resampling ratio")
    n=min(len(raw)//2,int(duration*rate))
    iq=(raw[:2*n:2].astype(np.float32)-127.5) + \
       1j*(raw[1:2*n:2].astype(np.float32)-127.5)
    t=np.arange(n,dtype=np.float64)/rate
    iq *= np.exp(2j*np.pi*offset*t)
    return resample_poly(iq,1,rate//fs)


def analyze_archives(data_dir, assisted_report, names, *, iq_lowpass_hz=8000.):
    if not 100. < iq_lowpass_hz < FS/2:
        raise ValueError("IQ lowpass cutoff must be above 100 Hz and below Nyquist")
    filter_sos = butter(3, iq_lowpass_hz, fs=FS, output="sos")
    rows={item["capture"]:item for item in json.loads(assisted_report.read_text())}
    cases=[]
    for name in names:
        meta=rows[name]
        path=data_dir/(name+".npy")
        bits=payload(meta["nbytes"])
        received=read_iq(path)
        report=analyze_iq(received,bits,baud=meta["rate"],
                          first_center_s=meta["start_s"])
        cleaned = sosfiltfilt(filter_sos, received)
        filtered_report = analyze_iq(cleaned,bits,baud=meta["rate"],
                                     first_center_s=meta["start_s"])
        report["iq_lowpass_comparison"] = {
            "cutoff_hz":iq_lowpass_hz,
            "order":3,
            "complex_iq_filtered_before_discrimination":True,
            "uses_the_same_assisted_timing":True,
            "simple_discriminator_error_windows":filtered_report["simple_discriminator_error_windows"],
            "fsk_mark_separation_hz":filtered_report["fsk_mark_separation_hz"],
            "caution":"Filtering can improve decisions but also smears transient edges; phase-bridge table uses unfiltered I/Q."
        }
        saved=np.unpackbits(np.frombuffer(bytes.fromhex(meta["recovered_hex"]),
                                        dtype=np.uint8))[:len(bits)]
        with path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        report.update({
            "capture":name,
            "input_sha256":digest,
            "sample_rate_hz_input":RAW_RATE,
            "sample_rate_hz_processed":FS,
            "carrier_shift_hz_from_prior_assisted_decoder":CARRIER_SHIFT,
            "original_known_assisted_error_windows":error_windows(saved!=bits),
            "original_assisted_correlation":meta["correlation"],
            "known_assisted_clock_ppm_vs_nominal":
                round((meta["rate"]/meta["baud"]-1)*1e6,1),
            "rf_calibrated":False,
            "phase_reset_proven":False
        })
        cases.append(report)
    return {
        "project":"Flux","research_id":"FSK-02","offline":True,
        "radio_access":False,"transmitted":False,
        "data_dir":str(data_dir),"known_assisted_report":str(assisted_report),
        "limitations":[
            "All bit positions were determined by prior known-payload-assisted search.",
            "Simple discriminator is not an independently synchronized receiver.",
            "RF tuning/SDR filtering and timing uncertainty affect inferred deviation.",
            "Phase-bridge metrics cannot confirm hardware phase resets or continuity.",
            "No emissions, blind BER, hardware TX validation, or spectral-mask conclusion."
        ],"cases":cases
    }


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir",required=True,type=Path)
    ap.add_argument("--assisted-report",type=Path)
    ap.add_argument("--captures",default="apc76-baud1200-len128,"
                    "apc76-baud2400-len128,apc76-baud3600-len64,"
                    "apc76-baud4800-len64")
    ap.add_argument("--out",required=True,type=Path)
    ap.add_argument("--iq-lowpass-hz",type=float,default=8000.)
    args=ap.parse_args(argv)
    names=args.captures.split(",")
    if any(not n or "/" in n or "\\" in n or n.startswith(".") for n in names):
        ap.error("invalid capture names")
    try:
        report=analyze_archives(args.data_dir,
            args.assisted_report or args.data_dir/"decoded.json",names,
            iq_lowpass_hz=args.iq_lowpass_hz)
    except (OSError,KeyError,ValueError) as exc:
        ap.error(str(exc))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    for row in report["cases"]:
        orig=row["original_known_assisted_error_windows"]
        own=row["simple_discriminator_error_windows"]
        low=row["iq_lowpass_comparison"]["simple_discriminator_error_windows"]
        print(f"{row['capture']}: original={orig['all']} "
              f"(tail={orig['after_168']}), simple discriminator={own['all']} "
              f"(tail={own['after_168']}), filtered={low['all']} "
              f"(tail={low['after_168']}), FSK sep≈{row['fsk_mark_separation_hz']}Hz, "
              f"edge count={row['phase_bridge_diagnostic']['isolated_transition_count']}")
    print("Offline results:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
