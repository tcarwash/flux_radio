#!/usr/bin/env python3
"""Paired, voice-clustered uncertainty analysis of tracker response sweep."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


INPUT = Path("recovery/gp0_raw/gp0-tracker-response-sweep-20261128.json")
OUTPUT = Path("recovery/gp0_raw/gp0-tracker-response-analysis-20261128.json")
STATIC = "static_preamble_wiener/scale=2.5/floor=0.03"
METHODS = (
    "causal_quantile_tracker/q=0.2/previous_weight=0.5",
    "causal_quantile_tracker/q=0.2/previous_weight=0.75",
    "causal_quantile_tracker/q=0.2/previous_weight=0.9",
    "causal_quantile_tracker/q=0.35/previous_weight=0.75",
)
PROFILES = ("stationary", "step_down_6db")
BOOTSTRAPS = 50000
SEED = 20261128


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit(f"refusing to overwrite {OUTPUT}")
    report = json.loads(INPUT.read_text())
    voices = sorted(report["conditions"])
    rng = np.random.default_rng(SEED)
    result = {
        "schema": 1,
        "offline_only": True,
        "source_report": str(INPUT),
        "unit_of_resampling": "speech voice; all noise seeds for a sampled voice travel together",
        "bootstrap_replicates": BOOTSTRAPS,
        "bootstrap_seed": SEED,
        "limitations": [
            "Only eight speech vectors are available, so the voice-cluster bootstrap interval is approximate.",
            "The analysis inherits the modeled banded noise, one SNR, and fixed Sotto codec conditions.",
            "Metric uncertainty does not establish human preference or physical-radio performance.",
        ],
        "comparisons": {},
    }
    for profile in PROFILES:
        result["comparisons"][profile] = {}
        for method in METHODS:
            by_voice: dict[str, list[float]] = {}
            pesq_by_voice: dict[str, list[float]] = {}
            wins = 0
            cases = 0
            for voice in voices:
                estoi_diffs, pesq_diffs = [], []
                for row in report["conditions"][voice]["profiles"][profile]:
                    treatment = row["treatments"][method]["post_codec"]
                    baseline = row["treatments"][STATIC]["post_codec"]
                    estoi_diffs.append(float(treatment["estoi"] - baseline["estoi"]))
                    pesq_diffs.append(float(treatment["pesq_nb"] - baseline["pesq_nb"]))
                    wins += treatment["estoi"] > baseline["estoi"]
                    cases += 1
                by_voice[voice] = estoi_diffs
                pesq_by_voice[voice] = pesq_diffs
            voice_means = np.array([np.mean(by_voice[v]) for v in voices])
            pesq_voice_means = np.array([np.mean(pesq_by_voice[v]) for v in voices])
            picked = rng.integers(0, len(voices), size=(BOOTSTRAPS, len(voices)))
            boot_estoi = voice_means[picked].mean(axis=1)
            boot_pesq = pesq_voice_means[picked].mean(axis=1)
            result["comparisons"][profile][method] = {
                "case_count": cases,
                "mean_paired_estoi_delta": round(float(voice_means.mean()), 7),
                "voice_cluster_bootstrap_95pct_ci_estoi": [
                    round(float(v), 7) for v in np.quantile(boot_estoi, [0.025, 0.975])
                ],
                "voices_with_positive_mean_estoi_delta": int(np.count_nonzero(voice_means > 0)),
                "cases_better_than_static": wins,
                "mean_paired_pesq_nb_delta": round(float(pesq_voice_means.mean()), 6),
                "voice_cluster_bootstrap_95pct_ci_pesq_nb": [
                    round(float(v), 6) for v in np.quantile(boot_pesq, [0.025, 0.975])
                ],
                "per_voice_mean_estoi_delta": {
                    voice: round(float(voice_means[i]), 7)
                    for i, voice in enumerate(voices)
                },
            }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUTPUT}")
    for profile, comparisons in result["comparisons"].items():
        print(profile)
        for method, stats in comparisons.items():
            lo, hi = stats["voice_cluster_bootstrap_95pct_ci_estoi"]
            print(f"  {method}: delta={stats['mean_paired_estoi_delta']:+.6f} "
                  f"cluster95=[{lo:+.6f},{hi:+.6f}] "
                  f"positive_voices={stats['voices_with_positive_mean_estoi_delta']}/8 "
                  f"case_wins={stats['cases_better_than_static']}/24")


if __name__ == "__main__":
    main()
