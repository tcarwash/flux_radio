#!/usr/bin/env python3
"""Flux FSK-PDM: independently acquire the 4,800-bit/s native bitstream.

Uses ONLY the explicitly known 320-bit pilot, plus the first 16 PDM
symbols, for frequency/clock/level and eight-level mapping calibration.
The following 28 symbols (448 native chips) are fully held out.
It is not a blind receiver, a direct analog-8FSK demodulator, or an
emission-mask/complex-IQ fidelity measurement.

No serial-port, K6, transmitter, or active SDR access. Saved I/Q only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, resample_poly, sosfiltfilt

RAW_FS = 1_024_000
IQ_FS = 128_000
LO_SHIFT_HZ = 280_500
NATIVE_BAUD = 4800
SYMBOL_BAUD = 300
PILOT_BITS = 320
TRAIN_SYMBOLS = 16
CHIPS_PER_SYMBOL = 16
HOLDOUT_SYMBOLS = 28


def validate_manifest(manifest: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Check the physical payload and learned-label training contract."""
    if (int(manifest.get("native_baud", -1)) != NATIVE_BAUD or
        int(manifest.get("symbol_baud", -1)) != SYMBOL_BAUD or
        int(manifest.get("native_chips_per_symbol", -1)) != CHIPS_PER_SYMBOL):
        raise ValueError("unsupported FSK-PDM clocks")
    bits_s = manifest.get("payload_bits", "")
    chip_s = manifest.get("pdm_chip_bits", "")
    symbols = np.asarray(manifest.get("symbols", []))
    if (not isinstance(bits_s, str) or
        not isinstance(chip_s, str) or
        len(bits_s) != 1024 or len(chip_s) != 704 or
        set(bits_s) - set("01") or set(chip_s) - set("01") or
        not np.issubdtype(symbols.dtype, np.integer) or len(symbols) != 44 or
        np.any(symbols < 0) or np.any(symbols > 7)):
        raise ValueError("invalid PDM truth bits or symbols")
    if (bits_s[:256] != "01"*128 or
        bits_s[256:288] != "0"*32 or
        bits_s[288:320] != "1"*32 or
        bits_s[320:] != chip_s):
        raise ValueError("expected 320-bit calibration prefix differs")
    expected_wire = bytes.fromhex(manifest.get("payload_bytes_hex",""))
    wire_bits = "".join(f"{v:08b}" for v in expected_wire)
    if wire_bits != bits_s or hashlib.sha256(expected_wire).hexdigest() != manifest.get("payload_sha256_le_words"):
        raise ValueError("payload-wire checksum or bit order inconsistent")
    training = symbols[:TRAIN_SYMBOLS]
    if any(np.count_nonzero(training == k) < 1 for k in range(8)):
        raise ValueError("first 16 symbols must train all eight levels")
    return (np.fromiter((int(v) for v in bits_s),dtype=np.uint8),
            np.fromiter((int(v) for v in chip_s),dtype=np.uint8),
            symbols.astype(np.uint8))


def _rf_to_baseband(raw: np.ndarray, *, fs=RAW_FS,
                    first_s=.68, last_s=1.45) -> tuple[np.ndarray, float]:
    if (not isinstance(raw,np.ndarray) or raw.dtype!=np.uint8 or raw.ndim!=1 or
        len(raw) % 2 or fs != RAW_FS or len(raw)//2 < int(last_s*fs)):
        raise ValueError("expected full interleaved U8 SDR capture")
    a,b=int(first_s*fs),int(last_s*fs)
    pair=raw[2*a:2*b]
    iq=(pair[::2].astype(np.float32)-127.5)+1j*(pair[1::2].astype(np.float32)-127.5)
    time=np.arange(len(iq))/fs+a/fs
    iq *= np.exp(2j*np.pi*LO_SHIFT_HZ*time)
    return resample_poly(iq,1,8,window=("kaiser",10)),a/fs


def _locate_activity(iq: np.ndarray, *, fs=IQ_FS) -> tuple[int,int]:
    rms=uniform_filter1d(abs(iq),size=128)
    mask=rms>2.1
    edge=np.diff(np.pad(mask.astype(int),(1,1)))
    spans=[(int(a),int(b)) for a,b in zip(np.where(edge==1)[0],np.where(edge==-1)[0])
           if (b-a)>int(.14*fs)]
    if not spans:
        raise ValueError("no reliable bounded FSK burst")
    a,b=max(spans,key=lambda p:p[1]-p[0])
    if not .14<(b-a)/fs<.75:
        raise ValueError("detected burst duration outside known range")
    return a,b


def _training_correlation(observed: np.ndarray, labels: np.ndarray)->float:
    observed=observed.astype(float)
    x=observed-observed.mean()
    y=labels-labels.mean()
    den=float(np.linalg.norm(x)*np.linalg.norm(y))
    return float(x@y/den) if den>1e-8 else 0.


def _fit_native_timing(freq: np.ndarray, start_s: float,
                       burst_s: float, pilot: np.ndarray) -> dict:
    """Only 320 known pilot bits are used to fit bit0 time and bit clock."""
    labels=2*pilot.astype(float)-1.
    sample_ix=np.arange(len(freq),dtype=float)
    centers=np.arange(len(pilot),dtype=float)+.5
    best=(-float("inf"),None)
    for baud in (4780,4790,4795,4800,4805,4810,4820):
        spacing=IQ_FS/baud
        for preamble_delay in np.arange(.015,.045,.000025):
            initial=(burst_s+preamble_delay-start_s)*IQ_FS
            positions=initial+centers*spacing
            if positions[0]<0 or positions[-1]>=len(freq):
                continue
            values=np.interp(positions,sample_ix,freq)
            c=_training_correlation(values,labels)
            if c>best[0]:
                best=(c,(baud,burst_s+preamble_delay,values))
    c,match=best
    if match is None or c<.92:
        raise ValueError(f"known pilot not acquired (correlation={c:.3f})")
    baud,bit0_s,pilot_values=match
    # Long zero/one runs are only pilot bits: a physically measurable
    # mark/space calibration, independent of withheld 448 data chips.
    low=float(np.median(pilot_values[256:288]))
    high=float(np.median(pilot_values[288:320]))
    if high-low < 500:
        raise ValueError("FSK mark/space levels are not resolved")
    return {"pilot_correlation":c,"bit0_s":bit0_s,
            "native_baud_estimate":baud,"pilot_low_hz":low,
            "pilot_high_hz":high}


def decode(raw: np.ndarray, manifest: dict) -> dict:
    full_bits,chip_truth,labels=validate_manifest(manifest)
    y,start_s=_rf_to_baseband(raw)
    begin,end=_locate_activity(y)
    burst_s=start_s+begin/IQ_FS
    y=sosfiltfilt(butter(4,10000,fs=IQ_FS,output="sos"),y)
    freq=uniform_filter1d(
        np.angle(y[1:]*np.conj(y[:-1]))*IQ_FS/(2*np.pi),size=6)
    fit=_fit_native_timing(freq,start_s,burst_s,full_bits[:PILOT_BITS])
    samples=((fit["bit0_s"]-start_s)*IQ_FS +
             (np.arange(1024)+.5)*IQ_FS/fit["native_baud_estimate"])
    if samples.min()<0 or samples.max()>=len(freq):
        raise ValueError("captured IQ ends before last native chip")
    values=np.interp(samples,np.arange(len(freq)),freq)
    boundary=(fit["pilot_low_hz"]+fit["pilot_high_hz"])/2.
    bit_est=(values>boundary).astype(np.uint8)
    # Explicit PDM-symbol training is not used to fit timing, but it is
    # used to learn popcount-to-symbol mapping. Held-out labels only score.
    chips=bit_est[PILOT_BITS:].reshape(-1,CHIPS_PER_SYMBOL)
    ones=chips.sum(axis=1)
    learned={k:float(np.median(ones[:TRAIN_SYMBOLS][labels[:TRAIN_SYMBOLS]==k]))
             for k in range(8)}
    ordered=[learned[k] for k in range(8)]
    if any(b-a<=.5 for a,b in zip(ordered,ordered[1:])):
        raise ValueError("eight training symbol popcount levels not separated")
    decoded=np.argmin(np.abs(ones[:,None]-np.array(ordered)[None,:]),axis=1).astype(np.uint8)
    heldout_bits=bit_est[PILOT_BITS+TRAIN_SYMBOLS*CHIPS_PER_SYMBOL:]
    expected_bits=full_bits[PILOT_BITS+TRAIN_SYMBOLS*CHIPS_PER_SYMBOL:]
    if len(heldout_bits)!=448:
        raise AssertionError("heldout chip count must remain 448")
    heldout_syms=decoded[TRAIN_SYMBOLS:]
    return {
        "known_pilot_acquired":True,
        "training_only_acquisition":True,
        "pilot_bit_count":PILOT_BITS,
        "pilot_correlation":round(fit["pilot_correlation"],6),
        "rf_activity_start_s":round(burst_s,6),
        "first_fifo_bit_time_s":round(fit["bit0_s"],7),
        "first_pdm_chip_time_s":round(
            fit["bit0_s"]+PILOT_BITS/fit["native_baud_estimate"],7),
        "native_baud_estimate":fit["native_baud_estimate"],
        "calibration_plateau_low_hz_relative":round(fit["pilot_low_hz"],2),
        "calibration_plateau_high_hz_relative":round(fit["pilot_high_hz"],2),
        "calibration_half_separation_hz":round(
            (fit["pilot_high_hz"]-fit["pilot_low_hz"])/2,2),
        "native_training_bits":TRAIN_SYMBOLS*CHIPS_PER_SYMBOL,
        "native_training_bit_errors":int(np.count_nonzero(
            bit_est[PILOT_BITS:PILOT_BITS+TRAIN_SYMBOLS*CHIPS_PER_SYMBOL] !=
            full_bits[PILOT_BITS:PILOT_BITS+TRAIN_SYMBOLS*CHIPS_PER_SYMBOL])),
        "native_heldout_bits":int(len(heldout_bits)),
        "native_heldout_bit_errors":int(np.count_nonzero(heldout_bits!=expected_bits)),
        "symbol_training_symbols":TRAIN_SYMBOLS,
        "symbol_training_errors":int(np.count_nonzero(decoded[:TRAIN_SYMBOLS]!=labels[:TRAIN_SYMBOLS])),
        "symbol_heldout_symbols":int(len(heldout_syms)),
        "symbol_heldout_errors":int(np.count_nonzero(heldout_syms!=labels[TRAIN_SYMBOLS:])),
        "learned_training_chip_counts":[round(learned[k],2) for k in range(8)],
        "recovered_heldout_symbols":heldout_syms.tolist(),
        "heldout_symbol_error_indices":np.flatnonzero(heldout_syms!=labels[TRAIN_SYMBOLS:]).tolist(),
        "receiver_type":"native binary FSK demod + 16-chip popcount 8-level reconstruction",
        "not_direct_analog_8fsk_receiver":True,
        "no_heldout_truth_used_in_sync_or_training":True,
        "not_an_independent_ber_estimate":True,
        "does_not_prove_rf_phase_continuity":True,
        "notes":[
            "The same pilot and repeated known vector were used across these trials.",
            "A binary-chip decoder is a different receiver from an analog 8-FSK frequency discriminator.",
            "RF clock/level calibration came only from the known initial 320-bit pilot.",
            "Popcount-to-symbol mapping came only from the first 16 known 8-level symbols.",
            "No absolute transmitter frequency, power, adjacent emissions, or continuous RF phase is established."
        ]
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest",required=True,type=Path)
    p.add_argument("--iq",required=True,nargs="+",type=Path)
    p.add_argument("--out",required=True,type=Path)
    args=p.parse_args(argv)
    if args.out.exists():
        p.error("refusing to overwrite an existing report")
    try:
        manifest=json.loads(args.manifest.read_text())
        reports=[]
        for path in args.iq:
            raw=np.load(path,mmap_mode="r",allow_pickle=False)
            row=decode(raw,manifest)
            with path.open("rb") as handle:
                row["iq_npy_sha256"]=hashlib.file_digest(handle,"sha256").hexdigest()
            row["iq_path"]=str(path)
            reports.append(row)
        output={"project":"Flux", "study":"FSK-PDM native-chips-heldout",
                "offline_only":True,"rf_transmission_initiated":False,
                "manifest_path":str(args.manifest),
                "cases":reports}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(output,indent=2)+"\n")
    except (OSError,ValueError,KeyError,TypeError) as exc:
        p.error(str(exc))
    for row in reports:
        print(f"{Path(row['iq_path']).name}: native heldout "
              f"{row['native_heldout_bit_errors']}/{row['native_heldout_bits']} errors, "
              f"popcount 8-FSK heldout {row['symbol_heldout_errors']}/"
              f"{row['symbol_heldout_symbols']} errors, "
              f"pilot corr {row['pilot_correlation']:.4f}")
    print("Saved offline analysis:",args.out)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
