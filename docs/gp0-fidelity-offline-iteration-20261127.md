# GP0 and Sotto readability — offline iteration, Nov 27 2026

**Goal:** improve physical K6 GP0 input fidelity and/or Sotto decoded voice readability without making a radio change. **No physical RF tests, no PTT, no firmware flashes, no library writes, no SDR driver access, and no bench reservations in this phase.** All original radio-generated IQ, 4096-byte count dumps and decoded speech WAVs remain untouched on the local workstation. No private recordings or keys were added to Git.

## Physical input evidence — genuine before-decimator popcounts

The trusted inputs are from the earlier attended `GP0 RAW AIR` tests, not the Sotto codec bitstream:

- Generator **500 Hz / 0.1Vpp**, fully CRC-verified **86/86** frames, **4,096** raw groups, 43.9ms timer; the working run is `gen-500-20261127-03`, after probes were reseated.
- Generator **1,000 Hz / 0.1Vpp**, 84/86 CRC frames. Sequence gaps 8 and 72 are never filled. Analysis uses ONLY the longest CRC-valid, **3,024 contiguous groups** (sequence 9..71).
- Generator **2,800 Hz / 0.1Vpp**, fully CRC-verified 86/86 frames, 4,096 raw groups, 43.9ms.
- Generator **OFF** control, 82/86 CRC frames. Analysis uses ONLY a **2,944 contiguous-group** run (sequence 24..85), never zeros or interpolation.

These are approximately 32–44ms bursts, not speech recordings. The source is a count of 64 one-bit PDM inputs, NOT the original individual PDM bitstream. Source hashes, checked group counts and remaining limitations are in [`docs/evidence/k6-gp0-fidelity-sweep-20261127.json`](evidence/k6-gp0-fidelity-sweep-20261127.json).

## Code and model

`tools/k6_gp0_fidelity_lab.py` extracts the currently pinned **Rust `Decimator` and `dec_push`** from `firmware/k6-aura/sotto-api30-gp0-capture.rs`. It compiles the existing original/AA3/FIR11 variants plus experimental symmetric integer FIR, lighter IIR and late-divide FIR variants as **temporary HOST Rust programs**. Nothing in production firmware is patched. The candidate code borrows the firmware's fractional-decimator and Q8 PCM math, uses input groups bounded to `[0,64]`, and models `group_weight=7500` with divisor 87260 to reflect the empirically measured ~8kHz output rate. The radio's physical firmware still has its original settings.

All variants are evaluated on **identical samples**. The same first 32 decoded PCM samples are omitted to reduce short-session filter-startup bias. Metric: fit the known nominal tone frequency within +/-3%, compute its PCM peak and residual RMS, then compare signal-to-residual ratio and tone attenuation relative to the existing boxcar. This is not a speech intelligibility score; long-duration codec and subjective listening remain unresolved. **No independent physical holdout set exists; do not overfit the filter to these specific captures.**

### New candidate that balances fidelity

A **7-point rectangular average with late division** (`fir7_box_late`) is the best preliminary worst-frequency trade-off in the tested candidates:

| Candidate | 500Hz SNR gain | 1kHz SNR gain | 2.8kHz SNR gain | 2.8kHz signal attenuation | Generator-OFF PCM RMS |
|---|---:|---:|---:|---:|---:|
| Original unfiltered | — | — | — | — | 0.04231 FS |
| AA3 | +2.60 dB | +1.86 dB | **−1.57 dB** | **−4.61 dB** | 0.03051 FS |
| FIR11_LATE | +1.33 dB | +1.23 dB | ~0.00 dB | −1.61 dB | 0.03451 FS |
| FIR5_BOX_LATE | +0.30 dB | +0.74 dB | +0.06 dB | −0.33 dB | 0.03782 FS |
| **FIR7_BOX_LATE** | **+0.58 dB** | **+0.92 dB** | **+0.17 dB** | **−0.61 dB** | **0.03635 FS** |

FIR7 late keeps seven previous bounded i8 values in an experimental state, updates a running i16 sum with one subtract and one add per high-rate group, then performs `floor(256 * |integrated_sum| / (7 * fs))` at *PCM* rate. The host compiler uses the same 87260 fast division as the real decimator. It closely agrees with the slower direct Q7 7-tap FIR. **This is not an MCU timing, stack, RAM0, ROM or live-ISR qualification.** The existing MCU leaves only 24 bytes of protected RAM0 headroom; until an isolated ARM linked/timing gate proves safety, do not enable FIR7 on the radio.

### Synthetic high-band robustness probes (not additional hardware measurements)

For a separate sensitivity check, add deterministic **3.2-count synthetic sine waves at 2kHz and 3.2kHz** into a *CRC-verified generator-OFF* group segment, quantize to `[0,64]`, and pass them through the same compiled Rust path. The FIR7 late probe improves SNR by **+1.15 dB at 2kHz** and **+0.33 dB at 3.2kHz**, with peak loss **−0.28 and −0.91dB** respectively. These simulated sources are NOT direct RFIC microphone response data, and their quantization/analog path is idealized. They reinforce—but cannot prove—the physical 7-tap result.

## Decoded-Sotto speech listening work (separate stage)

A raw 32–44ms GP0 dump cannot evaluate word readability. We therefore used **three previous 8kHz signed16 real Sotto decoded files**, durations **2.24s**, **4.64s**, and **17.16s**, to create private, level-matched playback-only versions with `tools/k6_sotto_fidelity_previews.py`: `original`, `gentle`, `presence`, `focused`, `strong`. This reuses the earlier receiver-side soft denoiser's idea but adds a **less restrictive 140–3650Hz band**, a time-frequency-smoothed spectral mask with a stronger 1.5–3.5kHz minimum-gain guard, and a modest +1.4dB consonant-presence variant. `focused` and `strong` are more aggressive controls. These versions are noncausal offline presentation experiments: they are *not* microphone ISR code, Sotto codec changes, or genuine source denoising.

On the 17.16s decoded clip, the **3–4kHz power fraction** is roughly **23.4%** original, **15.0%** gentle, **15.4%** presence, **7.1%** focused and **2.7%** strong. All five versions are RMS matched in this source, preserve duration and remain unclipped. That shows where audio energy was removed, **not how much noise was removed or whether a consonant became intelligible**. The 2.24s and 4.64s clips have similar energy trends. Derived-only manifest: [`docs/evidence/k6-sotto-fidelity-previews-20261127.json`](evidence/k6-sotto-fidelity-previews-20261127.json).

### Private blind A/B listening trial

All original and treated WAVs are available on the user-controlled workstation:

`/home/tyler/flux-bench/gp0-fidelity-lab-20261127/`

The longest 17.16s file has a private **eight-second, five-way blind** set:

`/home/tyler/flux-bench/gp0-fidelity-lab-20261127/blind-long-8s/{A,B,C,D,E}.wav`

There is also a short two-second version under `blind-short-2s`. `tools/k6_sotto_blind_review.py` creates these deterministically and refuses to overwrite a prior trial. Listen at the same system volume, rank A–E by **word clarity**, **consonant definition**, **hiss**, **naturalness**, and **warbling**. **Do not read the private `answer_key.json` until the ranking has been recorded.** The local answer key intentionally isn't checked into Git.

No ASR/Whisper, STOI or PESQ was installed in the lab Python environment; and no matched clean reference transcript or original undistorted voice clip is available. Installing those tools alone would not make the unpaired clips a reliable readability score. A blind human rating is the next evidence gate, not inferred perceptual gain.

## Reproduce the offline sweep

```sh
cd /storage5/dev/flux-gp0-popcount-air-20261127
VENV=/home/tyler/.local/share/flux-rf-venv/bin/python
$VENV tools/k6_gp0_fidelity_lab.py \
  --raw-root recovery/gp0_raw \
  --json-out recovery/gp0_raw/fidelity-another-run.json
$VENV -m pytest tests/test_k6_gp0_fidelity_lab.py \
  tests/test_k6_gp0_noise_filter_model.py \
  tests/test_k6_gp0_popcount_air.py tests/test_k6_gp0_popcount_air_rx.py -q
```

Receiver-audio listening previews can be reproduced to **new empty output directories** using `tools/k6_sotto_fidelity_previews.py` and `tools/k6_sotto_blind_review.py` with their explicit source/output flags. All scripts are offline and fail closed on missing/mixed/corrupt raw captures.

## Recommendation and next gates

1. **Keep original radio settings.** FIR7 late is promising, but the SNR changes are mostly under 1dB and the physical input is too short for word-quality conclusions. AA3 consistently sacrifices too much high-frequency tone power.
2. Perform **blind A–E listening** on the private 8-second Sotto clip. Prefer `gentle`/`presence` only if a listener actually understands more content without musical artifacts; the labels remain hidden until scoring.
3. If another physical radio session is authorized later, capture **multiple longer voice spans or a wider set of real high-rate input clips**, plus an independent reference audio file. That enables a true speech-quality, word-recognition or intelligibility comparison.
4. *Only after* offline preference and physical blind evidence, consider a bounded experimental FIR7 linked-ARM code-size/RAM0/ISR-cycle gate, **without** modifying the known-good firmware until a separate operator review.

**Checkpoint:** offline evidence is reproducible; no claim of demonstrated human readability improvement or hardware qualification has been made.
