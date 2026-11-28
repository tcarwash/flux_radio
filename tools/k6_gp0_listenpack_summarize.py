#!/usr/bin/env python3
"""Unblind and summarize an exported GP0 listening-pack score JSON."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--answer-key", type=Path, required=True)
    ap.add_argument("--scores", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        ap.error("refuse overwrite existing summary")
    key = json.loads(args.answer_key.read_text())
    source = json.loads(args.scores.read_text())
    scores = source.get("scores")
    if not isinstance(scores, dict):
        ap.error("score export must have an object named 'scores'")
    grouped: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    by_speaker: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    missing_labels = []
    label_treatments: dict[tuple[str, str], str] = {}
    rank_limits: dict[str, int] = {}
    for speaker, detail in key.get("treatments", {}).items():
        if isinstance(detail, dict) and isinstance(detail.get("mapping"), dict):
            for label, entry in detail["mapping"].items():
                if isinstance(entry, dict) and "treatment" in entry:
                    label_treatments[(speaker, label)] = entry["treatment"]
            rank_limits[speaker] = max(1, len(detail["mapping"]))
        elif isinstance(detail, dict):
            for filename, entry in detail.items():
                if isinstance(entry, dict) and "treatment" in entry:
                    stem = Path(filename).stem
                    speaker_name, _, label = stem.rpartition("_")
                    label_treatments[(speaker_name, label)] = entry["treatment"]
                    rank_limits[speaker_name] = max(rank_limits.get(speaker_name, 0), 5)
    for name, raw in scores.items():
        try:
            speaker, label, metric = name.rsplit("_", 2)
            value = int(raw)
        except (ValueError, AttributeError):
            ap.error(f"invalid score entry: {name!r}={raw!r}")
        max_value = rank_limits.get(speaker, 5) if metric == "intelligibility" else 5
        if metric not in {"intelligibility", "effort", "naturalness", "consonants", "artifacts"} or not 1 <= value <= max_value:
            ap.error(f"invalid metric or rating: {name!r}={raw!r}")
        filename = f"{speaker}_{label}.wav"
        treatment = label_treatments.get((speaker, label))
        if treatment is None:
            missing_labels.append(name)
            continue
        grouped[treatment][metric].append(value)
        by_speaker[speaker][treatment][metric] = value

    summary = {}
    for treatment, metrics in sorted(grouped.items()):
        summary[treatment] = {}
        for metric, values in sorted(metrics.items()):
            summary[treatment][metric] = {
                "n": len(values),
                "mean": round(sum(values) / len(values), 3),
                "scores": values,
            }
    report = {
        "schema": 1,
        "source_export_created": source.get("created"),
        "answer_key_parameter_sweep_pack": key.get("parameter_sweep_pack"),
        "answer_key_source_model": key.get("source_model"),
        "answer_key_noise_profile": key.get("noise_profile"),
        "answered_item_count": len(scores) - len(missing_labels),
        "unmapped_score_items": missing_labels,
        "treatment_summary": summary,
        "speaker_scores": {speaker: dict(treatments) for speaker, treatments in sorted(by_speaker.items())},
        "limitations": [
            "Each score entry is a single listener rating; do not treat repeated clips as independent listeners.",
            "Unanswered fields are omitted, so metric counts differ.",
            "Intelligibility values are ranks where 1 is best; other score directions follow the listening-page labels.",
            "The clean-codec anchor is intentionally noise-free and is not a radio recording.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as out:
        json.dump(report, out, indent=2, sort_keys=True)
        out.write("\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
