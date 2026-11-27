#!/usr/bin/env python3
"""No-hardware K6 GP0 rate sensitivity: raw group clock vs fixed PCM divisor.

Group-rate evidence came from *different* attended hardware measurements.
Inferred clock from decoded WAV is a conditional inverse calculation, NOT a
current measured clock. Sotto pitch quantization can invalidate that inference.
"""
from __future__ import annotations
import argparse
import json
from math import isfinite
from pathlib import Path

NOMINAL_GROUPS_PER_S=87260.0
LABELED_PCM_HZ=8000.0
PREVIOUS_MEASUREMENTS={
  "earlier_1k_diagnostic":84960.0,
  "earlier_generator_off_or_2k_diagnostic":87770.0,
}


def profile(actual_groups_per_s,*,nominal_tone_hz=500., label="scenario"):
    if not isfinite(actual_groups_per_s) or not 70_000<=actual_groups_per_s<=100_000:
        raise ValueError("invalid physical GP0 group rate")
    if not isfinite(nominal_tone_hz) or nominal_tone_hz<=0:
        raise ValueError("invalid tone frequency")
    pcm_cadence=LABELED_PCM_HZ*actual_groups_per_s/NOMINAL_GROUPS_PER_S
    playback_factor=LABELED_PCM_HZ/pcm_cadence
    return dict(label=label,actual_group_rate_hz=round(actual_groups_per_s,5),
                firmware_group_divisor_hz=NOMINAL_GROUPS_PER_S,
                nominal_pcm_header_hz=LABELED_PCM_HZ,
                output_pcm_samples_per_wall_second=round(pcm_cadence,4),
                input_sine_hz=nominal_tone_hz,
                expected_pcm_playback_peak_hz=round(nominal_tone_hz*playback_factor,4),
                expected_output_over_input_factor=round(playback_factor,7))


def report():
    scenarios=[profile(v,label=k) for k,v in PREVIOUS_MEASUREMENTS.items()]
    scenarios.append(profile(NOMINAL_GROUPS_PER_S,label="firmware_nominal"))
    nominal=500.0
    observed=482.18
    inferred=NOMINAL_GROUPS_PER_S*nominal/observed
    scenarios.append(profile(inferred,nominal_tone_hz=nominal,
                             label="inferred_if_500_to_482_is_entirely_clock_error_NOT_MEASURED"))
    return dict(schema=1,offline_only=True,
                note="The last clock is NOT measured: assumes a pure input sine and transparent codec, which Sotto is not.",
                physical_measurements_age="from distinct Nov 25 attended GP0 diagnostics, not contemporaneous with Nov 26 tone WAVs",
                pitch_formula="played_hz = physical_input_hz * 87260 / actual_GP0_groups_per_sec",
                elapsed_pcm_formula="actual_PCM_samples_per_wall_second = 8000 * actual_GP0_groups_per_sec / 87260",
                warning="A slower physical ADC/group clock makes decoded playback pitch HIGHER with a fixed 8000Hz header; a faster clock makes it LOWER. No real PDM waveform is executed by the virtual-link simulator.",
                scenarios=scenarios)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json",type=Path,required=True,help="new evidence file only")
    a=p.parse_args()
    r=report()
    with a.json.open("x") as f:f.write(json.dumps(r,indent=2,sort_keys=True)+"\n")
    for s in r["scenarios"]:
        print(s["label"],s["actual_group_rate_hz"],s["expected_pcm_playback_peak_hz"])


if __name__=="__main__":main()
