# Flux TX-15T — predeclared active RF-carrier phase-steering A/B experiment

**Status:** SOURCE, SCORING AND QUALIFICATION FROZEN BEFORE ANY NEW
TX-15T PHYSICAL EMISSION. This is the intended prospective control
vs steering protocol for the K6 FD6818B / BK4829 B-chip.

**Local design date:** 2026-11-22 Pacific / 2026-11-23 UTC.
**Branch:** `research/native-fsk-pdm`.
**Goal:** test whether selecting a short, transient firmware-programmed
internal audio-oscillator frequency trajectory can leave a controlled
difference in the **received RF carrier's phase** after returning to the
*same* audio frequency/near-identical oscillator phase. This goes
beyond classifying FSK and beyond merely predicting the already
transmitted RF phase, but does **not** claim an arbitrary I/Q DAC.

## Predeclared control and active pulse

Two source programs A (control) and B (pulse) are fixed and differ in
**exactly SIX REG_71 tone-word writes**, indices **177–182**.
Everything before index 177, and everything at or after 183,
is identical. Every emission has 192 writes at nominal 150/s
(1.28 s) inside an 1800-ms TX firmware lease.

| Word indices | A control | B phase-pulse |
|---|---|---|
| 0–173 | exactly the published TX-15S program | **identical** |
| 174–176 | tone 4 (1693 Hz) | tone 4 (1693 Hz) |
| 177–179 | tone 0 (502 Hz) | **tone 1 (782 Hz)** |
| 180–182 | tone 7 (2519 Hz) | **tone 2 (1091 Hz)** |
| 183–185 | tone 0 (502 Hz) | tone 0 (502 Hz) |
| 186–191 | tone 0, final shutdown guard | **identical** |

Source frequency words are
`5183,8074,11264,14702,17479,20515,23467,26007`,
interpreted in the prior TX-15P/Q/R/S calibrated tone model.
The program has four **identical pilot windows**
`[0,18), [48,66), [96,114), [156,174)`, holding
72 total pilot slots. 114 other slots are heldout and
six terminal words `[186,192)` are explicitly unscored
for ordinary data decoding. The final symbol 191 is
known from prior TX-15S captures to be contaminated by PA
de-keying and is NEVER part of retained phase-step scoring.

The two changed blocks correspond to a 40-ms transient
FM-tone sequence. At the end, the model's *audio*
oscillator phase in B rejoins within approximately
**−4° to −6°** of A at the measured TX-15S
SDR-relative symbol update rates. This matters: a
remaining RF phase difference in the following common-tone
guard is then a candidate deliberately **retained
carrier-phase displacement**, rather than merely choosing
a different currently transmitted audio tone.

## Design-data leakage and target sign

The two candidate tone indices `(1,2)` were selected
**using three older TX-15S recordings** by searching
64 possible pairs of middle three-symbol tone blocks.
The first and last tone blocks were constrained to match
the control. The search preferred small audio oscillator
phase mismatch at the rejoin and a strong signed RF-phase
difference under each older recording's pilot-fitted FM
quadratures.

Earlier TX-15S carrier models predict a B-versus-A
guard RF-phase shift around **−57°, −128°, −133°**
after fitting their own pilots with the final scoring
algorithm. These are **design-data predictions**, NOT
independent proof and NOT a fixed target guaranteed
across new RF trials.

An uncontrolled initial audio-oscillator phase or a
small relative update-clock change can change the
pulse's RF-phase displacement, even reversing its sign.
Without live phase feedback, a fixed program cannot
promise a signed degree shift for every emission.
Therefore the **prospective test** uses the signed
target predicted from *that emission's PILOT IQ only*,
not an arbitrary post-hoc constant target or comparison
of absolute carrier phase between transmissions.

## Frozen receiver / scoring model

Tool: `tools/k6_tx15t_rf_steering.py` (READ ONLY).

1. Read saved raw RTL-SDR U8 I/Q at 1.024 MS/s; use
   the existing 280500-Hz receiver-frequency correction
   and decimate to 64 kS/s COMPLEX RF I/Q. The receiver
   is air-coupled, uncalibrated and independent.
2. Extract the known distributed pilot edges and
   learn the **piecewise-linear** RF symbol schedule
   using only those four disjoint pilot windows.
3. From pilot tone-slot FM-discriminator audio, learn
   six measured deviation amplitudes and interpolate
   two other tone deviations. All this is PILOT only.
4. Reconstruct two complete predicted continuous-
   phase source FM waveforms A and B. On the 72
   identical pilot symbols they produce **exactly
   identical** FM phase basis samples.
5. Learn one **common** 5-parameter complex RF phase
   model from the pilot complex IQ: constant RF phase,
   relative RF CFO, quadratic RF phase / linear
   relative CFO drift, and two oscillator FM
   quadrature coefficients.
6. The global fit leaves local drift near the end.
   Fit ONE additional residual RF CFO slope from
   only the **last SIX pilot symbols [168,174)**,
   then linearly extrapolate it after the
   start of that pilot block. Apply exactly the
   SAME correction to predictions A and B.
   Earlier TX-15S control run data were used
   to choose this pilot-only correction: prior
   A-only apparent guard steps without it were
   `−1.5°, −88.3°, −40.0°`; with the frozen
   last-six-pilot correction they become
   `−19.2°, +0.8°, +18.4°`.
   Those historical scores are **design work**, not
   prospective validation.
7. Freeze every parameter. Score the active
   **177–185** phase trajectory against both
   source hypotheses, plus the common-tone
   early guard **187–190**. Exclude 191.
   The guard RF phase step is measured relative
   to **late pilot anchor 168–173**, in the
   *same physical SDR recording*, after
   subtraction of the A-source pilot prediction.
   Predict B-minus-A guard phase from the
   same pilot-frozen quadrature model.
8. Compare circular coherence of correct and
   wrong source models using **the same heldout
   IQ samples**, no refitting for the
   counterfactual. Never compare absolute
   RF carrier phase between independent A and B
   emissions (unknown startup LO phase).

## Locked engineering qualification (per independent RF emission)

A/control must satisfy BOTH:
- Measured retained guard RF phase shift from its
  own A-source model is within **±40°** of 0.
- Correct-A complex RF trajectory coherence in
  active window 177–185 exceeds the
  counterfactual-B coherence by **at least 0.10**.

B/pulse must satisfy ALL:
- Pilot-predicted absolute B-versus-A retained
  guard phase shift **at least 35°**.
- Observed A-relative retained guard phase
  shift magnitude **at least 25°** and
  same SIGN as pilot prediction.
- Signed phase-step error (circular, observed
  versus pilot-predicted) within **±45°**.
- Correct-B RF trajectory coherence in active
  window 177–185 exceeds counterfactual-A by
  **at least 0.10**.

These are source-locked engineering thresholds,
not external emission-mask or RF phase DAC
qualification. If pilot-predicted phase is
smaller than 35°, mark that emission
**inconclusive/negative for phase-step goal**.
**Do not reclassify a weak result as positive**.
Do not adjust the model or thresholds using
new TX-15T RF captures; any later model
revisions require new independent trials.

A successful A/B pair would show that REG_71
can program a distinct, source-predicted
RF-carrier phase evolution with a significant
retained phase change after the audio-tone
frequency and (approximately) oscillator phase
have rejoined. It would NOT establish
arbitrary RF phase-state selection without
feedback, useful spectral containment,
quadrature amplitude shaping, QAM/OFDM,
or arbitrary RF-IQ.

## Safety: exact-source, bounded single-shot only

- The radio is connected by the existing shared
  serial `/dev/ttyUSB0`, emission to the
  previously confirmed dummy-load fixture, with
  air-coupled RTL-SDR capture. No off-load
  RF transmission or on-air identification.
- Both fully compiled module executables are
  **exact SHA256 pinned**, not just source
  macro strings. Firmware must match build
  `95d6b260d4122287`, B-chip ID `0x4829`,
  idle radio at both VFOs 144.390 MHz,
  known original signed slot 30 image and
  occupancy/receiver baseline thresholds.
- A control source wrapper (outside Git):
  `~/flux-bench/tx-injection-20261122/tone-oscillator/tx15t-phase-steer-0-enabled.c`
  compiled-code SHA256
  `de53fc01aacb6a5e63fbf5e1bc73ffcc0b53fe30d1b6d4822cc9ed28ee9f6989`.
- B pulse wrapper (outside Git):
  `~/flux-bench/tx-injection-20261122/tone-oscillator/tx15t-phase-steer-1-enabled.c`
  compiled-code SHA256
  `bbbe7b3bc47f651d9f7c5df2a4ff0be7c9069048fa6f3e2bc5b5a88624c7299b`.
- Use `tools/k6_tx15t_rf_bench.py` with an
  explicit matching `--variant 0` or
  `--variant 1`, guarded fixture and TX
  watchdog source macros, and a one-time
  `--dummy-load-confirmed` parameter.
  The bench never auto-retries; it saves
  each experiment's original slot envelope
  and exact SDR IQ, and restores the
  original module/VFO with verified readback.
- The standalone `tests/test_k6_tx15t_steering.py`
  checks exact source differences, fixed
  pilot and guard, endpoint audio-phase
  convergence, synthetic RF phase positive
  and negative controls, source-identity
  and no-heldout-training guarantees,
  Cortex-M0 emulation on PA-OFF and RF-ON
  guarded versions, and compile-time
  RF and variant rejection. Optional tests
  will read raw physical data **only
  after** the capture exists.

**Before new physical RF:** run these tests,
commit this exact protocol and the
implementation, and verify existing TX15S
compiled source SHA unchanged. After that,
run a separate A control and B pulse,
each with its own verified restore,
then apply this *unchanged* scorer to
each saved IQ. Keep TX-17 signed
synthesizer latch and all other alternative
TX paths in the main roadmap.
