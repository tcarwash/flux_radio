# Flux TX-15U — predeclared independent validation of pilot audio-rate transfer

**Status:** This methodology was developed AFTER inspecting TX-15T
control and pulse runs 01–02. Those four physical IQ files are therefore
**training/development evidence, NOT blind prospective validation**.
This document and the new scoring code MUST be committed before
a separate independent run 03 control and run 03 pulse are emitted.

**Local design date:** 2026-11-22 Pacific / 2026-11-23 UTC.
**Branch:** `research/native-fsk-pdm`.
**No new RF transmit firmware:** TX-15T A/B compiled-image code,
exact source frequencies, 192 programmed writes, dummy-load RF
watchdog, 72 distributed pilots, 114 heldout words, and six
unscored terminal guard slots are unchanged. All controls in
`docs/k6-tx15t-phase-steering-prospective-20261122.md`
continue to apply.

## Why the new model is necessary

The original TX-15T first pair passed the pre-committed prospective
engineering test. An intentional B repeat passed as well.
But a second independent A control FAILED: the unchanged
tone program had only **0.306** complex RF coherence over the
active interval and an apparent **+79.3°** retained phase step.
The original test remains recorded as a failure; these thresholds
must NOT be altered retroactively.

RF amplitude remained strong and steady (no ADC clipping), all
114/114 heldout audio tones decoded, 95%+ high-tone RF-phase
segment coherence remained in the bad control, and same-word
recovered FM audio phase was near 0.4°. The inconsistent
complex-carrier model was worst during low **502-Hz**
audio-tone slots, where the FM modulation index is largest.

A diagnostic comparison of FM-discriminator *audio phase*
versus the source's accumulated nominal audio-oscillator
phase revealed a slow phase accumulation discrepancy.
Fitting one **effective common frequency offset** relative
to the SDR's audio timebase, using ONLY the known pilot
audio tone phases, greatly improved the same old
RF complex-phase scores. This is not a measured FD6818B
oscillator clock: TX/RX/filter/SDR biases are not isolated.

### Retrospective scores on known development files

| Old TX-15T capture | Previously committed correct source RF coherence | With pilot audio-rate correction | New engineering qualification |
|---|---:|---:|---|
| A control 01 | 0.702 | **0.938** | pass, post-hoc |
| B pulse 01 | 0.988 | 0.978 | pass, post-hoc |
| B pulse 02 | 0.661 | **0.964** | pass, post-hoc |
| A control 02 | 0.306 | **0.900** | pass, post-hoc |

The anomalous A control 02's apparent phase step changes
from **+79.3° to −6.8°** under the newly chosen model.
Effective pilot audio-rate corrections in chronological
order A01/B01/B02/A02 are approximately
**−0.143/+0.015/−0.181/−0.157 Hz**.
All are estimates relative to an uncalibrated independent
receiver and a simplified source phase model.

These results were directly involved in developing the
correction and **do not count as 4/4 independent successes**.
The original TX-15T locked score remains **three passes
and one failed control**.

## TX-15U prospective method, FROZEN before independent run 03

Tool: `tools/k6_tx15u_phase_steering_clock.py`.

1. Read saved raw RF complex IQ, mix and decimate
   identically to the TX-15T scorer.
2. From the four predeclared identical pilot windows,
   fit the piecewise symbol timebase using strong
   frequency transitions, **never from heldout edges**.
3. For each pilot tone, recover its phase at the
   start of each known source slot. Exclude startup
   symbols 0–2 and any phase-fit RMSE above 250 Hz.
4. Subtract the nominal integrated continuous
   audio-oscillator source phase; unwrap the
   residual ONLY separately within repeated
   appearances of the SAME tone. Center each
   tone's residual and sample time to remove
   a fixed unknown tone-dependent RX/TX phase
   offset. Combine all pilot tone groups with
   one ordinary least-squares regression to
   find **one additive effective oscillator
   rate correction Δf** (in Hz).
5. Require exactly six fixed source tone
   groups (0,1,2,5,6,7), at least eight
   qualified pilot words per tone, phase
   residual RMS no greater than **30°**,
   and **|Δf| ≤ 0.60 Hz**. Otherwise refuse
   to qualify the source; DO NOT optimize
   from nonpilot data or silently discard
   a capture.
6. Construct the original TX-15T continuous-FM
   RF carrier-phase basis with source frequency
   terms `freq + Δf` for BOTH A and B.
   The six-tone deviation amplitudes still
   come from pilot-only audio fits.
7. Apply the identical *original* TX-15T
   RF-IQ nuisance model: constant phase,
   relative RF carrier CFO, quadratic
   carrier phase (linear CFO drift), and
   two FM quadrature coefficients,
   fitted only on four pilot intervals.
   Apply the original last-six-pilot
   local RF CFO correction to both
   counterfactual source predictions.
8. Score the same predeclared active
   **177–185** source interval, clean
   early guard **187–190**, and late
   reference pilot **168–173**.
   The real RF phase is the phase of the
   measured COMPLEX RF-IQ samples, not
   the audio phase surrogate.
   The absolute phase of separate physical
   recordings is not compared.
9. Use the **UNCHANGED frozen TX-15T
   engineering thresholds**:
   - A: absolute A-relative retained guard
     phase ≤40°, and A coherence better
     than counterfactual B by ≥0.10.
   - B: pilot-predicted B-minus-A retained
     phase magnitude ≥35°; observed
     magnitude ≥25° with same sign,
     circular step prediction error ≤45°,
     and B coherence advantage ≥0.10.
   - A weak predicted B phase is a
     negative/inconclusive engineering result,
     not an excuse to fit nonpilot data.

The new rate correction is ONE common model
parameter estimated from pilot FM discriminator
phase only. This is a source-aware receiver
calibration, **not** a software API for
arbitrary absolute RF-carrier phase commands.

## Prospective protocol

- Freeze and commit this exact file,
  the analyzer and its regression tests.
- Continue using the same exact compiled
  TX-15T hardware code from
  `582fa53`:
  - variant A SHA256
    `de53fc01aacb6a5e63fbf5e1bc73ffcc0b53fe30d1b6d4822cc9ed28ee9f6989`
  - variant B SHA256
    `bbbe7b3bc47f651d9f7c5df2a4ff0be7c9069048fa6f3e2bc5b5a88624c7299b`.
- For each new single shot, verify live
  firmware `95d6b260d4122287`,
  chip ID `0x4829`, both
  VFOs 144.390 MHz idle, trusted slot-30
  original executable, dummy-load
  fixture and SDR baseline. Retain a
  separate original envelope per TX.
- Emit **new run 03 A control and new
  run 03 B phase pulse** at the
  previously operator-authorized
  conducted 433.500-MHz bench only.
- Run this exact new scorer on each
  raw RF-IQ recording. **Do not change**
  parameters or thresholds before
  interpreting those new scores.
- Document raw-data SHA256, exact
  firmware execution report,
  backup/restore readback, matched
  vs wrong-model coherence, signed
  predicted and measured RF phase
  steps, and the prospective result.
- Preserve the original locked
  TX-15T scoring files unchanged
  as a baseline and include the
  prior negative control in the
  published report.

If the fresh control and fresh active pulse
both pass, that would strengthen evidence
that the K6 can program a transient
RF phase displacement that persists after
the tone source rejoins, *conditional on
pilot calibration of the unknown oscillator
phase*. It still does not demonstrate an
absolute phase DAC, open-loop target phase
selection, arbitrary RF IQ, OFDM/QAM,
or measured transmitter spectrum.

Both TX-17 B-chip signed synthesizer
frequency-offset investigation and
TX-20 APC/polar modulation remain
independent next directions.
