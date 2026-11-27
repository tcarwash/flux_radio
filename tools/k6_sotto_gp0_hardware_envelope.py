#!/usr/bin/env python3
"""Reproducible Sotto GP0 timing *sensitivity* envelope from physical GPF1/GPT1.

The 96-MHz Cortex-M0 compiled Sotto module, real pinned codebook and virtual
FSK/RX are exercised in the existing Unicorn harness. The new GP0 scheduler
injects a **constant** ISR cost at each nominal 87,260/8-Hz interrupt rather
than an invented smooth 8-kHz cost per PCM sample.

Only physical *peak* ISR ticks were measured. No mean cycles or preemption
distribution were captured. Multipliers below are explicitly hypothetical.
Do not claim a matching simulated failure proves the real failure mechanism
or use a pass here to authorize a hardware flash.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import tempfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from tools.k6_gp0_irq_timing import CPU_HZ, GROUPS_PER_IRQ, PDM_GROUPS_PER_SECOND, PCM_HZ, GP0InterruptBudget

ROOT = Path(__file__).resolve().parents[1]
AA3_RECORD = ROOT / "docs/evidence/sotto-gp0-aa3-first-physical-queue-overflow-20261126.json"
# Working 8-second voice telemetry: docs/sotto-gp0-fast4-micro-first-physical-20261126.md;
# full raw GPF1/GPT1 preserved privately, not committed to the repository.
KNOWN_GOOD = {"image_sha256":"c69a87428a4b72192f4a9ac22bd17c5fe40da159ebe818880a2a5b04ca7f03ec",
              "peak_isr_ticks":3147,"pcm_lost":False,"module_return":202,
              "gp0_irq_count":65535,"gp0_irq_counter_saturated":True}
MODULE_SHA256 = "7c91f220ad6d7577dfac86cd2eaf3d53a6a14b2ad671aa870596031025092121"
SPI_SETUP_US=6.19213
SPI_BYTE_US=.940394
FD_REGISTER_US=60.0


def read_aa3(path:Path=AA3_RECORD)->dict:
    rec=json.loads(path.read_text())
    f=rec["raw_readback"]["fault"]
    t=rec["raw_readback"]["timing"]
    if (rec["firmware_sha256"]!="cff8ee3308fd0cf65a07dac28513bd33f7c86556073a59b6ec2281568de83e56"
            or not f["valid"] or not t["valid"]
            or f["module_serial"] != t["module_serial"]
            or f["module_serial"] != rec["module_slot24_serial"]):
        raise ValueError("GPF1/GPT1 provenance mismatch")
    if f["module_serial"]!=rec["module_slot24_serial"]:
        raise ValueError("unexpected module serial")
    if (f["module_return"]!=-12 or not f["pcm_lost"] or f["gp0_dma_error"]!=3
            or t["gp0_code3_reason_id"]!=2 or f["resource_errors"] or f["fsk_errors"]):
        raise ValueError("physical AA3 record is not the observed PCM queue-full failure")
    return {"image_sha256":rec["firmware_sha256"],"peak_isr_ticks":f["gp0_peak_isr_ticks"],
            "gp0_irq_count":f["gp0_irq_count"],"pcm_lost":True,
            "module_return":-12,"measured_capture_ms":round(
                f["gp0_irq_count"] * GROUPS_PER_IRQ / PDM_GROUPS_PER_SECOND * 1000,2),
            "gp0_irq_counter_saturated":False,
            "resource_reads":t["resource_reads"],
            "resource_callback_ms":round(t["resource_total_tim17_ticks_modulo"]/96000,2),
            "fsk_preload_peak_ms":t["fsk_preload_peak_approx_ms"],
            "module_serial":f["module_serial"]}


def envelope(*,cpi=(2.5,3.0),peak_fractions=(0.75,1.0),execute=True,fixture_samples=3280,actual_group_rate_hz=PDM_GROUPS_PER_SECOND):
    aa3=read_aa3()
    if not 800<=fixture_samples<=24000:
        raise ValueError("fixture_samples must be 0.1–3 seconds at 8kHz")
    for axis in (cpi,peak_fractions):
        if not axis or any(not math.isfinite(v) or v<=0 for v in axis):
            raise ValueError("invalid scenario parameter")
    if any(v>1 for v in peak_fractions):
        raise ValueError("peak fractions cannot exceed hardware-measured peak")
    if len(cpi)*len(peak_fractions)>16:
        raise ValueError("maximum 16 timing combinations per image")
    period=GP0InterruptBudget(0,0,actual_group_rate_hz=actual_group_rate_hz).period_cycles
    profiles={"working_unfiltered":KNOWN_GOOD, "aa3_failure":aa3}
    record={"schema":1,"offline_only":True,
            "test_type":"constant-IRQ-cost sensitivity study, not cycle-accurate hardware replay",
            "module_sha256_expected":MODULE_SHA256,
            "hardware":profiles,
            "irq_rate_hz":actual_group_rate_hz/GROUPS_PER_IRQ,
            "gp0_actual_group_rate_hz":actual_group_rate_hz,
            "gp0_nominal_firmware_divisor":PDM_GROUPS_PER_SECOND,
            "gp0_effective_pcm_samples_per_wall_second":round(PCM_HZ*actual_group_rate_hz/PDM_GROUPS_PER_SECOND,5),
            "gp0_playback_pitch_factor":round(PDM_GROUPS_PER_SECOND/actual_group_rate_hz,7),
            "irq_period_us":period/CPU_HZ*1e6,
            "cpu_hz":CPU_HZ,
            "modeled_spi_setup_us":SPI_SETUP_US,"modeled_spi_per_byte_us":SPI_BYTE_US,
            "modeled_fd_reg_us":FD_REGISTER_US,
            "limitations":[
              "TIM17 peak is not average ISR cost, and the model never runs the actual K6 Rust IRQ handler",
              "the input is ideal 8kHz PCM; GP0 PDM, analog mic gain, AA3 noise attenuation and aliasing are separate unvalidated models",
              "model CPI is an assumption, not an on-chip profiler; physical ROM/flash wait states, nested ISR and bus contention are not cycle exact",
              "the known-good 65535 IRQ counter is saturated, not an exact count",
              "physical AA3 has one failed recording; mean ISR and codec time distribution are unmeasured",
            ],"scenarios":[]}
    if not execute:
        return record
    from sim.k6emu.rf.virtual_link import Channel
    from tools.k6_sotto_module import build
    from tools.k6_sotto_virtual_link import prepare,trial,accepted
    with tempfile.TemporaryDirectory(prefix="sotto-gp0-physical-envelope-") as tmp:
        i=np.arange(fixture_samples)
        pcm=(7000*np.sin(2*np.pi*1000*i/PCM_HZ)+1200*np.sin(2*np.pi*2000*i/PCM_HZ)).astype("<i2")
        artifact=prepare(ROOT,Path(tmp),pcm=pcm,gp0=True)
        code,entry=build(host_resources=True,gp0_irq_tx=True,ram_micro=True,
                         fast_div=True,fast_history_copy=True,fast_cb_pair=True,fast_lag_pair=True)
        assert hashlib.sha256(code).hexdigest()==MODULE_SHA256, "not the deployed GP0 module"
        artifact.update(code=code,entry=entry)
        record["module_sha256_measured"]=hashlib.sha256(code).hexdigest()
        record["pcm_fixture_samples"]=fixture_samples
        for name,profile in profiles.items():
            for clock_cpi in cpi:
                for ratio in peak_fractions:
                    irq_cost=profile["peak_isr_ticks"]*ratio
                    r=trial(artifact,Channel(),cpi=clock_cpi,gp0_irq_cycles=irq_cost,
                            spi_us=SPI_SETUP_US,spi_byte_us=SPI_BYTE_US,fd_us=FD_REGISTER_US,
                            gp0_actual_group_rate_hz=actual_group_rate_hz,
                            fsk_cooperative_model=True)
                    record["scenarios"].append({
                        "profile":name,"cpi":clock_cpi,
                        "irq_cycles":irq_cost,"fraction_of_measured_peak":ratio,
                        "hypothetical_irq_cpu_duty":round(irq_cost/period,4),
                        "accepted":accepted(r),"module_return":r["module_return"],
                        "pcm_lost":r["pcm_lost"],"pcm_highwater_blocks":r["pcm_highwater"],
                        "pcm_overflow_ms":r["pcm_overflow_ms"],
                        "virtual_irq_count":r["gp0_virtual_irq_count"],
                        "virtual_irq_cpu_cycles":r["gp0_virtual_irq_charged_cycles"],
                        "rx_valid_packets":r["received"] if r["session"]["complete"] else 0,
                        "sim_runtime_seconds":r["wall_seconds"]})
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json",type=Path,help="write report to a NEW path")
    p.add_argument("--cpi",type=float,nargs="+",default=[2.5,3.0])
    p.add_argument("--peak-fractions",type=float,nargs="+",default=[.75,1.0])
    p.add_argument("--metadata-only",action="store_true")
    p.add_argument("--fixture-samples",type=int,default=3280)
    p.add_argument("--actual-group-rate",type=float,default=PDM_GROUPS_PER_SECOND,
                   help="physical GP0 groups per second; distinct from fixed firmware divisor")
    a=p.parse_args()
    report=envelope(cpi=a.cpi,peak_fractions=a.peak_fractions,
                    execute=not a.metadata_only,fixture_samples=a.fixture_samples,
                    actual_group_rate_hz=a.actual_group_rate)
    print(json.dumps(report,indent=2,sort_keys=True))
    if a.json:
        with a.json.open("x") as f: f.write(json.dumps(report,indent=2,sort_keys=True)+"\n")
if __name__=="__main__":
    main()
