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

### Best in the original compact-filter sweep (historical)

A **7-point rectangular average with late division** (`fir7_box_late`) was the best preliminary worst-frequency trade-off among the original candidates up to 11 taps. A later long-FIR study found better simulated results; see the follow-up section below.

| Candidate | 500Hz SNR gain | 1kHz SNR gain | 2.8kHz SNR gain | 2.8kHz signal attenuation | Generator-OFF PCM RMS |
|---|---:|---:|---:|---:|---:|
| Original unfiltered | — | — | — | — | 0.04231 FS |
| AA3 | +2.60 dB | +1.86 dB | **−1.57 dB** | **−4.61 dB** | 0.03051 FS |
| FIR11_LATE | +1.33 dB | +1.23 dB | ~0.00 dB | −1.61 dB | 0.03451 FS |
| FIR5_BOX_LATE | +0.30 dB | +0.74 dB | +0.06 dB | −0.33 dB | 0.03782 FS |
| **FIR7_BOX_LATE** | **+0.58 dB** | **+0.92 dB** | **+0.17 dB** | **−0.61 dB** | **0.03635 FS** |

FIR7 late keeps seven previous bounded i8 values in an experimental state, updates a running i16 sum with one subtract and one add per high-rate group, then performs `floor(256 * |integrated_sum| / (7 * fs))` at *PCM* rate. The host compiler uses the same 87260 fast division as the real decimator. It closely agrees with the slower direct Q7 7-tap FIR. **This is not an MCU timing, stack, RAM0, ROM or live-ISR qualification.** The existing MCU leaves only 24 bytes of protected RAM0 headroom; until an isolated ARM linked/timing gate proves safety, do not enable FIR7 on the radio.

### Synthetic high-band robustness probes (not additional hardware measurements)

For a separate sensitivity check, add deterministic **3.2-count synthetic sine waves at 2kHz and 3.2kHz** into a *CRC-verified generator-OFF* group segment, quantize to `[0,64]`, and pass them through the same compiled Rust path. The FIR7 late probe improves SNR by **+1.15 dB at 2kHz** and **+0.33 dB at 3.2kHz**, with peak loss **−0.28 and −0.91dB** respectively. These simulated sources are NOT direct RFIC microphone response data, and their quantization/analog path is idealized. They test passband response only; they do not measure alias-band noise rejection or demonstrate a physical GP0 noise improvement.

## Decoded-Sotto speech listening work (separate stage)

A raw 32–44ms GP0 dump cannot evaluate word readability. We therefore used **three previous 8kHz signed16 real Sotto decoded files**, durations **2.24s**, **4.64s**, and **17.16s**, to create private, level-matched playback-only versions with `tools/k6_sotto_fidelity_previews.py`: `original`, `gentle`, `presence`, `focused`, `strong`. This reuses the earlier receiver-side soft denoiser's idea but adds a **less restrictive 140–3650Hz band**, a time-frequency-smoothed spectral mask with a stronger 1.5–3.5kHz minimum-gain guard, and a modest +1.4dB consonant-presence variant. `focused` and `strong` are more aggressive controls. These versions are noncausal offline presentation experiments: they are *not* microphone ISR code, Sotto codec changes, or genuine source denoising.

On the 17.16s decoded clip, the **3–4kHz power fraction** is roughly **23.4%** original, **15.0%** gentle, **15.4%** presence, **7.1%** focused and **2.7%** strong. All five versions are RMS matched in this source, preserve duration and remain unclipped. That shows where audio energy was removed, **not how much noise was removed or whether a consonant became intelligible**. The 2.24s and 4.64s clips have similar energy trends. Derived-only manifest: [`docs/evidence/k6-sotto-fidelity-previews-20261127.json`](evidence/k6-sotto-fidelity-previews-20261127.json).

### Private blind A/B listening trial

All original and treated WAVs are available on the user-controlled workstation:

`/home/tyler/flux-bench/gp0-fidelity-lab-20261127/`

The longest 17.16s file has a private **eight-second, five-way blind** set:

`/home/tyler/flux-bench/gp0-fidelity-lab-20261127/blind-long-8s/{A,B,C,D,E}.wav`

There is also a short two-second version under `blind-short-2s`. `tools/k6_sotto_blind_review.py` creates these deterministically and refuses to overwrite a prior trial. Listen at the same system volume, rank A–E by **word clarity**, **consonant definition**, **hiss**, **naturalness**, and **warbling**. **Do not read the private `answer_key.json` until the ranking has been recorded.** The local answer key intentionally isn't checked into Git.

At this initial playback-preview stage, no ASR/Whisper, STOI or PESQ was
available in the lab environment, and these three excerpts had no matched
clean reference. Later offline work found cached PESQ/ESTOI and Whisper
dependencies and used public Sotto codec pairs for proxy studies; that does
not repair the unpaired nature of these previews. A blind human rating is
still required before claiming readability improvement.

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

1. **Keep original radio settings.** The follow-up 127/255-tap candidates improve simulated tone SNR by roughly 1.3–1.5dB on the short captures, but their state and multiply-accumulate cost is far outside current MCU qualification. These remain offline leads only. AA3 still sacrifices too much 2.8kHz signal.
2. Perform the **expanded three-excerpt blind A–E listening trial** under `/home/tyler/flux-bench/gp0-fidelity-lab-20261127/blind-multiclip-v1/`. The excerpts were level-matched separately; score each before opening its private key. Prefer any treatment only if a listener understands more content without artifacts across more than one excerpt.
3. If another physical radio session is authorized later, capture **multiple longer voice spans or a wider set of real high-rate input clips**, plus an independent reference audio file. That enables a true speech-quality, word-recognition or intelligibility comparison.
4. The corrected multistage study shows modest offline SNR gains, while long FIRs show larger gains on short captured tone/noise spans. None is an embedded recommendation: every candidate needs a separate linked-ARM RAM0/ISR gate, and the current measurements do not establish alias rejection on speech or human readability. Do not place host-only 127/255-tap studies on the radio.

**Checkpoint:** offline evidence is reproducible; no claim of demonstrated human readability improvement or hardware qualification has been made.

## Follow-up: count-domain PSD, holdout and longer FIRs

The new CRC-gated spectrum tool, `tools/k6_gp0_raw_spectrum.py`, measures the
raw count PSD through 46.65kHz and removes the fitted tone fundamental from
tone runs. Confirmed generator-off and tone-removed residuals have average
per-Hz density above 4kHz about 1.3–2.3dB above below 4kHz. Integrated power
above 4kHz is 11.5–12.6dB higher mainly because the upper region is much
wider. The density is broadly flat; this supports possible fold-down of
wideband residual, but does not identify its cause or prove that the firmware
decimator aliases it into voice band.

The new `tools/k6_gp0_long_fir_study.py` applies quantized Q15 Kaiser filters
to physical group counts, then uses temporary host runners around the
unchanged Rust fractional decimator. A 255-tap 3.8kHz design gave +1.39dB mean
and +1.17dB worst-tone SNR gain, with +1.53dB at 2.8kHz; the 127-tap variant
gave +1.32dB mean and +1.43dB at 2.8kHz. The 255-tap analytical response is
near-flat to 3.2kHz and −12.9dB at 4kHz. This is much stronger than the
compact FIR7 study but still a modest, short-window simulation; it is not an
MCU candidate. Metrics-only outputs are in
`recovery/gp0_raw/raw-spectrum-gp0-offline-v2-20261127.json` and
`recovery/gp0_raw/long-fir-study-20261127.json`.

An independent 0dB SDR air capture's longest CRC-contiguous GP0 500Hz run
(1,632 groups) reproduces 3.327 peak counts and 1.293 residual RMS counts.
Host Rust replay on this ~17.5ms holdout produced +0.32dB for FIR7 late at
500Hz; the narrow tone and run are too short to validate broadband or voice
quality. See `recovery/gp0_raw/fidelity-g0-air-holdout.json`.

An additional three-excerpt blind listening set has been prepared at
`/home/tyler/flux-bench/gp0-fidelity-lab-20261127/blind-multiclip-v1/`.
Each 2s/4s/8s excerpt was independently RMS matched across five conditions,
with <=0.00003dB label spread and no clipped samples. The randomized answer
keys remain private until the listener records scores in `scoring-sheet.md`.

## Follow-up: corrected two-stage factor-two decimation study

The first run tested a 31-tap Q15 Kaiser low-pass at 16kHz, followed by a 31-
or 63-tap 3.8kHz low-pass after factor-two decimation. Its negative result is
**superseded and invalid**: the zero-state filters operated on uncentered
0..64 counts, creating an artificial startup step that persisted through the
decimator's slow DC blocker; comparisons also did not align the FIR group
delay. The first output is retained for provenance at
`recovery/gp0_raw/multistage-filter-study-20261127.json`.

The corrected rerun centers counts before filtering, restores the count-domain
mean after both stages, designs each filter at its capture's measured rate,
and compares matching source-time windows. The 31-tap second stage averaged
**+0.35dB** tone SNR change (worst −1.11dB at 2.8kHz); the 63-tap stage
averaged **+0.79dB** (worst +0.69dB at 500Hz). Generator-off output RMS fell
−0.23dB and −0.32dB, respectively. Tone level changes for the 63-tap stage
were −0.12dB at 500Hz, −0.06dB at 1kHz, and −0.25dB at 2.8kHz. This is a
modest offline improvement, not a firmware recommendation. It costs about
4.34M or 5.83M dense multiply-accumulates per second before optimization and
still lacks ARM timing evidence. Corrected metrics are in
`recovery/gp0_raw/multistage-filter-study-centered-aligned-20261127.json`.

## Follow-up: tones injected into measured generator-off count noise

To compare candidates under a physically measured noise realization, a new
offline experiment takes the longest CRC-contiguous generator-off GP0 count
sequence, removes its mean, adds known 500–3200Hz tones at 0.8, 1.6 or 3.2
peak counts and three phase offsets, requantizes to the 0..64 count alphabet,
then replays each sequence through the actual Rust decimator. This differs
from the generic white/high-pass/in-band synthetic noise model: the background
is one captured physical noise sequence. The tone is still synthetic and is
added after ADC quantization, so the experiment does not reproduce the full
microphone/PDM path.

An independent read-only review of the first analysis version caught a
frequency-fit bias, an FIR sample-rate mismatch, the sparse phase grid, and
missing clipping/absolute-level reporting. Those were corrected. A later
review caught that the output-window alignment still subtracted rather than
added causal FIR delay. The aligned rerun below uses the same source-time
interval across linear-phase filters. IIR/AA3 group delay remains
uncompensated.

Across the corrected test matrix (five frequencies, three amplitudes and 12
phases), FIR255's average of per-case median SNR gains was **+1.37dB**, and its
worst individual phase case remained **+0.82dB**. Its mean 3.2kHz tone level
change was +0.03dB. FIR127 averaged +1.26dB with a −0.14dB worst phase; its
mean 3.2kHz tone change was −1.11dB. FIR7 box averaged +1.00dB with a −0.18dB
worst phase and −0.79dB mean 3.2kHz tone change. No input count clipping or
PCM full-scale output clipping occurred in these trials.

This supports FIR255 as the strongest **offline tone-fidelity lead** among
the tested filters, but not a speech or intelligibility conclusion. All
trials reuse the same approximately 32ms noise sequence; phase sweeps are
within-record sensitivity checks, not independent noise realizations. The
tone is added after ADC count quantization, so this still does not recreate
the full microphone/PDM path.

Reproduction: `tools/k6_gp0_empirical_noise_injection.py`; corrected metrics:
`recovery/gp0_raw/empirical-noise-injection-aligned-20261127.json`. The prior
`empirical-noise-injection-20261127.json` is retained but has incorrect FIR
window alignment and must not be used for filter ranking. Read-only review
`194c31546643` identified first-pass analysis issues; follow-up review
`51ddb7dea427` caught the remaining alignment error. The corrected results
were rerun locally. No speech intelligibility claim follows from tone SNR.

## Follow-up: offline ASR word-recovery proxy for playback filters

The locally cached faster-whisper base.en model can run on the existing
CPython 3.11 runtime by loading its already-cached dependencies directly;
there was no package installation or model download. The new
`tools/k6_sotto_asr_proxy_study.py` evaluates three public LibriSpeech/Codec2
voices and their paired no-channel Sotto v3.5 decodes. It runs the clean
source through the same recognizer, then reports WER of each level-matched
decoded/filter variant against that **ASR-generated clean-source transcript**.
Transcripts themselves are not saved in the report.

The result is voice-dependent. With beam 5, `strong` changed WER versus the
unfiltered decode by −10.9 percentage points for the female sample, +7.1
points for one male LibriSpeech sample, and 0 for the ve9qrp sample. `focused`
changed those by −6.5, +4.8 and 0 points respectively. Repeating with greedy
beam 1, `strong` and `focused` each improved the female sample by 4.3 points,
while all treatments were unchanged on both male samples. The recognizer's
beam setting therefore changes the observed effect, and the small three-voice
set gives no general filter winner. The less aggressive filters also varied
by voice and decoding mode.

This is an objective word-recognition proxy, not a human listening test: the
reference transcript is model-generated rather than verified against the
LibriSpeech transcript, ASR WER can disagree with listener judgments, and the
filters are downstream playback treatments on public Sotto codec output. They
cannot establish GP0 input fidelity. Results are metrics-only in
`recovery/gp0_raw/sotto-asr-proxy-study-20261127.json` (beam 5) and
`recovery/gp0_raw/sotto-asr-proxy-beam1-20261127.json` (beam 1). The blind
three-excerpt GP0 listening scores remain necessary to determine which, if
any, treatment actually improves readability on the target recordings.

## Follow-up: PESQ and ESTOI on the same public speech pairs

The project's cached `pesq` and `pystoi` packages were usable without install
or download, so the same three source/decoded pairs were scored with the
repo-standard fixed 160-sample codec-delay alignment. The five level-matched
playback variants show a clear quality/readability tradeoff:

| Voice | Treatment | PESQ change | ESTOI change |
|---|---|---:|---:|
| Female LibriSpeech 198 | gentle | +0.027 | −0.0004 |
| Female LibriSpeech 198 | strong | +0.029 | −0.0352 |
| Male LibriSpeech 3436 | focused | +0.032 | −0.0650 |
| Male LibriSpeech 3436 | strong | +0.049 | −0.0937 |
| Male ve9qrp | presence | +0.020 | −0.0022 |
| Male ve9qrp | strong | −0.058 | −0.0374 |

No treatment improves both metrics consistently across all three voices.
`strong` often raises PESQ on two voices while substantially lowering ESTOI;
it lowers both metrics on ve9qrp. That helps explain why cleaner-sounding
filters cannot be selected by tone SNR or PESQ alone. The ASR proxy result is
also not enough to select them: model-generated WER improved for the female
sample in both beam settings, while ESTOI fell for the strong treatment.

These metrics are from public no-channel Sotto v3.5 codec output, not target
GP0/K6 recordings, and the sample is only three voices with no confidence
intervals. PESQ/ESTOI remain objective proxies rather than listener ratings.
Reproduce via `tools/k6_sotto_perceptual_proxy_study.py`; metrics-only output:
`recovery/gp0_raw/sotto-perceptual-proxy-study-20261127.json`. The blind GP0
listening trial is still required before making a readability recommendation.
To make that human trial easier, a local-only player and score exporter is at
`/home/tyler/flux-bench/gp0-fidelity-lab-20261127/blind-multiclip-v1/listen-and-score.html`;
it embeds no answer-key data and makes no network requests.

## Follow-up: eight held-out Sotto v3.8 voices

The same PESQ/ESTOI comparison was expanded to the eight held-out LibriSpeech
speakers already shipped as Sotto v3.8 no-channel bitstreams. The current C
decoder and the repository's pinned Codec2 source (revision `598f1bc`) were
built in temporary directories, then decoded speech and 16kHz clean references
were both downsampled to the project's standard 8kHz PESQ/ESTOI core. No build
products were written into the repository.

As a decoder/alignment cross-check, the unfiltered results reproduce the
repository's published `sotto/docs/results/page_v38.json` baselines for all
eight held-out voices: maximum difference was 0.00273 PESQ and 0.00006 ESTOI.

| Treatment | Mean PESQ change | Voices with PESQ gain | Mean ESTOI change | Voices with ESTOI loss |
|---|---:|---:|---:|---:|
| gentle | +0.012 | 7/8 | −0.0053 | 8/8 |
| presence | +0.024 | 7/8 | −0.0055 | 8/8 |
| focused | +0.040 | 8/8 | −0.0274 | 8/8 |
| strong | +0.048 | 8/8 | −0.0432 | 8/8 |

This larger held-out set supports the same tradeoff seen in the earlier three
voices: filters tend to raise PESQ while lowering ESTOI, and stronger
suppression causes larger ESTOI losses. `gentle`/`presence` have smaller
changes but still reduced ESTOI on every one of the eight clips. There is no
objective basis here for recommending any filter for intelligibility. The
sample expands speaker coverage, not independent sessions, and the scores are
still not listener judgments or GP0 hardware evidence.

Reproduce with `tools/k6_sotto_perceptual_proxy_v38.py`; the decoder build is
temporary and uses the repository Codec2 source. The original Sotto baseline
cross-check is recorded in the metrics report. Metrics-only output is
`recovery/gp0_raw/sotto-perceptual-proxy-v38-20261127.json`. The initial
three-voice v3.5 study and this eight-voice v3.8 study should remain separate:
they use different Sotto versions and codec paths.

## Follow-up: playback filters on paired simulated radio-channel clips

The same v3.8 decoder and scoring path was applied to the four radio-channel
bitstreams whose shipped sample set includes a matching clean 16kHz reference:
female LibriSpeech 198 and male LibriSpeech 3436, each under the two published
channel conditions (146MHz/30kmh/56dB-Hz and 446MHz/60kmh/52dB-Hz). The `.good`
frame masks were passed to the C decoder. Good-frame fractions ranged from
93.7% to 98.1% across these clips. Two additional ve9qrp channel bitstreams are
shipped without a matching `__original16.flac`, so they were excluded from
reference-based scoring.

| Treatment | Mean PESQ change | PESQ gain | Mean ESTOI change | ESTOI loss |
|---|---:|---:|---:|---:|
| gentle | +0.020 | 4/4 | −0.0117 | 4/4 |
| presence | +0.008 | 2/4 | −0.0118 | 4/4 |
| focused | +0.028 | 4/4 | −0.0443 | 4/4 |
| strong | +0.060 | 4/4 | −0.0661 | 4/4 |

The simulated channel clips show the same pattern as the no-channel v3.8
results: PESQ generally rises while ESTOI falls, with stronger filtering causing
larger ESTOI losses. The male 3436 cases have especially large ESTOI losses
under focused/strong suppression (about 0.068–0.098), despite small or positive
PESQ changes. This provides no objective basis for a general readability
recommendation. These are still public simulated codec/channel clips, not
on-air or GP0 recordings, and the sample covers only two voices under two
simulated channel conditions each.

Reproduce with `tools/k6_sotto_perceptual_proxy_v38.py --include-radio`; the
metrics-only report, including the eight no-channel cases, is
`recovery/gp0_raw/sotto-perceptual-proxy-v38-with-radio-20261127.json`.

## Follow-up: measured-inspired GP0 source noise through the real Sotto v3.8 codec

The most useful speech vectors for the GP0 question are the eight held-out
LibriSpeech excerpts and their paired v3.8 no-channel codec samples in
`sotto/docs/samples/v38/`: each `librispeech_<id>__original16.flac` has a
matching `__No_radio_channel.bit` and decoded
`__No_radio_channel__codec2_2400.flac`. They provide clean references and
repeatable speech inputs for a pre-vocoder source-noise sweep. The four paired
radio-channel vectors are useful as separate decoder/channel robustness checks,
but are too few to stand in for a broad speech set. Codec2 raw speech files and
`sotto/tests/golden/` bitstreams are codec/regression vectors; they do not
exercise GP0 input capture or establish on-air DV readability.

`tools/k6_gp0_noise_sotto_v38_study.py` peak-normalizes each speech excerpt,
applies the RFSim K6 GP0 source model at 0.025, 0.05 and 0.1 Vpp scenarios,
then runs the real Sotto v3.8 8kHz encoder and decoder. It compares narrowband
PESQ and ESTOI against the same excerpt passed through the noise-free model
baseline. Each condition has three noise seeds. A second run changes the
statistical noise spectrum from band-shaped to white; results are effectively
the same (mean ESTOI deltas differ by no more than about 0.003), so this sweep
is presently sensitive mainly to the modeled noise RMS, not the assumed broad
spectral shape.

Across the eight voices, measured-model noise lowers ESTOI versus the
noise-free GP0-model baseline for all eight at each level. Mean source SNRs
are −12.96dB, −6.94dB and −0.92dB for 0.025, 0.05 and 0.1Vpp respectively.
An assumed 10dB reduction in source-noise RMS improves mean ESTOI, but leaves
residual gaps of approximately −0.375, −0.257 and −0.168. This makes 8–10dB
source-noise reduction a useful offline target to explore; it does not show
that such reduction is achievable by a causal embedded filter or necessary
for a listener to understand the speech.

Metrics-only outputs are
`recovery/gp0_raw/gp0-noise-sotto-v38-peaknorm-banded-20261127.json` and
`recovery/gp0_raw/gp0-noise-sotto-v38-peaknorm-white-20261127.json`. Treat the
older `gp0-noise-sotto-v38-unnormalized-reference-level-20261127.json` as
superseded: those input WAVs were not normalized to the source model's unit
peak reference. These are statistical source-model results, not matched
physical speech captures; the 0.1Vpp reference comes from tone-generator
calibration and is not speech SPL calibration. The 8kHz codec path omits the
v3.8 4–8kHz hiss band, and PESQ/ESTOI remain proxies. Human listening and a
calibrated spoken-input capture are still required before making a DV usability
claim.

## Follow-up: oracle spectral-mask headroom before the vocoder

To estimate how much of the modeled readability loss is in principle
recoverable by speech-aware suppression, `tools/k6_gp0_oracle_denoise_sotto_v38.py`
uses the exact clean speech and exact synthesized noise to construct three
32ms/8ms-hop masks, then sends their 8kHz audio through the same real Sotto
v3.8 encoder/decoder. This is intentionally an **oracle upper-bound study**:
clean speech and noise are not known in a radio, and the masks are not
causal or embedded implementations.

The ideal magnitude-ratio mask performed best. Relative to the noise-free
model baseline, average ESTOI gaps were −0.031, −0.018 and −0.006 at 0.025,
0.05 and 0.1Vpp, recovering most of the measured-noise loss at 0.1Vpp. It
exceeded the baseline in only 0/24, 1/24 and 4/24 speaker/seed pairs at those
levels, so this is near-recovery on average, not consistent improvement. The
oracle Wiener-power mask left larger mean gaps (−0.145, −0.082, −0.040), and
the hard binary mask was worse (−0.306, −0.186, −0.104). PESQ rose substantially
for the oracle magnitude mask, but ESTOI remains the more conservative
intelligibility proxy here.

This indicates that soft speech-shaped suppression has substantial modeled
headroom, especially at the stronger input scenario, but it does **not** yet
show that a practical online denoiser can realize that headroom. The next
offline comparison should replace oracle knowledge with estimable noise
statistics (noise-only pre-roll or a causal noise tracker), apply temporal and
frequency smoothing to control musical artifacts, then score all eight speech
vectors through Sotto. Metrics-only results:
`recovery/gp0_raw/gp0-oracle-denoise-sotto-v38-20261127.json`. The script
rebuilds the codec only in a temporary directory; no firmware or radio state
was changed.

## Follow-up: estimated-noise masks from a silence preamble

The next study replaces oracle clean/noise knowledge with a 500ms noise-only
GP0-model preamble. Four masks estimate speech power as `max(mixture power −
noise power, 0)`: Wiener-power and magnitude-ratio gains, each with or without
a 3-bin time/frequency smoothing pass and a 0.05 minimum gain. The masks see
the whole utterance STFT, so this is an offline estimate experiment rather
than a causal/latency-qualified implementation. Results are passed through the
real Sotto v3.8 encoder/decoder on all eight speech vectors, all three modeled
input levels and three noise seeds.

The first run accidentally reused the speech-noise seed for the noise-only
preamble, making the initial noise realization correlated with the speech
mixture. Its metrics are retained at
`recovery/gp0_raw/gp0-preamble-denoise-sotto-v38-20261127.json` for provenance,
but are superseded and should not be used as independent-pre-roll evidence.
The rerun uses a distinct deterministic preamble seed for every speech seed;
this removes the leaked noise realization without changing the modeled noise
level or speech cases.

In the corrected run, the 3×3-smoothed Wiener-power mask had the best average
ESTOI. It improved over unfiltered noisy codec output in 23/24 cases at 0.025Vpp
and 24/24 at 0.05 and 0.1Vpp. Mean ESTOI gains were +0.039, +0.070 and +0.068
respectively. At 0.1Vpp mean PESQ rose +0.158; at 0.025Vpp it fell −0.037
despite the ESTOI improvement. All filtered outputs still remained below the
noise-free model baseline, with mean ESTOI gaps near −0.48, −0.37 and −0.26.
So the correction changes no practical conclusion: the modeled soft mask makes
a repeatable partial improvement, not an established usability threshold or a
human-readability result.

This sweep varies no preamble duration or mask parameters yet. Tuning those
remains a useful next offline step, followed by listening/ASR on exported
examples. Corrected metrics-only output:
`recovery/gp0_raw/gp0-preamble-denoise-sotto-v38-independent-preamble-20261127.json`;
reproduction: `tools/k6_gp0_preamble_denoise_sotto_v38.py`.

## Follow-up: causal-frame noise-preamble masks

`tools/k6_gp0_causal_denoise_sotto_v38.py` re-runs the measured-noise model
through four preamble estimates (100, 250, 500 and 1000ms) and four masks:
plain Wiener power, one-sided temporally smoothed Wiener, smoothed magnitude
ratio, and 1.5x oversubtraction. Unlike the prior STFT mask sweep, every
speech frame's mask uses only that frame and past mask state; the 256-sample
frame has up to 32ms lookahead. Frequency smoothing is within the current
frame. A separate, independent synthetic noise realization supplies the
preamble. All outputs are evaluated through the real Sotto v3.8 codec
against eight speech vectors, three modeled input levels and three noise
seeds (24 cases per level).

At 500ms preamble, 1.5x oversubtraction had the largest mean ESTOI gain at
each level: +0.040 at 0.025Vpp, +0.064 at 0.05Vpp and +0.062 at 0.1Vpp, with
23/24, 24/24 and 24/24 cases better than unfiltered modeled-noise output.
However, filtered mean ESTOI remained 0.479, 0.377 and 0.262 below its
noise-free-model codec baseline. Preamble length and mask choice changed the
ranking modestly; the 500ms oversubtraction result is a proxy winner, not a
deployment setting. Full metrics:
`recovery/gp0_raw/gp0-causal-denoise-sotto-v38-20261127.json`.

## Follow-up: local ASR on causal-mask outputs

`tools/k6_gp0_causal_asr_sotto_v38.py` evaluates the 500ms causal mask outputs
with a locally cached faster-whisper base.en model, using the same ASR on
clean downsampled speech to produce a pseudo-reference transcript. No model
download is performed. With 1.5x oversubtraction, mean WER changed from 0.995
to 1.109 at 0.025Vpp (only 9/24 cases improved), 1.060 to 1.035 at 0.05Vpp
(10/24 improved), and 0.931 to 0.818 at 0.1Vpp (17/24 improved). Even at
0.1Vpp, clean Sotto-codec WER averaged 0.067 while noisy and denoised modeled
inputs remained near 0.93 and 0.82. A same-level 0.1Vpp mask sweep found
mean WER 0.778 plain Wiener (20/24 cases improved), 0.764 smoothed Wiener
(17/24), 0.806 smoothed magnitude-ratio (17/24), and 0.817 oversubtraction
(17/24). Thus ESTOI's top mask is not ASR's top mean-WER mask, and no one mask
wins for every case. Full outputs:
`recovery/gp0_raw/gp0-causal-asr-sotto-v38-20261127.json` and
`recovery/gp0_raw/gp0-causal-asr-mask-sweep-0p1vpp-v38-20261127.json`.

These remain statistical-model experiments, not speech captured from the
radio. The 0.1Vpp convention is a laboratory reference and not acoustic speech
calibration; the synthetic noise may not reproduce real RFIC/PDM behavior.
ASR uses a model-generated transcript, WER is only comparative, and ESTOI/ASR
do not substitute for a human listening test. The substantial gap between
denoised and noise-free speech means the current source model does not support
a claim of usable DV. The metrics disagree on mask ranking, so preserve the
several masks for blinded listening instead of selecting by a single proxy.

## Blinded listening set prepared

Prepared `recovery/gp0_raw/listening-causal-0p1vpp-v1/` for human review:
eight speech vectors × six treatments (noise-free codec anchor, noisy model
baseline, and the four causal masks), at 0.1Vpp with deterministic noise seed
83. Each speaker's six clips are independently label-randomized, rendered as
8kHz mono PCM, and RMS-matched to that speaker's clean-codec output with peak
clipping prevented. The directory contains a blinded `blind/` folder,
listener instructions and a separate `answer-key.json`. It was verified to
contain 48 files, six distinct conditions per speaker and no clipped samples.
The pack is a modeled listening exercise only; reviewers should score words
understood, listening effort, naturalness and artifacts without treating the
clean anchor as a radio capture. Reproduction: `tools/k6_gp0_causal_listenpack_sotto_v38.py`.

The objective ASR/PESQ/ESTOI metrics are not a replacement for this listening
result. No human scores have been collected yet, and there is still no
physical spoken-sample capture through GP0.

## Triage of the later speech-labelled RF recordings

The three files under `/tmp/flux-speech-tests/` are separate from the matched
tone set above. Their capture metadata confirms complete 20-second IQ files at
0dB SDR gain, but RF activity is intermittent. The FM quiet control behaved
like carrier-only audio in its active intervals. The speech-labelled FM file
had brief modulation bursts; local faster-whisper returned only `A-G7-F-U`,
which is not reliable transcription. The file named `gp0_speech_g0` was made
with the generic SDR IQ capture tool, not the GP0 RAW AIR receiver. Reprocessing
its active RF portion at the GP0 IF yielded 3 CRC-valid packets (sequences
1, 3 and 5) from one session token, far short of the 86-packet complete dump
required for trustworthy popcount reconstruction. Faster-whisper returned only
`You`, also not reliable evidence of intelligible speech. Treat these files as
incomplete/ambiguous exploratory captures; they do not add a valid analog-vs-
GP0 speech quality comparison. The matched 500Hz set and the offline synthetic
experiments remain the usable evidence. No changes were made to firmware,
radio configuration, or RF hardware.

## Follow-up: Wiener noise-estimate tuning and cross-metric check

Extended the causal Wiener sweep over six noise-power multipliers (0.5x to
2.0x) and two retained-gain floors (0.02 and 0.05), with unsmoothed and
temporally/frequency-smoothed forms at a 0.05 floor. The screen used eight
speech vectors, three independent banded-noise seeds, 0.1Vpp modeled input,
and the Sotto v3.8 codec: 24 cases per setting. Wiener with a 2.0x noise
estimate and 0.02 floor ranked first in both ESTOI and narrowband PESQ:
mean ESTOI 0.33131 (+0.08043 against noisy codec output) and mean PESQ-NB
1.512. It exceeded noisy ESTOI in all 24 cases. The 2.0x/0.05 variant was
second, close behind. Script and full report:
`tools/k6_gp0_wiener_parameter_sweep_v38.py` and
`recovery/gp0_raw/gp0-wiener-param-sweep-v38-20261127.json`.

Then checked the top 2.0x/0.02 setting with local faster-whisper base.en
against the 1.0x/0.02 Wiener mask over the same 24 cases. Mean pseudo-reference
WER was 0.790 at 2.0x versus 0.806 at 1.0x; both settings improved 19/24
cases relative to unfiltered modeled noise. Mean noisy WER was 0.930 and the
clean-codec reference was 0.067. This is a small favorable cross-metric signal,
not evidence of acceptable speech readability; the large clean-to-noisy gap
remains. ASR report:
`recovery/gp0_raw/gp0-wiener-param-asr-v38-20261127.json`.

Prepared a new 48-clip blinded pack at
`recovery/gp0_raw/listening-causal-0p1vpp-tuned-v2/` with clean and noisy
anchors, the 1.0x/0.02 Wiener mask, both 2.0x Wiener floors, and the earlier
1.5x oversubtraction mask. Each of eight voices has six randomized, RMS-matched
clips. The WAVs have zero clipped samples. This is ready for human scoring;
until listening scores exist, 2.0x/0.02 is only the best proxy-ranked
candidate. The source is still statistical, not captured speech, and the
host-side filter has not been qualified for K6 compute or latency.

## Listener scores and refined Wiener neighborhood (2026-11-28)

The first partial listener export is preserved with its unblinding summary in
`recovery/gp0_raw/listening-causal-0p1vpp-tuned-v2/`. All 66 submitted fields
mapped to the answer key. The apparently perfect clips speaker01-D,
speaker02-C, and speaker03-C are the intentional `clean_codec` references;
all eight clean anchors received 5/5 intelligibility. These noise-free codec
references are ceilings, not radio speech.

Across the partial ratings, Wiener scale 2/floor 0.05 averaged 3.57/5 for
intelligibility (7 ratings), ahead of oversubtraction 1.5 at 3.14/5 (7
ratings). On the same seven speakers, it won four comparisons, tied two, and
lost one. Ratings for effort and naturalness are too sparse for treatment
comparisons. This is one listener on modeled clips, so it is a candidate
selection signal only.

To explore the listener-preferred setting's neighborhood, ran 20 Wiener
settings over the same eight speech vectors and three deterministic noise
seeds (24 cases per setting), using the real Sotto v3.8 codec and PESQ-NB/
ESTOI proxies. In this run, scale 2.5/floor 0.03 ranked highest ESTOI (0.33346,
+0.08257 vs noisy; 24/24 cases improved), while scale 2.25/floor 0.03 ranked
highest PESQ-NB (1.5158; 24/24 ESTOI cases improved). Scale 2/floor 0.05,
the partial human-listening favorite, scored ESTOI 0.32858 (+0.07769; 24/24)
and PESQ-NB 1.4896. The neighboring candidates are close and the human and
objective rankings do not identify one universal optimum. Full report:
`recovery/gp0_raw/gp0-wiener-listener-neighborhood-sweep-20261128.json`.

Prepared a second blind pack at
`recovery/gp0_raw/listening-causal-0p1vpp-refined-v3/`, comparing the
listener-favored scale 2/floor 0.05 against the objective leaders 2.25/0.03
and 2.5/0.03, with clean/noisy anchors and oversubtraction. All 48 WAV hashes
match the answer key, each is mono 8kHz PCM, and none contains clipped
samples. The page remains blinded; its key is separate from the player.

The earlier ASR reports disagree on some repeated named cases. Five
transcriptions of one exact blinded WAV in the same faster-whisper process
were identical; a fresh-process repeatability check is recorded as a separate
diagnostic. Because prior ASR audio intermediates were not saved, those WER
reports cannot establish reproducibility on identical PCM. The refined
candidate ranking therefore relies on the 24-case codec/perceptual-proxy sweep
and partial human scores, not the conflicting ASR numbers. All this remains
offline modeled evidence, not physical-radio intelligibility or K6 MCU/
latency qualification.

## Fixed-SNR, pre/post-codec Log-MMSE comparison (2026-11-28)

The directed Neon review recommended separating source-SNR effects, denoiser
effects, and the Sotto codec boundary. Added
`tools/k6_gp0_snr_logmmse_study.py` and ran it over eight speech vectors ×
three independent noise seeds at target source SNRs of −1, +2, +5, and +8dB.
Every source-noise realization measured at its target SNR after the GP0 source
model, and none clipped. Each case used the same independent 500ms noise
preamble for all filters. The report retains PESQ-NB/ESTOI scores before and
after encoding/decoding through the temporary Sotto v3.8 host codec:
`recovery/gp0_raw/gp0-snr-logmmse-study-20261128.json`.

At −1dB, the noisy post-codec mean ESTOI was 0.24376. The best existing
Wiener candidate (2.5× noise-power scale, 0.03 gain floor) reached 0.33405
(+0.09029, better in 24/24 cases); the human-favored 2×/0.05 setting reached
0.32999 (+0.08623). Decision-directed Wiener and Log-MMSE at α=0.98/floor
0.05 reached 0.30042 and 0.29378. At +8dB, noisy scored 0.39711; Wiener
2.5×/0.03 reached 0.46260, while the two decision-directed variants scored
0.41952 and 0.41320. Thus all tested methods improve on noisy output, but the
first Log-MMSE configuration does not beat the current simple Wiener filters.

| Target SNR | Noisy ESTOI / PESQ-NB | Wiener 2×/0.05 | Wiener 2.5×/0.03 | DD Wiener α=.98 | DD Log-MMSE α=.98 |
|---:|---:|---:|---:|---:|---:|
| −1dB | 0.2438 / 1.2878 | 0.3300 / 1.4789 | 0.3341 / 1.4979 | 0.3004 / 1.4176 | 0.2938 / 1.4431 |
| +2dB | 0.3005 / 1.3465 | 0.3785 / 1.6432 | 0.3835 / 1.6640 | 0.3451 / 1.5596 | 0.3372 / 1.5952 |
| +5dB | 0.3537 / 1.4258 | 0.4200 / 1.8197 | 0.4247 / 1.8268 | 0.3845 / 1.6940 | 0.3771 / 1.7546 |
| +8dB | 0.3971 / 1.5324 | 0.4572 / 1.9992 | 0.4626 / 1.9787 | 0.4195 / 1.8289 | 0.4132 / 1.9105 |

Values are mean post-codec ESTOI / PESQ-NB over 24 cases per row. At +8dB,
ESTOI slightly favors Wiener 2.5×/0.03 while PESQ-NB favors Wiener 2×/0.05;
there is no metric-independent winner.

The noise-free modeled input scored 0.99996 ESTOI before the codec and 0.57455
after it. The best filtered output scored 0.33405 at −1dB and 0.46260 at +8dB
after the codec, leaving gaps of −0.24050 and −0.11195 against the clean-codec
reference. These pre/post metrics show a substantial codec-boundary penalty,
but do not prove it is caused only by the vocoder: this remains a model-based
test scored against the original reference, with no physical radio channel.
Increasing input SNR helps substantially; even +8dB is not near the clean
reference. The existing model does not establish that +8dB is attainable with
real spoken input.

Follow-up `tools/k6_gp0_dd_parameter_sweep.py` varied decision-directed α
across 0.50, 0.70, 0.85, 0.95 and 0.98 at −1dB (24 cases per setting). The
best tested decision-directed Wiener setting, α=0.85/floor 0.05, scored
0.32036 post-codec ESTOI (+0.07660 over noisy, 24/24 improved), below the
static Wiener 2.5×/0.03 result of 0.33405. The best tested Log-MMSE setting
was also α=0.85/floor 0.05 at 0.30714 (+0.06338 over noisy). Lowering α helps
relative to α=0.98 but does not close the gap, so this Log-MMSE branch is not
the current leading offline candidate. Decision-directed Wiener α=0.95 had
the sweep's highest mean PESQ-NB (1.4951), close to static Wiener 2.5×/0.03
(1.4979), while ESTOI remained lower; that does not justify selecting it
without a properly blinded listener round. Full sweep:
`recovery/gp0_raw/gp0-dd-alpha-sweep-20261128.json`.

The attack/release sweep also should not be used to select a treatment yet:
the implementation enables three-bin frequency smoothing whenever the
method is named `wiener_smoothed`, so its asymmetric temporal runs combine two
changes. Separating those factors would be required before drawing conclusions
about attack/release alone.

Reproduce the two completed comparisons with the locally provisioned metrics
environment (the base interpreter in this session lacked PESQ/STOI). The
output path must be new because each script refuses to overwrite reports:

```sh
uv venv --python python3 /tmp/gp0-fidelity-review-venv
uv pip install --python /tmp/gp0-fidelity-review-venv/bin/python \
  numpy scipy soundfile pesq pystoi

/tmp/gp0-fidelity-review-venv/bin/python tools/k6_gp0_snr_logmmse_study.py \
  --json-out /tmp/gp0-snr-logmmse-rerun.json

/tmp/gp0-fidelity-review-venv/bin/python tools/k6_gp0_dd_parameter_sweep.py \
  --json-out /tmp/gp0-dd-alpha-rerun.json
```

Both reports are offline simulation only. No radio, firmware, or RF settings
were changed. A future listening comparison needs new utterances and a fresh
pack-specific permutation/storage identifier: the existing v2, refined-v3,
and asym-v4 packs reuse utterances and deterministic label assignments, and
the clean control labels from v2 have already been identified.

## Fixed-preamble filter under changing noise level (2026-11-28)

To test whether the stationary-noise assumption is a practical weakness,
`tools/k6_gp0_nonstationary_noise_study.py` applies five level envelopes to
the same statistical banded noise: stationary, abrupt +6dB and −6dB steps,
250ms bursts at 9.5dB above the local floor every 2s, and a smooth 6dB swell.
Each complete utterance is renormalized to the same −1dB global source SNR;
the 500ms preamble retains the profile's true initial local noise level. For
each profile, a fixed-preamble Wiener mask is paired with an
**envelope-informed oracle** mask that receives the exact current noise-level
envelope. The oracle estimates the current level perfectly from simulated
ground truth and is an attribution ceiling, not an implementable receiver
algorithm. Full 8-voice × 3-seed output:
`recovery/gp0_raw/gp0-nonstationary-noise-study-20261128.json`.

| Noise profile | Noisy ESTOI / PESQ-NB | Fixed Wiener ESTOI / PESQ-NB | Envelope-informed ESTOI / PESQ-NB | Oracle ESTOI gain vs fixed |
|---|---:|---:|---:|---:|
| Stationary | 0.2438 / 1.2878 | 0.3334 / 1.4975 | 0.3334 / 1.4975 | 0.0000 |
| Step up 6dB | 0.2623 / 1.3045 | 0.3247 / 1.3635 | 0.3446 / 1.5290 | +0.0199 (24/24) |
| Step down 6dB | 0.2564 / 1.3063 | 0.3211 / 1.4204 | 0.3454 / 1.5235 | +0.0243 (24/24) |
| Bursts 9.5dB | 0.2701 / 1.3252 | 0.3405 / 1.2661 | 0.3530 / 1.5321 | +0.0125 (22/24) |
| Smooth swell 6dB | 0.2505 / 1.2857 | 0.3335 / 1.4592 | 0.3408 / 1.5180 | +0.0073 (19/24) |

The fixed-preamble mask improves ESTOI over unfiltered modeled audio in all
24 cases for every profile, but the burst-profile PESQ-NB mean falls below its
unfiltered mean. Exact envelope knowledge gives a repeatable additional
ESTOI improvement for the abrupt steps and a smaller improvement for the
bursts/swell. This supports investigating causal level tracking, but the
oracle gap is moderate and does not show that a deployable tracker will reach
it. A broader nonstationary noise-spectrum model and speech/VAD-aware causal
tracking remain untested. There was no source clipping, and measured global
SNR was −1.00dB in all profiles; the variability therefore comes from noise
timing, not a larger total noise dose. These are still simulated clips and
objective proxies, not physical speech or human-readability evidence.

Reproduce with the temporary metric environment used above, choosing a new
output path because the script refuses to overwrite:

```sh
/tmp/gp0-fidelity-review-venv/bin/python tools/k6_gp0_nonstationary_noise_study.py \
  --json-out /tmp/gp0-nonstationary-noise-rerun.json
```

## Fixed-point slim encoder voicing response (2026-11-28)

The outside review proposed that GP0 noise could push the on-radio fixed-point
Sotto slim encoder toward its unvoiced class. To check that mechanism directly,
`tools/k6_gp0_slim_voicing_study.py` builds the repository's
`c/fixed/sotto_fx_slim.c` for the host and adds a temporary class counter at
the half-frame decision point. It uses eight speech vectors, three
noise seeds, and modeled GP0 source noise at 0, 6, and 12dB source SNR. The
Wiener condition uses the existing causal preamble filter (scale 2, floor
0.05). The instrumented copy and shared library exist only in a temporary
directory; no fixed-point source, firmware image, or radio was modified.

| Input | Class 0 | Class 1 | Class 2 |
|---|---:|---:|---:|
| Clean modeled GP0 speech | 38.6% | 41.8% | 19.6% |
| 0dB GP0 noise, unfiltered | 63.8% | 13.7% | 22.5% |
| 0dB GP0 noise, Wiener | 41.8% | 36.3% | 21.9% |
| 6dB GP0 noise, unfiltered | 51.8% | 28.3% | 19.9% |
| 6dB GP0 noise, Wiener | 40.0% | 41.1% | 18.8% |
| 12dB GP0 noise, unfiltered | 45.4% | 36.0% | 18.6% |
| 12dB GP0 noise, Wiener | 39.7% | 42.4% | 17.9% |

Percentages are unweighted means across eight voices and three seeds per SNR.
Class 0 includes legitimate unvoiced speech and quiet-tail suppression; it is
not an error counter. Still, the consistent rise under noise and return toward
the clean distribution after Wiener filtering support the proposed codec
interaction. This is a mechanism result only: class distributions do not prove
better decoded intelligibility, and the noise is a statistical model rather
than a sample-exact physical recording. There was no clipping. Full per-voice
counts and actual SNR values are in
`recovery/gp0_raw/gp0-slim-voicing-study-20261128.json`.

The next useful codec experiment should compare decoded output and frame-class
counts together while varying pitch-path filtering or energy compensation in
temporary source copies. A lower unvoiced fraction alone must not select a
change because legitimate fricatives and silence should remain unvoiced.

Cross-checking the review's proposed pitch-path fix against the implemented
seven-tap coefficients found a material response error in its rationale. The
existing `(1,2,3,4,3,2,1)/16` kernel, evaluated at its 8kHz input rate, is
already about −7.4dB at 1kHz, −13.6dB at 1.3kHz, and has a zero at 2kHz; it is
not −2.4dB at 1.3kHz and −6dB at 2kHz. An additional `(1,2,1)/4` pass at the
4kHz x4 rate would attenuate 500Hz by about 1.4dB and 1kHz by 6dB. Therefore
the claimed 3–4dB pitch-SNR gain is not established, and that filter candidate
needs direct codec-output testing before it can be considered beneficial.

## Causal tracker response-weight sweep and new blind listening set (2026-11-28)

To check whether the promising step-down result depended on the tracker's
temporal smoothing, `tools/k6_gp0_tracker_response_sweep.py` compared three
EMA previous-scale weights (0.50, 0.75, 0.90) at spectral quantiles 0.20 and
0.35. It reused eight Sotto v3.8 speech vectors, three noise seeds, the same
measured-response RFSim GP0 source/noise model, −1dB global source SNR, and
temporary host Sotto v3.8 codec. Only stationary and 6dB step-down conditions
were included. Report:
`recovery/gp0_raw/gp0-tracker-response-sweep-20261128.json`.

| Condition | Static Wiener | Tracker q=.20, weight=.90 | Tracker q=.35, weight=.75 |
|---|---:|---:|---:|
| Stationary ESTOI / PESQ-NB | 0.33335 / 1.49753 | 0.33471 / 1.47873 | 0.33311 / 1.46592 |
| Step-down ESTOI / PESQ-NB | 0.32105 / 1.42039 | 0.34699 / 1.50125 | 0.34534 / 1.48848 |

For step-down noise, q=.20/weight=.90 beat the static filter on ESTOI in
24/24 paired voice-seed cases; q=.35/weight=.75 did so in 24/24 as well.
The larger mean ESTOI gain for q=.20/weight=.90 was +0.02594. On stationary
noise, no tracker setting consistently beat static filtering; q=.20/weight=.90
had a small +0.00136 mean ESTOI difference but won only 13/24 pairs and had
lower PESQ-NB. Thus the stronger result is conditional on the modeled falling
noise profile, not a general tracker win. None of these metric differences
establishes human preference, and the scalar tracker still lacks speech
activity detection.

A blind listening set is available at
`recovery/gp0_raw/listening-tracker-stepdown-response-v1/blind/index.html`.
It contains two previously unused speech excerpts and six level-matched
versions per excerpt: clean codec anchor, noisy input, fixed Wiener, q=.20
and q=.35 trackers at weight=.75, and the sweep-leading q=.20 tracker at
weight=.90. Each excerpt has an independent randomized A–F mapping; its
answer key is outside the browser directory. The noise is simulated 6dB
step-down noise, not a radio capture. The earlier five-way set remains at
`recovery/gp0_raw/listening-tracker-stepdown-v1/` for provenance.

Reproduce the metric sweep in the existing temporary metrics environment:

```sh
PYTHONPATH=tools:/storage5/dev/rfsim-k6-gp0-20261127 \
  /tmp/gp0-fidelity-review-venv/bin/python \
  tools/k6_gp0_tracker_response_sweep.py
```

The script writes only the fixed report path and refuses to overwrite it.
The listening-set generator takes a required new output path and refuses to
overwrite an existing directory. These studies and listening files are local
and uncommitted; no radio, firmware, or RF settings were changed.

## Tracker result uncertainty and estimator diagnosis (2026-11-28)

The 24 cases per profile reuse eight voices with three seeds each, so the
tracker response sweep also has a paired voice-cluster bootstrap in
`tools/k6_gp0_tracker_response_analysis.py`. For q=.20/weight=.90 versus
static Wiener, the step-down mean ESTOI difference is +0.02594, with a
voice-clustered 95% interval of **+0.02110 to +0.03094**; every one of the
eight voice means is positive. For stationary noise the difference is only
+0.00136, its interval is **−0.00139 to +0.00434**, and only three of eight
voice means are positive. This supports a conditional step-down benefit in
the model, not a stationary-noise improvement or a population-level claim.
Full paired PESQ/ESTOI intervals and per-voice differences are in
`recovery/gp0_raw/gp0-tracker-response-analysis-20261128.json`.

To explain the smoothing effect, `tools/k6_gp0_tracker_estimator_diagnostics.py`
measures the same quantile/EMA level estimator on both noise-only signals and
speech-plus-noise mixtures, over stationary, up-step, down-step, and burst
profiles (eight voices × three seeds). At −1dB global SNR, for q=.20 the
speech-present scale-estimate RMSE on the 6dB step-down is 0.2086 at weight
.50, 0.1780 at .75, and 0.1529 at .90; correlations to the known scale
envelope are .913, .942, and .964. The same ordering appears for the step-up.
On stationary noise, .90 also suppresses frame-to-frame estimate variance,
but its ESTOI differences from static Wiener remain statistically
indeterminate. For 250ms bursts, the fast .50 setting has lower tracker RMSE
than .90 (1.5485 vs 1.9356), although both estimates understate burst power
and the earlier codec study found no decisive broad readability winner. Thus
.90 appears to trade burst response speed for stability during sustained
level transitions; it is not a universal setting.

The speech-present estimator is systematically biased high relative to the
known noise-only scale because speech energy enters the current-frame
quantile. At q=.20/weight=.90, mean scale bias is +0.1027 on step-down and
+0.1632 on stationary mixtures. This directly supports testing speech-aware
update gating or a speech-presence probability next. The diagnostic reports
per-case scale-estimation summaries and is reproducible with:

```sh
PYTHONPATH=tools:/storage5/dev/rfsim-k6-gp0-20261127 \
  /tmp/gp0-fidelity-review-venv/bin/python \
  tools/k6_gp0_tracker_estimator_diagnostics.py
```

The generated per-case diagnostic report is
`recovery/gp0_raw/gp0-tracker-estimator-diagnostics-20261128.json`. This
diagnostic evaluates noise-level estimation only; it does not establish that
speech-aware gating will improve decoded audio. All data remain simulated
and offline.

## First human listening scores for the tracker set (2026-11-28)

The user supplied `gp0-tracker-listening-scores.json` (created
2026-11-28T02:42:25Z). Its ten entries are intelligibility ranks for the
earlier five-way pack `listening-tracker-stepdown-v1`; they are not ratings of
the newer six-way response pack. The answer key was used only after the
scores were available. Summary is preserved at
`recovery/gp0_raw/listening-tracker-stepdown-v1/scores-summary.json`.

| Treatment | Utterance 1 rank | Utterance 2 rank | Mean rank (1 best) | Exact-stimulus ESTOI, mean | PESQ-NB, mean |
|---|---:|---:|---:|---:|---:|
| Clean codec anchor | 1 | 1 | 1.0 | 1.0000 | 4.5486 |
| Fixed Wiener | 3 | 2 | 2.5 | 0.4945 | 1.4088 |
| Causal tracker q=.20, weight=.75 | 2 | 4 | 3.0 | 0.5336 | 1.4825 |
| Causal tracker q=.35, weight=.75 | 4 | 3 | 3.5 | 0.5348 | 1.4685 |
| Noisy codec | 5 | 5 | 5.0 | 0.4029 | 1.1836 |

The listener placed clean first and noisy last in both excerpts. Among the
filtered versions, fixed Wiener had the best mean subjective rank, while q=.20
was second and q=.35 third. Tracker order changed between excerpts. This does
**not** select fixed Wiener as a general winner: the result is one listener,
two excerpts, one simulated step-down noise realization, and only the
intelligibility field was filled. There are no consonant, effort, naturalness,
or artifact scores in the export.

For context, the exact WAVs from this pack were also scored against each
excerpt's clean-codec anchor. ESTOI places q=.20 and q=.35 nearly tied and
ahead of fixed Wiener, which differs from the listener's average ordering.
On utterance 2 specifically, the listener ranked fixed Wiener above both
trackers even though both ESTOI values exceeded fixed Wiener. This confirms
that objective metrics should remain proxies and listening feedback is
decision-relevant. The objective scores are from these exact stimuli, but
they are not additional independent speech samples or listeners.

The score summarizer now accepts both older per-file answer-key layouts and
the current per-utterance randomized mapping, including A–F ranks. It refuses
unmapped entries and writes a separate summary rather than modifying the
supplied score file.

## Asymmetric tracker check (2026-11-28)

I compared the q=.20 asymmetric attack/release tracker (previous-scale EMA
weight .50 when estimated noise rises, .90 when it falls) with symmetric
weights .75 and .90, static preamble Wiener, and unfiltered audio. The offline
run uses eight simulated voice vectors and three noise seeds (24 cases per
profile) across stationary noise, 6dB up/down steps, 9.5dB bursts, and a 6dB
swell. The report is
`recovery/gp0_raw/gp0-asymmetric-tracker-study-20261128.json`; reproduce it
with:

```sh
PYTHONPATH=tools:/storage5/dev/rfsim-k6-gp0-20261127 \
  /tmp/gp0-fidelity-review-venv/bin/python \
  tools/k6_gp0_asymmetric_tracker_study.py
```

The asymmetric version beat static Wiener in mean ESTOI in every profile,
but it did not beat the symmetric q=.20/.90 tracker in any profile. Its mean
ESTOI was lower by .0013 to .0063 across the five profiles; PESQ-NB was also
lower in all five. Relative to symmetric .75, the asymmetric version was
slightly better on step-up ESTOI (+.0004), but lower on the other four
profiles and lower on PESQ-NB in all five. These small proxy-metric differences
do not establish listener preference; the asymmetric version has no current
reason to displace the simpler symmetric tracker.

This remains a simulated host-side result. None of the tracker or denoiser
variants is a radio-ready firmware change: there is no target-MCU timing,
memory/stack, linked-image, or on-radio listening qualification. No radio
configuration or firmware was changed.

## Speech-gated noise-tracker diagnostic (2026-11-28)

The prior estimator diagnostic showed that speech energy biases the q=.20
tracker high. I tested a simple spectral-contrast gate: freeze the .90 EMA when
the normalized q80/q20 current-to-preamble spectral ratio exceeds a threshold.
Across the same eight voices, three seeds, and five level profiles, thresholds
2 and 3 gated only about 0.6–6.6% and 0.3–1.8% of frames respectively. They
barely changed estimator error. Threshold 1.5 gated 15–19% and increased
step-down RMSE from .153 to .164 and burst RMSE from 1.936 to 1.974. Threshold
1.25 gated 34–37% and performed worse on every profile. Full threshold sweep:
`recovery/gp0_raw/gp0-speech-gate-tracker-diagnostics-20261128.json`.

I also measured an oracle upper bound that freezes updates whenever the known
source speech frame RMS exceeds 12% of that utterance's peak frame RMS. This
reduces scale RMSE from .225 to .087 on stationary noise and .153 to .124 on
step-down noise, but increases it from .380 to .434 on step-up noise and 1.936
to 2.426 on bursts. It gates 57% of frames. So speech-aware freezing can remove
speech bias when the noise is steady or falling, but it can badly miss genuine
noise increases while speech is active. This oracle is not implementable as-is;
it only shows that VAD gating by itself is insufficient. Results and per-case
data are in `recovery/gp0_raw/gp0-speech-gate-tracker-diagnostics-20261128-oracle.json`.

The spectral-contrast heuristic has no useful operating point in this sweep,
and the oracle shows the tradeoff a future detector must solve. No codec-level
or listener claim follows from these estimator-only diagnostics. The tests are
simulated and offline; they do not qualify a radio change.

## Score gain metadata correction and temporal-smoothing rerun (2026-11-28)

Reviewing the shared objective-score helper found that it passed signed PCM16
integers to `normalize_for_audition`, which expects normalized floating-point
audio. This made the stored `level_match_gain_db` field read about 90.309dB
too low. In normal operating cases the resulting normalized comparison audio
was effectively the intended level-matched waveform; when the implied gain
exceeded the helper's 1.8x cap, it could exceed that cap. The helper now takes
the decoded float signal directly. Existing reports are retained unchanged;
their `level_match_gain_db` values should not be interpreted as real gain.

To check whether this affected the temporal-mask conclusion, I reran the full
eight-voice, three-seed, 12-setting Wiener scale/floor/smoothing sweep with the
corrected helper. The ESTOI mean for every setting changed by at most 0.00001
at report precision (largest case-level change 0.00008); PESQ-NB changed by at
most 0.0006. Candidate ordering stayed the same: static Wiener 2.5x/floor .03
remains first, while stronger temporal smoothing still lowers ESTOI. Corrected
report: `recovery/gp0_raw/gp0-wiener-temporal-smoothing-sweep-corrected-20261128.json`.
Reproduce with:

```sh
PYTHONPATH=tools:/storage5/dev/rfsim-k6-gp0-20261127 \
  /tmp/gp0-fidelity-review-venv/bin/python \
  tools/k6_gp0_wiener_parameter_sweep_v38.py \
  --json-out recovery/gp0_raw/gp0-wiener-temporal-smoothing-sweep-corrected-20261128.json \
  --noise-scales 2 2.5 --mask-floors 0.03 0.05 --include-smoothed \
  --time-previous-weights 0 0.3 0.6 0.9
```

This validates the temporal-smoothing result against the helper correction;
it does not retroactively validate every other metric report's small
level-normalization differences. Main readability metrics and the human WAV
pack generation path remain distinct from this gain-metadata field.

The other sweep with gains near or above the 1.8x cap was the high-scale
Wiener screen. I reran all 16 scale/floor settings with the corrected scorer.
Its ranking is unchanged: scale 3/floor .01 leads that matrix at ESTOI .33320
and PESQ-NB 1.5259, with improvement over noisy audio in 24/24 cases. This is
still marginally below the separate neighborhood screen's ESTOI .33346 for
scale 2.5/floor .03. Only eight case-level PESQ values changed, by at most
.0062; ESTOI changed by at most .00011. Corrected report:
`recovery/gp0_raw/gp0-wiener-high-scale-sweep-corrected-20261128.json`.
Reproduce with:

```sh
PYTHONPATH=tools:/storage5/dev/rfsim-k6-gp0-20261127 \
  /tmp/gp0-fidelity-review-venv/bin/python \
  tools/k6_gp0_wiener_parameter_sweep_v38.py \
  --json-out recovery/gp0_raw/gp0-wiener-high-scale-sweep-corrected-20261128.json \
  --noise-scales 2.5 3 4 5 --mask-floors 0.01 0.02 0.05 0.1
```

## Human listening scores: step-down tracker pack (2026-11-28)

The listener's exported scores for `listening-tracker-stepdown-v1` were
unblinded with `recovery/gp0_raw/listening-tracker-stepdown-v1/answer-key.json`
and summarized in `recovery/gp0_raw/listening-tracker-stepdown-v1/scores-summary.json`.
The pack used two utterances, one listener, the simulated `step_down_6db`
profile, and -1 dB global input SNR. Intelligibility is a rank where 1 is best.

| Treatment | Mean intelligibility rank | Per-utterance ranks |
| --- | ---: | --- |
| Clean codec anchor | 1.0 | 1, 1 |
| Fixed Wiener 2.5/.03 | 2.5 | 3, 2 |
| Causal tracker q=.20 | 3.0 | 2, 4 |
| Causal tracker q=.35 | 3.5 | 4, 3 |
| Noisy codec | 5.0 | 5, 5 |

On these two clips the fixed Wiener treatment ranked ahead of the noisy codec
both times and ahead of q=.35 both times. Its comparison with q=.20 split by
utterance: q=.20 ranked better on the first, while fixed Wiener ranked better
on the second. This gives a small human-listening signal in favor of fixed
Wiener over the unprocessed noisy codec and against q=.35 for this step-down
case, but does not establish a general preference or a radio benefit. Repeated
clips are not independent listeners, the clean anchor is not a radio recording,
and no effort/naturalness ratings were supplied. Keep fixed Wiener as an offline
candidate for follow-up listening; it is not a firmware recommendation or
radio-ready change.

## Expanded blind listening pack: Wiener neighborhood (2026-11-28)

To follow up the two-utterance step-down result, I generated
`recovery/gp0_raw/listening-wiener-neighborhood-v1/`, an eight-voice blind set
with six level-matched treatments per voice: clean-codec anchor, untreated
modeled GP0 noise, fixed Wiener 2.5/.03, nearby Wiener 3/.01 and 2/.05
settings, and oversubtraction 1.5. Each voice has an independently randomized
label mapping. The page uses a newly generated local-storage key so ratings
from prior packs cannot appear as prefilled scores. The answer key retains the
mapping and hashes; the page does not load it. All 48 WAVs and the score page
were generated, and the key confirms eight voices with six treatments each.

This pack uses the stationary banded noise model at the study's 0.1Vpp source
convention. It is a broader human comparison of the current Wiener candidate
and nearby settings, not a step-down replication, physical radio capture, or
firmware qualification. The label randomization and fresh score key prevent
carry-over from earlier browser ratings, but do not resolve the source model's
limitations.

## Held-out fixed-FIR screen (2026-11-28)

The 31-tap fixed spectral-mask screen completed on four held-out voices, three
noise seeds each (12 cases), using the RFSim GP0 measured-response source,
0.1 Vpp input convention, and -1 dB modeled SNR. The best FIR by mean ESTOI
was scale 1.5/floor .1 at .27799; its mean PESQ-NB was 1.5261. Fixed STFT
Wiener scored .31300 ESTOI and 1.4985 PESQ-NB. The FIR's PESQ advantage is
small and comes with lower intelligibility proxy; no fixed FIR setting matched
Wiener on ESTOI. The weakest FIR settings were close to untreated audio.

This reduces the case for replacing the STFT candidate with a fixed short FIR
merely to simplify embedded execution. The next meaningful path is to evaluate
the human scores for the already-generated Wiener-neighborhood pack, then
prototype a bounded, fixed-point Wiener implementation and measure its actual
M0 cycle/RAM costs before including any denoising in the radio image. The sweep
does not use a physical GP0 noise capture and provides no K6 timing or RF proof;
it is an offline candidate-ranking result only. Full per-case values are in
`recovery/gp0_raw/fir-wiener-sweep-v1/report.json`.

## Causal Wiener frame-size screen (2026-11-28)

To identify a lower-latency embedded target, I compared 64-, 128-, and
256-sample causal STFT frames at 8 kHz, with quarter-frame hops, a 500 ms
independent noise preamble, Wiener scale 2.5/floor .03, and the actual Sotto
v3.8 codec. On the same four held-out voices and three banded-noise seeds
(12 cases, 0.1 Vpp source convention), mean ESTOI was .29365 for 64 samples,
.29111 for 128, and .30542 for 256, versus .22939 untreated. Every frame size
beat untreated audio in all 12 cases. The nominal frame bounds are 8, 16, and
32 ms respectively. PESQ-NB means were 1.4790, 1.4691, and 1.5088.

The 64-sample option is a useful latency/quality point for an embedded
prototype: its mean ESTOI is .01177 below the 256-sample version but remains
.06426 above untreated audio in this modeled set. This is objective-model
evidence only. The present implementation uses host NumPy FFT and overlap-add;
no K6 fixed-point cycle, stack, memory, streaming, or listener result has been
established. Results: `recovery/gp0_raw/wiener-framesize-study-20261128.json`;
reproduction script: `tools/k6_gp0_wiener_framesize_study.py`.

## Embedded FIR15 comparison module (2026-11-28)

The held-out FIR scale 1.5/floor .1 design was regenerated at 15 taps for a
small module experiment. It reached mean ESTOI .28246 and PESQ-NB 1.4807 on the
same 12 held-out model cases, beating untreated audio in all 12. A Q15
streaming C implementation matched the offline coefficient reference to 0 LSB
over 800 samples across 80-sample block boundaries.

The compiled Cortex-M0 module ran the FIR before Sotto encoding and completed
all four virtual microphone profiles at modeled 2.5 CPI: all 8 sessions (four
profiles x one load) arrived with valid packets and all 8,293 microphone
samples. At modeled 3.0 CPI, voiced multitone, chirp, and burst profiles lost
PCM; silence passed. This establishes a useful integration gate, not calibrated
K6 timing. The module uses the experimental `ram_micro` codec workspace reuse
and has only 58 bytes of modeled TX scratch headroom. Its FIR has DC gain .610,
so its coded audio may be quieter; the offline scores were level-matched.

I assembled a separate paired TX/RX test package at
`recovery/sotto_onair_test_candidate_wiener_fir15_20261128/`. The original
candidate remains intact. The package is not signed, flashed, or physically
qualified. It also has no human listening scores for the FIR treatment; the
active Wiener-neighborhood listening pack remains the next perceptual gate.
The radio returned no live response and no legacy SPI backup response during
this turn, so no installation was attempted. Full FIR sweep and virtual module
reports are included in the package evidence directory.

## FIR15 module loaded on the K6 (2026-11-27)

After the radio was connected, the legacy cable session returned a 2 MiB SPI
backup. The backup contains the exact candidate Sotto 1200 K6CB codebook and
K6SC IVF deck. The status inventory reports only the separate 600/400 codebook
missing. The current firmware advertises API 30 but not the resident `sotto`
capability; the capability-gated attempt correctly stopped before writing.

The `SOTTO FIR` module was then loaded to existing slot 24 with the legacy
cable loader and verified by read-back. The prior `SOTTO GP0` image was replaced;
all other library slots were left untouched. `SOTTO RX` remains in slot 28.
The core firmware was not replaced. Final inventory lists `SOTTO FIR`, 9,970 B,
serial 1795842280, and `SOTTO RX`, 10,838 B. No module launch, PTT, transmission,
RF, or intelligibility test was performed. The full pre-install backup and
radio-specific evidence are recorded under `recovery/k6_backups/` and the
candidate package's `evidence/radio-load-20261127.json`.
