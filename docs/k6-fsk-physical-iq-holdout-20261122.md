# Flux FSK-24 — held-out complex-IQ measurement on four archived real RF frames

Date: 2026-11-22 local / 2026-11-23 UTC
Branch: `research/native-fsk-pdm`
Status: **offline analysis of prior physical RF recordings; no new TX or flash**

## Question and measurement contract

A native 2-FSK stream has already carried 300-symbol/s PDM eight-level
**information** with 0/1,792 held-out chip decision errors. Does the
same transmitted signal actually reproduce a reference **complex-IQ
waveform** over a full frame, with phase coherence held through a data
payload not used for fitting?

**Result: No, not at arbitrary-waveform fidelity.** The ideal binary-FSK
complex target differs significantly from the physical received RF,
particularly on a long held-out segment. This does **not** contradict
successful binary-bit decoding. Physical operation of simultaneous
FSK+APC amplitude modulation remains **unproven**; the saved samples
here came from FSK-only transmissions.

## New tools

- `tools/k6_polar_known_target.py` now accepts both the original native
  2-FSK lab manifest and the separately checksum-verified 8FSK-PDM300
  native-bit manifest, plus `--amplitude-pattern constant` (required to
  model FSK-only actual recordings).
- `tools/k6_pdm_iq_archive_score.py` validates archived capture metadata
  (RF center, sample rate, one shot, 64-word completion, RX restoration)
  and source hash; acquires native-bit timing from the **known initial
  320-bit pilot** with the already-tested `k6_fsk_pdm_chip_decode`;
  downconverts saved 1.024-MS/s U8 I/Q via +280,500 Hz to 128 ksample/s,
  resamples to 96 ksample/s and extracts an RF burst with 2-ms pre-roll.
  It then uses the independent target and `k6_iq_holdout_verify` to
  fit **only pilot** integer time lag, one CFO and complex gain. These
  are then frozen for the remaining 704 FIFO bits. The tool does not
  control an SDR or radio.
- `tools/k6_iq_holdout_verify.py` now provides four frozen-calibration
  held-out EVM segments and a *posthoc* phase-error slope to help diagnose
  error growth. **Posthoc slope is NOT subtracted from held-out error.**

The mathematical reference assumes ±1,205.23 Hz instantaneous binary
frequency states from an **independently documented earlier hardware
deviation hypothesis**, constant ideal envelope, no added preamble or
framing, no RFIC filter/memory/PLL settling, no PA distortion, no
receiver filtering and no frequency drift. This is a *strict ideal
comparison*, not a calibrated modem waveform specification.

Pilot = initial 320 bits = 6,400 target samples / 66.667 ms;
complex-IQ holdout = remaining 704 bits = 14,080 samples / 146.667 ms.
The earlier symbol decoder also trained on 16 *additional* PDM symbols;
its final independently held-out **448 binary chip decisions** are
therefore not the same sample subset as this new 704-bit IQ holdout.

## Measured real-FSK-only comparison

| Archived IQ | Pilot normalized complex EVM | 704-bit held-out normalized complex EVM | Held-out coherent correlation | Frozen-pilot CFO fit | Posthoc held-out phase-trend slope |
|---|---:|---:|---:|---:|---:|
| original `native-fsk-pdm300-slot30.npy` | 0.2781 | **1.2094** | 0.5654 | +47.8 Hz | −10.22 Hz |
| `pdm300-repeat-01.npy` | 0.2778 | **1.0708** | 0.6296 | +42.0 Hz | −9.44 Hz |
| `pdm300-repeat-02.npy` | 0.2894 | **1.1050** | 0.5678 | +24.1 Hz | −9.74 Hz |
| `pdm300-repeat-03.npy` | 0.3035 | **1.7108** | 0.5701 | +18.5 Hz | +2.62 Hz |

EVM uses *one complex gain determined on the pilot* and a fixed
independent ideal reference. A value above 1.0 is legitimate: it
means the measured, pilot-gain-normalized complex residual exceeds
the ideal reference's RMS vector amplitude.

The posthoc phase-error slope is estimated from **held-out data for
diagnosis only**. It combines the physical source, receive oscillator,
DSP, alignment and mismatch to the ideal instantaneous binary
frequency model. It is not independent RF carrier calibration. The
first three runs consistently show ~−10 Hz held-out phase trend;
repeat 03 is a larger outlier even without a similar posthoc trend.
This supports investigating a time-varying phase-response model,
not simply adjusting CFO after seeing the held-out data.

The four held-out quarter-window EVM values (all **with unchanged
pilot gain/CFO/timing**) are:

| Run | Q1 | Q2 | Q3 | Q4 |
|---|---:|---:|---:|---:|
| Original | 1.046 | 1.113 | 1.032 | 1.566 |
| Repeat 01 | 1.033 | 1.120 | 0.828 | 1.257 |
| Repeat 02 | 1.011 | 1.211 | 0.737 | 1.361 |
| Repeat 03 | 1.409 | 1.720 | 1.762 | 1.913 |

A fixed binary instantaneous-FSK synthesizer alone is therefore not
validated for arbitrary **phase-coherent** RF waveform emission. The
previous [physical native-chip phase FIR analysis]
(k6-fsk-pdm-repeats-and-phase-20261122.md) demonstrates that adding
source/receiver finite-memory terms improves held-out **chip-boundary
phase coherence** to ~0.64–0.83 in the first three runs (but not
repeat 03). That is valuable evidence for a physical-channel response
model, not an already achieved full-sample IQ backend.

## Reproduce (reads historical data, cannot transmit)

```sh
cd /storage5/dev/flux-native-fsk-pdm
python3 tools/k6_pdm_iq_archive_score.py \
  --manifest docs/k6-fsk-pdm300-vector.json \
  --iq \
    /home/tyler/flux-bench/tx-injection-20261122/native-fsk-pdm300-slot30.npy \
    /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-01.npy \
    /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-02.npy \
    /home/tyler/flux-bench/tx-injection-20261122/phase-repeat/pdm300-repeat-03.npy \
  --out /tmp/flux-physical-fsk-complex-holdout-recheck.json

python3 -m pytest -q \
  tests/test_k6_pdm_iq_archive_score.py \
  tests/test_k6_iq_holdout_verify.py \
  tests/test_k6_polar_known_target.py
```

Physical IQ archive and saved V2 scoring report (not checked into Git):

`~/flux-bench/tx-injection-20261122/phase-repeat/physical-iq-heldout-v2.json`

All archived captures retain uncalibrated, air-coupled receiver status.
Their metadata supports the previous dummy-load transaction but does
not cryptographically attest the exact transmitted FIFO payload,
calibrated RF output power, oscillator offsets or out-of-band emissions.
Pilot matching and the original module-envelope fingerprint provide
additional contextual evidence, not absolute provenance.

## What to test next

1. Identify frequency impulse response and phase drift **on training
   data only**, then validate a *predictive* modeled IQ output across
   the withheld 448 native chips. Never report model-fit error as the
   waveform's fidelity to the original independently desired signal.
2. Diagnose repeat 03 with pilot-clock residuals, receive SDR phase
   noise, frame-start uncertainty and phase-response changes. Avoid
   choosing a correction based on heldout labels.
3. Define a backend capability profile distinguishing **digital data
   decodability**, **frequency trajectory approximation**, and
   **coherent complex waveform EVM**. The latter fails this initial
   ideal-reference test.
4. Keep separate paths (MICIN flat audio/modulator injection, native
   multilevel modem, tone oscillator, TCXO, subaudio, PA/APC, external
   IQ stage) alive; see [TX-10..TX-23 roadmap](k6-tx-next-experiments.md).
5. Genuine joint FSK+APC would still require a fresh, safety-gated,
   separately documented physical experiment. This analysis neither
   authorizes nor performs it.

**Important:** high held-out EVM is a finding, not proof the transmitted
RF was physically discontinuous, or that all shaped FSK waveforms must
fail. Hardware and uncalibrated receiver contributions remain mixed.
