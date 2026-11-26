#!/usr/bin/env python3
"""Offline RFSim fading/noise sweeps for hypothetical REG51 polar DQPSK.

Only a simulated signed-FM register intention is modeled; no actual RFIC
multilevel signed FM, firmware mode, serial command or radio transmission.
The receiver knows frame start and baud and learns phase centroids from a
public pilot (not the held-out payload). This is not a deployable decoder.
"""
from __future__ import annotations
import argparse
from itertools import product
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from sim.modulation.dqpsk_polar import Parameters, RATE, synthesize
from tools.k6_dqpsk_polar_lab import decode_known_frame
from tools.k6_frequency_modem_lab import rx_soapy
from tools.k6_rfsim_onair_sweep import PROFILES, csv_values, CENTER_HZ


class PilotLockError(RuntimeError):
    """The simulated receiver failed to acquire the public pilot."""


def run_case(*, baud=100, bandwidth_hz=135., dac_bits=7,
             spi_write_us=117., profile="static", snr_db=30.,
             seed=0, cfo_hz=0., reverse_sign=False,
             channel_class=None, receiver=rx_soapy, decoder=decode_known_frame):
    if profile not in PROFILES:
        raise ValueError("unknown RFSim profile")
    if not math.isfinite(snr_db) or not -10<=snr_db<=120:
        raise ValueError("invalid SNR")
    if not math.isfinite(cfo_hz) or abs(cfo_hz)>500:
        raise ValueError("invalid CFO")
    if not isinstance(seed,int) or not 0<=seed<2**32:
        raise ValueError("invalid seed")
    params=Parameters(baud=baud,spi_write_us=spi_write_us,
                      analog_bandwidth_hz=bandwidth_hz,
                      dac_bits=dac_bits,seed=seed)
    params.validate()
    if channel_class is None:
        try:
            from rfsim.session import ChannelSession
        except ImportError as exc:
            raise RuntimeError("RFSim must be importable from PYTHONPATH") from exc
        channel_class=ChannelSession
    source=synthesize(params,reverse_sign=reverse_sign)
    transmitted=source["iq"]
    session=channel_class(RATE,profile,seed=seed,carrier_hz=CENTER_HZ,
                          duration_s=len(transmitted)/RATE+.1)
    iq=session.apply(transmitted,snr_db=snr_db,cfo_hz=cfo_hz,
                     noise_seed=(seed+1)&0xffffffff,
                     signal_power=float(np.mean(np.abs(transmitted)**2)))
    if np.shape(iq)!=np.shape(transmitted) or not np.all(np.isfinite(iq)):
        raise ValueError("RFSim returned invalid IQ")
    filtered=receiver(np.asarray(iq,dtype=np.complex64),snr_db=120.)
    try:
        result=decoder(source,filtered)
    except RuntimeError as exc:
        raise PilotLockError(str(exc)) from exc
    good=(result["payload_ber"]==0 and result["sync_errors"]==0
          and result["signed_frequency_mapping_valid"]
          and result["phase_level_span_degrees"]>20
          and source["dropped_writes"]==0)
    return {
        "baud":baud,"bandwidth_hz":bandwidth_hz,"dac_bits":dac_bits,
        "spi_write_us":spi_write_us,"profile":profile,"snr_db":snr_db,
        "seed":seed,"cfo_hz":cfo_hz,"reverse_sign":reverse_sign,
        "mode":"hypothetical_signed_FM_polar_DQPSK",
        "modeled_register_writes":len(source["writes"]),
        "dropped_register_writes":source["dropped_writes"],
        "receiver":result,"simulation_pass":bool(good),
        "physical_tx":False,"hardware_qualified":False,
        "decoded_in_K6_firmware":False,
        "receiver_assistance":"Known frame start and baud; public-pilot phase centroids",
        "transmit_evidence":"Unverified B-chip REG51 multilevel signed-FM transfer",
    }


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bauds",default="50,100,150,200")
    p.add_argument("--bandwidth-hz",default="135")
    p.add_argument("--dac-bits",default="7")
    p.add_argument("--spi-write-us",default="117")
    p.add_argument("--profiles",default="static,mobile-rural")
    p.add_argument("--snr-db",default="30,15")
    p.add_argument("--seeds",default="0")
    p.add_argument("--cfo-hz",default="0")
    p.add_argument("--reverse-sign",action="store_true")
    p.add_argument("--json",type=Path,required=True)
    a=p.parse_args(argv)
    try:
        rates=csv_values(a.bauds,int)
        bandwidths=csv_values(a.bandwidth_hz,float)
        bits=csv_values(a.dac_bits,int)
        writes=csv_values(a.spi_write_us,float)
        profiles=csv_values(a.profiles)
        snrs=csv_values(a.snr_db,float)
        seeds=csv_values(a.seeds,int)
        offsets=csv_values(a.cfo_hz,float)
        cases=list(product(rates,bandwidths,bits,writes,profiles,snrs,seeds,offsets))
        if not 0<len(cases)<=500: raise ValueError("case count must be 1..500")
        if any(x not in PROFILES for x in profiles):
            raise ValueError("unsupported RFSim profile")
        for rate,bw,b,write,profile,snr,seed,cfo in cases:
            Parameters(baud=rate,analog_bandwidth_hz=bw,dac_bits=b,
                       spi_write_us=write,seed=seed).validate()
            if not math.isfinite(snr) or not -10<=snr<=120:
                raise ValueError("invalid SNR")
            if not math.isfinite(cfo) or abs(cfo)>500:
                raise ValueError("invalid CFO")
    except ValueError as exc: p.error(str(exc))
    rows=[]
    for rate,bw,b,write,profile,snr,seed,cfo in cases:
        args=dict(baud=rate,bandwidth_hz=bw,dac_bits=b,spi_write_us=write,
                  profile=profile,snr_db=snr,seed=seed,cfo_hz=cfo,
                  reverse_sign=a.reverse_sign)
        try:
            row=run_case(**args)
            row["status"]="decoded" if row["simulation_pass"] else "decode_failed"
        except PilotLockError as exc:
            # Loss of public-pilot lock is a modeled PHY failure, not a
            # harness bug; preserve the exact loss reason.
            row=dict(**args,status="decode_failed",reason=str(exc),
                     simulation_pass=False,physical_tx=False,hardware_qualified=False)
        except (ValueError,ArithmeticError,RuntimeError) as exc:
            row=dict(**args,status="harness_error",reason=f"{type(exc).__name__}: {exc}",
                     simulation_pass=False,physical_tx=False,hardware_qualified=False)
        print(f'{rate:4} baud {bw:5g} Hz {profile:16} SNR={snr:5g} '
              f'CFO={cfo:5g} {row["status"]}',flush=True)
        rows.append(row)
    report={
        "kind":"unqualified_K6_REG51_polar_DQPSK_RFSim_SoapyFang",
        "physical_tx":False,"hardware_qualified":False,
        "receiver":"known_frame_start_public_pilot_only",
        "cases":rows,"decoded_count":sum(r["status"]=="decoded" for r in rows),
        "decode_failed_count":sum(r["status"]=="decode_failed" for r in rows),
        "harness_error_count":sum(r["status"]=="harness_error" for r in rows),
    }
    a.json.parent.mkdir(parents=True,exist_ok=True)
    a.json.write_text(json.dumps(report,indent=2)+"\n")
    return 2 if report["harness_error_count"] else 0


if __name__=="__main__":
    raise SystemExit(main())
