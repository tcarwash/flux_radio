# GP0 microphone noise: physical PCM fingerprint, modeled filters, and receiver previews

**Date:** 2026-11-26 Pacific, about 22:39–22:44. Branch `experiment/gp0-noise-offline-20261126`, worktree `/storage5/dev/flux-gp0-noise-offline-20261126`, based on the previous `3b7aaa27` clock-correction investigation.

**Status: OFFLINE_ONLY / HOLD_PHYSICAL_UNTIL_OPERATOR_AVAILABLE TOMORROW (Nov 27).** No serial reads, firmware flashes, module installs, SDR receiver sessions, PTT, radio transmissions, or bench reservations were performed in this noise investigation. The user's radio may be unattended; leave it and all working firmware/diagnostics untouched. Private signed radio contents, original RF IQ and WAVs remain in the prior worktree and original home-directory paths. All new audio files are in a new local `flux-bench` output folder, not in Git.

## 1. Verified raw audio evidence, before the vocoder

`tools/k6_gp0_noise_fingerprint.py` rechecks the **original CRC-validated packet payloads**: full 67/67 sender sessions from signed16 WAVs whose SHA256 is verified against the RF manifest, plus the 36/67 generator-OFF and 20/67 second-500Hz sessions read directly from 28MB post-tune RTL-SDR IQ. Both partial captures must have consecutive seq `0..N`, same session identity, and valid D9 CRCs. **No missing packets, samples, or silence are synthesized.** Signal/time axes use the measured 1600-sample capture duration in 100µs MCU ticks (~8,529–8,533 PCM samples per physical second), not the nominal 8kHz WAV header.

| Input stimulus | CRC-verified samples | Unfiltered residual RMS/full-scale | Lag-1 residual autocorrelation | Physical corrected generator frequency |
|---|---:|---:|---:|---:|
| Generator OFF | 864 | **0.04377** (−27.18 dBFS) | +0.008 | — |
| First 500Hz take, no reliable tone | 1,600 | **0.04463** | +0.031 | not fitted |
| Second 500Hz take, valid tone | 480 | **0.04578** | −0.040 | **499.91Hz** (sinusoid fit) |
| 1000Hz take, valid tone | 1,600 | **0.04499** | +0.001 | **999.46Hz** (sinusoid fit) |

The complete four-run RMS spread is **0.39dB**. Residuals are roughly broadband and adjacent samples are nearly uncorrelated. Approximate 0–300 / 300–800 / 800–1500 / 1500–2500 / 2500–3500 / 3500–4000Hz fractions are stable across runs. 1k tone residual fractions: **8.5% / 11.5% / 15.0% / 26.6% / 26.3% / 12.1%**. These are **Welch descriptive estimates** over short 56–188ms windows, not stable, high-resolution noise-PSD measurements. The 500Hz take2 has only ~56ms; do not overinterpret isolated spectral peaks.

Neither valid tone clips. The first three fitted harmonics yield **about −40.5dB (500Hz partial) and −42.0dB (1000Hz) apparent harmonic-to-fundamental ratios**, but these are **noise-floor limited**. The 480-sample/0.045-RMS noise record can itself create harmonic-bin fits on the order of 0.003 full-scale amplitude, comparable to the observed tiny harmonics. Therefore **no claim of measured analog THD at −40dB** is justified. What we can say is that harmonic distortion is **not the dominant visible residual**, and the persistent noise is present well ahead of Sotto encoding.

At **0.1Vpp** generator input, a good tone produces approximately **0.41 full-scale peak** (0.291 full-scale RMS). The pre-vocoder residual of about 0.045 full-scale RMS gives only **~16.2dB signal-to-residual** even for a strong signal. The equivalent input-referred amplitude at this *one test gain* is nominally **~5.5mVrms**, computed from `0.1Vpp = 0.05Vpeak` and full-scale gain 0.41/0.05 = 8.2 full-scale/Vpeak. This is *not* evidence of 5.5mVrms physical microphone analog noise: decimation aliases and GPIO/ADC quantization can produce the same output noise, and the mic input/output transfer may be nonlinear at other levels.

**Interpretation:** noise is approximately stimulus-independent and broad-spectrum in current recordings, so the most fruitful next hypothesis is **pre-decimation count-stream noise and aliasing**, not a bad 500Hz frequency response, clipping, severe signal-dependent nonlinearity, or a narrow 50/60Hz hum. It could *also* originate as broadband RFIC input/analog noise or digital acquisition artifacts; current 8k PCM cannot identify one root cause.

Reproduce metrics only (reads private original captures, creates new JSON; no radio activity):

```bash
cd /storage5/dev/flux-gp0-noise-offline-20261126
/home/tyler/.local/share/flux-rf-venv/bin/python tools/k6_gp0_noise_fingerprint.py \
  --source-root /storage5/dev/flux-sotto-gp0-pcm-telemetry-20261126/recovery/pcm_air \
  --json-out /home/tyler/flux-bench/gp0-noise-lab-20261126/fingerprint-another-run.json
```

Checked-in redacted derived data: [`docs/evidence/sotto-gp0-raw-noise-fingerprint-20261126.json`](evidence/sotto-gp0-raw-noise-fingerprint-20261126.json).

## 2. Actual Rust pre-decimation filter modeling — conditional, not physical

`tools/k6_gp0_noise_filter_model.py` extracts and compiles the **same Rust `Decimator` and `dec_push` functions used by the real firmware** from `firmware/k6-aura/sotto-api30-gp0-capture.rs`. It builds temporary native executables for the default boxcar, AA3 (three high-rate IIR poles), original FIR11 and faster FIR11_LATE, using the empirically justified `group_weight=7500` and fast 87,260 denominator divider. Synthetic *count-of-64-bit* streams enter at the inferred ~93,050 counts/s, producing ~7,998 PCM samples/s. Three seeded source cases vary only the **high-rate noise spectrum**: white independent counts, highpass first difference, and an input with most noise below 2.5kHz. These are mathematical sensitivity cases, **not recorded PDM/GPIO samples**, and not source-accurate sigma-delta modulation.

| Conditional model | AA3 gain vs baseline | FIR11_LATE gain vs baseline | What it means |
|---|---:|---:|---|
| White high-rate popcount noise | **+2.13dB** SNR | **+1.30dB** SNR | Modest improvement only |
| Highpass high-rate popcount noise | +5.11dB | +3.87dB | Stronger if upstream noise is predominantly above band |
| Input noise mostly below 2.5kHz | +0.54dB | +0.25dB | Cannot remove in-band noise without losing speech |

The **white high-rate** model best reproduces the measured *shape* of the six 8kHz spectrum bands and near-zero adjacent-sample correlation. Its synthetic output noise RMS 0.0497 resembles measured ~0.045 without a complex fit. Under the same input variance, the model's highpass and in-band cases have noticeably different post-decimation spectra and lag1 correlations (~−0.277 and +0.454, versus physical ≈0.0). These are **qualitative model comparisons**, not formal Bayesian source identification: different physical analog or sigma-delta noise processes could yield the same low-rate output.

Tone passband loss versus original boxcar, from these *synthetic actual-Rust decimations*:

| Tone | AA3 | FIR11_LATE |
|---|---:|---:|
| 500Hz | −0.20dB | −0.05dB |
| 1000Hz | −0.70dB | −0.20dB |
| 2000Hz | −2.58dB | −0.81dB |
| 2800Hz | **−4.64dB** | **−1.61dB** |
| 3200Hz | −5.78dB | −2.14dB |

**Recommendation for tomorrow's source-side investigation:** do **not** immediately choose AA3 just because it suppresses noise more. At 2.8–3.2kHz it attenuates important consonant energy 4–6dB, while its white-noise improvement is ~2dB. FIR11_LATE is a better *speech-preservation candidate*, but improves white noise only ~1dB, uses more persistent filter state than the currently measured 24B RAM0 margin can comfortably absorb, and still needs physical DMA-IRQ timing qualification. The official firmware build gate **forbids** mixing either filter with the experimental 7500 clock fix. Only standalone Rust model binaries combined flags here, without a new firmware image. The previously noted CIC3 alias risks also make indiscriminate decimator swapping unwise.

Checked-in conditional model data: [`docs/evidence/sotto-gp0-noise-rust-filter-conditions-20261126.json`](evidence/sotto-gp0-noise-rust-filter-conditions-20261126.json).

## 3. Reversible decoded-Sotto voice listening previews

The private `sotto-received(5).wav` is a **2.24s/17,920-sample/8kHz decoded Sotto RX file**, not pre-vocoder PCM. It contains nonuniform but persistent audio with overall RMS about 0.0619 full scale. Roughly **26.75% of estimated spectral power resides at 3–4kHz**; much of it might be unwanted noise, but high-frequency speech consonants are also possible. We cannot claim intelligibility improvement without clean speech reference or listening.

`tools/k6_sotto_voice_noise_preview.py` creates three distinct **new receiver-output-only** WAVs: a conservative 200–3300Hz third-order zero-phase voice bandpass, that bandpass plus mild time/frequency-smoothed spectral attenuation, and the bandpass plus stronger spectral attenuation. It uses a low temporal quantile per frequency to estimate an imperfect stationary noise proxy. The output's noncausal zero-phase filters and FFT processing **are not suitable as-is for a real-time K6 Cortex-M0 mic ISR**. All versions have equal source duration, remain unclipped, and cannot touch radio state.

The preferred subjective A/B files are **level-matched to the original RMS**, so differences are not confounded by quieter outputs. Their measured 3–4kHz power fractions are approximately 6.04%, 5.73%, and 5.66%, versus 26.75% in the untouched decoder output. **This is only removed high-frequency energy, not a measured amount of removed noise or evidence of clarity.**

Local source (original, unchanged):

`/home/tyler/snap/firefox/common/sotto-received(5).wav`

Listening outputs (private, untracked):

`/home/tyler/flux-bench/gp0-noise-lab-20261126/voice-sotto-5-level-matched/{bandpass,soft,strong}-preview.wav`

Reproduce to a new empty output directory:

```bash
cd /storage5/dev/flux-gp0-noise-offline-20261126
mkdir -p /home/tyler/flux-bench/gp0-noise-lab-20261126/voice-another-run
/home/tyler/.local/share/flux-rf-venv/bin/python tools/k6_sotto_voice_noise_preview.py \
  --input-wav '/home/tyler/snap/firefox/common/sotto-received(5).wav' \
  --output-dir /home/tyler/flux-bench/gp0-noise-lab-20261126/voice-another-run \
  --level-match-rms
```

## 4. Highest-value next acquisition: RF-dump **high-rate popcount groups**, not merely PCM

The existing GP0 PCM AIR radio diagnostic successfully transfers 67 × CRC-checked packet payloads over authorized RF, with no live UART required. It only supplies **already-decimated PCM**. Therefore the fundamental question *where the ~0.045 full-scale noise originates* cannot be settled from our existing post-decimation WAVs.

Design for a **future (not yet installed) small diagnostic**, reusing the bounded receiver and RF dump transport:

1. In a radio-attended setup, capture a **fixed 2,048 or 4,096 consecutive 8-bit popcounts** (each exactly 0..64) at the existing `gp0_bits_64()` ISR boundary, **before** any AA/FIR or fractional resampling. The underlying GPIO bitstream is sampled at 64 × group rate; storing one byte per 64-bit group records *all the information the present Rust decimator actually consumes*, though not the original 64-bit temporal pattern. Approximate capture window: **22ms or 44ms** at inferred 93.0k groups/s, and 2–4KiB payload. Store in the module arena *only with a verified, collision-free temporary allocation and stop DMA before RF output*; preserve all protected RAM0/stack contracts. A firmware API extension might be needed; don't assume a module has arbitrary access to DMA ISR internals.
2. Include actual group-count/timebase and fault counters, capture session/sequence, total-byte count, version, CRC, and transfer-level integrity. Packet receiver rejects mismatched sessions, conflicting duplicates and holes. Never fill missing data; the open-loop RF transport may require multiple human-supervised attempts. Keep all raw captures private; never send SPI secrets/keys/calibration dumps over radio.
3. Replay **the real count stream** offline through the pinned Rust decimator with the calibrated 7500/87260 ratio, baseline, AA3, FIR11_LATE, and other small filters. Compare white-vs-shaped noise spectra, actual alias folding, 500/1k sine transfer, DSP clipping, and voice-band SNR, **without repeated MCU flashes**. Before any hardware, write strict tests for the diagnostic producer and independent packet parser.
4. Tomorrow, only when the operator is back: confirm cable/dummy load/battery, verify preserved SPI backup, and perform **attended** diagnostic checks as appropriate. Optional first priority is a *complete* 67/67 generator-OFF PCM AIR run and a full, verified 500Hz repeat. The calibrated firmware still fails byte-identity with the known-good fielded image and is **not yet flash-approved**. Any new raw-count service would need its own full-linked ARM resource and watchdog gates.

**Noise goal:** determine whether broadband noise is (A) present in the GP0 count stream at voice frequencies, (B) predominantly out-of-band popcount noise folded in by the boxcar, (C) timing/GPIO DMA artifacts, or (D) post-PCM/codec distortion. Our current captures already establish that at least much of the noise precedes Sotto, but **do not distinguish A, B and C**.

## 5. Verification

The new scripts are read-only with explicit output paths, refusal to overwrite existing captures/previews, explicit private source paths, no UI or hardware control. Tests include synthetic tone fitting with correct physical clock, invalid/partial input rejection, count-stream reproducibility, real Rust filter compilation, and decoder-preview duration/RMS/clipping checks. Legacy GP0 clock correction and existing source-code gates remain unchanged. No MCU build gates were relaxed, and no combined filter/clock firmware was compiled or flashed.
