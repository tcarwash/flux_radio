#!/usr/bin/env python3
"""Build a fresh blinded listening pack for causal GP0 noise tracking.

Uses two Sotto speech excerpts not used by the previous GP0 blind packs. Audio
is simulated and encoded/decoded by the real Sotto v3.8 host tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import tempfile
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from k6_gp0_causal_denoise_sotto_v38 import causal_denoise
from k6_gp0_noise_level_tracker_study import causal_quantile_tracker_denoise
from k6_gp0_noise_sotto_v38_study import (
    FS, SAMPLE_ROOT, build_codec, encode_decode, read_flac, resample_poly,
)
from k6_gp0_nonstationary_noise_study import (
    MASK_FLOOR, NOISE_SCALE, PREAMBLE_MS, PREAMBLE_SEED_OFFSET,
    TARGET_SNR_DB, VPP, envelope,
)
from rfsim.k6_mic import OFF_PCM_RMS_FS, generate_gp0_noise, k6_gp0_pcm
from k6_sotto_fidelity_previews import normalize_for_audition

SOURCES = (
    ("female_libri198_14s", "female_libri198_14s__original16.flac"),
    ("male_libri3436_15s", "male_libri3436_15s__original16.flac"),
)
NOISE_SEED = 20261128
PROFILE = "step_down_6db"
TRACKS = (
    ("clean_codec", "clean"),
    ("noisy_codec", "noisy"),
    ("fixed_wiener_2p5_0p03", "fixed"),
    ("causal_tracker_q0p20_w0p75", "q20w75"),
    ("causal_tracker_q0p35_w0p75", "q35w75"),
    ("causal_tracker_q0p20_w0p90", "q20w90"),
)
LETTERS = tuple("ABCDEF")


def build_html(pack_id: str) -> str:
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Blind GP0 tracker response comparison</title>
<style>
:root {{ color-scheme: light dark; font: 16px/1.45 system-ui,sans-serif }}
body {{ max-width: 1050px; margin: 2rem auto; padding: 0 1rem 3rem }}
.intro,.task {{ border: 1px solid #8886; border-radius: 12px; padding: 1rem 1.2rem; margin: 1rem 0 }}
.intro {{ background: color-mix(in srgb,#3973a8 10%,transparent) }}
.hint {{ opacity: .8 }} table {{ border-collapse: collapse; width: 100%; min-width: 760px }}
th,td {{ border-bottom: 1px solid #8885; padding: .55rem .45rem; text-align: left }}
audio {{ width: 220px; max-width: 100% }} input {{ width: 3.8rem; padding: .35rem }}
.wrap {{ overflow-x: auto }} button {{ font: inherit; padding: .65rem 1rem; cursor: pointer }}
#save {{ position: sticky; bottom: 1rem }}
</style></head><body>
<h1>Blind GP0 tracker response comparison</h1>
<section class="intro"><p>Compare A–F <b>within each utterance</b> at a comfortable, steady volume. These are new speech excerpts processed through a simulated GP0 microphone-noise model and the Sotto v3.8 codec; they are not radio captures.</p>
<p class="hint">The modeled noise starts higher and falls by 6 dB halfway through each utterance. Rate each clip for intelligibility, consonant clarity, listening effort, naturalness, and artifacts. Labels are randomized separately for each excerpt. This page does not read the answer key and makes no network requests.</p></section>
<main id="tasks"></main><button id="save">Download scores</button> <span id="status" role="status"></span>
<script>
const tasks=[{{id:"utterance01",title:"Utterance 1"}},{{id:"utterance02",title:"Utterance 2"}}];
const labels=["A","B","C","D","E","F"];
const fields=[["intelligibility","Intelligibility rank (1 = best)","rank"],["consonants","Consonant clarity (1–5)","rating"],["effort","Listening effort (1 = easy)","effort"],["naturalness","Naturalness (1–5)","rating"],["artifacts","Low artifacts (1 = poor)","rating"]];
const root=document.getElementById("tasks");
for(const task of tasks){{
 const section=document.createElement("section");section.className="task";
 section.innerHTML=`<h2>${{task.title}}</h2><p class="hint">Score this utterance before moving to the next. Do not compare ranks across utterances.</p>`;
 const wrap=document.createElement("div");wrap.className="wrap";const table=document.createElement("table");
 table.innerHTML=`<thead><tr><th>Label</th><th>Listen</th>${{fields.map(x=>`<th>${{x[1]}}</th>`).join("")}}</tr></thead>`;
 const body=document.createElement("tbody");
 for(const label of labels){{
  const row=document.createElement("tr");row.innerHTML=`<th>${{label}}</th><td><audio controls preload="none" src="${{task.id}}_${{label}}.wav" aria-label="Play ${{task.title}} label ${{label}}"></audio></td>`;
  for(const [key,title,kind] of fields){{const cell=document.createElement("td"),input=document.createElement("input");input.type="number";input.min="1";input.max=kind==="rank"?"6":"5";input.step="1";input.dataset.task=task.id;input.dataset.label=label;input.dataset.field=key;input.setAttribute("aria-label",`${{task.title}} ${{label}} ${{title}}`);cell.append(input);row.append(cell)}}
  body.append(row)
 }}
 table.append(body);wrap.append(table);section.append(wrap);root.append(section)
}}
document.getElementById("save").addEventListener("click",()=>{{const scores={{}};for(const input of document.querySelectorAll("input[data-task]"))if(input.value)scores[`${{input.dataset.task}}_${{input.dataset.label}}_${{input.dataset.field}}`]=input.value;const data={{schema:1,created:new Date().toISOString(),scores}};const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{{type:"application/json"}}));a.download="gp0-tracker-listening-scores.json";a.click();URL.revokeObjectURL(a.href);document.getElementById("status").textContent="Scores downloaded."}});
</script></body></html>'''


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    root = args.out_dir
    if root.exists():
        ap.error("output directory already exists; choose a new directory")
    blind = root / "blind"
    blind.mkdir(parents=True)
    rng = secrets.SystemRandom()
    answer = {
        "schema": 1,
        "offline_only": True,
        "codec": "Sotto v3.8 host encoder/decoder",
        "source_model": "RFSim measured-response GP0 source plus statistical banded noise",
        "noise_profile": PROFILE,
        "noise_seed": NOISE_SEED,
        "source_level_vpp": VPP,
        "global_input_snr_db": TARGET_SNR_DB,
        "level_match": "RMS matched to that utterance's clean-codec anchor",
        "trackers": {
            "candidates": [
                {"spectral_quantile": 0.20, "previous_scale_weight": 0.75},
                {"spectral_quantile": 0.35, "previous_scale_weight": 0.75},
                {"spectral_quantile": 0.20, "previous_scale_weight": 0.90},
            ],
            "selected_from": "offline response sweep 2026-11-28; simulated step-down profile",
        },
        "treatments": {},
        "ui_storage_key": f"gp0-tracker-blind-{secrets.token_hex(12)}",
        "limitations": [
            "Noise is simulated and uses one controlled step-down profile; this is not a physical K6 recording.",
            "The two source excerpts provide useful listening feedback but are not a broad listener study.",
            "PESQ-NB and ESTOI are objective proxies; human ratings remain necessary.",
        ],
    }
    instructions = """# Blind GP0 tracker listening set

Compare A–F within each utterance only. Each utterance uses new speech source
material and its own randomized A–F mapping. The page does not load the answer
key. All outputs are 8kHz mono PCM and are RMS matched to that utterance's
clean-codec anchor. The noise is a modeled 6dB step-down, not a radio capture.

Rate intelligibility rank (1 best), consonant clarity, listening effort (1
easy), naturalness, and artifacts. Ties are useful. Save the downloaded score
JSON before opening the separate answer-key file.
"""
    with tempfile.TemporaryDirectory(prefix="gp0-tracker-listenpack-") as td:
        temp = Path(td)
        encoder, decoder = build_codec(temp)
        for index, (name, filename) in enumerate(SOURCES, 1):
            source_path = SAMPLE_ROOT / filename
            original16 = read_flac(source_path, 16000)
            reference = resample_poly(original16, 1, 2)
            normalized = reference / max(float(np.max(np.abs(reference))), 1e-15)
            clean = k6_gp0_pcm(normalized, input_vpp=VPP, noise_rms_fs=0,
                               response="measured", clip=True)
            clean_decoded = encode_decode(encoder, decoder, clean, temp, f"{name}-clean")
            clean_rms = float(np.std(clean))
            raw_noise = generate_gp0_noise(len(clean), seed=NOISE_SEED,
                                           model="banded", rms_fs=OFF_PCM_RMS_FS)
            env = envelope(PROFILE, len(clean))
            scale = clean_rms / (10.0 ** (TARGET_SNR_DB / 20.0)) / max(float(np.std(raw_noise * env)), 1e-15)
            noisy = clean + raw_noise * env * scale
            np.clip(noisy, -1.0, 1.0, out=noisy)
            initial_level = float(np.sqrt(np.mean(env[:FS * PREAMBLE_MS // 1000] ** 2)))
            preamble = generate_gp0_noise(
                FS * PREAMBLE_MS // 1000,
                seed=NOISE_SEED + PREAMBLE_SEED_OFFSET,
                model="banded", rms_fs=OFF_PCM_RMS_FS * initial_level * scale,
            )
            filtered = causal_denoise(noisy, preamble, "wiener",
                                      noise_scale=NOISE_SCALE, mask_floor=MASK_FLOOR)
            tracked = {
                (q, w): causal_quantile_tracker_denoise(
                    noisy, preamble, spectral_quantile=q, previous_weight=w)[0]
                for q, w in ((0.20, 0.75), (0.35, 0.75), (0.20, 0.90))
            }
            decoded = {
                "clean_codec": clean_decoded,
                "noisy_codec": encode_decode(encoder, decoder, noisy, temp, f"{name}-noisy"),
                "fixed_wiener_2p5_0p03": encode_decode(encoder, decoder, filtered, temp, f"{name}-fixed"),
                "causal_tracker_q0p20_w0p75": encode_decode(encoder, decoder, tracked[(0.20, 0.75)], temp, f"{name}-q20-w75"),
                "causal_tracker_q0p35_w0p75": encode_decode(encoder, decoder, tracked[(0.35, 0.75)], temp, f"{name}-q35-w75"),
                "causal_tracker_q0p20_w0p90": encode_decode(encoder, decoder, tracked[(0.20, 0.90)], temp, f"{name}-q20-w90"),
            }
            labels = list(LETTERS)
            rng.shuffle(labels)
            mapping = {}
            target_rms = float(np.std(clean_decoded))
            for (treatment, _), label in zip(TRACKS, labels):
                matched, gain = normalize_for_audition(decoded[treatment], target_rms)
                path = blind / f"utterance{index:02d}_{label}.wav"
                wavfile.write(path, FS, matched)
                mapping[label] = {
                    "treatment": treatment,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "samples": int(len(matched)),
                    "duration_s": round(len(matched) / FS, 3),
                    "rms_gain_db": round(20 * np.log10(max(gain, 1e-12)), 3),
                    "clipped_samples": int(np.count_nonzero(np.abs(matched.astype(np.int32)) >= 32767)),
                }
            answer["treatments"][f"utterance{index:02d}"] = {
                "source_file": filename,
                "samples": int(len(reference)),
                "duration_s": round(len(reference) / FS, 3),
                "actual_global_snr_db": round(20 * np.log10(
                    max(clean_rms, 1e-15) / max(float(np.std(noisy - clean)), 1e-15)), 4),
                "mapping": mapping,
            }
            print(f"utterance{index:02d}: wrote six blinded clips", flush=True)
    (root / "instructions.md").write_text(instructions)
    (root / "answer-key.json").write_text(json.dumps(answer, indent=2, sort_keys=True) + "\n")
    (blind / "index.html").write_text(build_html(answer["ui_storage_key"]))
    print(f"blind listening set: {blind}")


if __name__ == "__main__":
    main()
