# Flux TX-15S — distributed pilot and pilot-frozen complex RF carrier-phase prediction

**Experiment:** physical K6/FD6818B B-chip, serial-controlled REG_70/71
tone oscillator, RF into operator-confirmed dummy load, monitored by
air-coupled RTL-SDR at 1.024 MS/s. **No MCU firmware flash.**

**Local dates:** 2026-11-22 Pacific time (runs saved 2026-11-23 UTC).
**Branch:** `research/native-fsk-pdm`. The TX-15S physical waveform
is distinct from TX-15P (64 events) and TX-15R (192 events with an
initial-only pilot). The previous TX-15R and every other TX control
path remain intact.

## Main finding (three real emissions)

A *predeclared distributed-pilot* K6 source waveform was emitted three
independent times. Four pilot windows anchor the tone-update clock
over the whole 1.28-s RF burst. This let us recover and predict not
only the FM-audio phase trajectory, but a significant component of
the **actual transmitted complex-RF carrier phase trajectory** as
received by the RTL-SDR, with model calibration performed on pilot
IQ samples only.

**This is a *specific constant-envelope FM waveform*, not a
general-purpose arbitrary RF-IQ transmitter or demonstration of
independent amplitude/phase DACs.** Received IQ phase is relative to
an uncalibrated receiver oscillator, and the unknown transmitter
and receiver response is not separated.

All three 192-event firmware reports returned
`tx8s n=192 ... late=0 worst=39`. Every transaction verified exact
temporary code/known original slot-30 executable, confirmed an
1800-ms bounded TX lease, saved the original slot envelope, restored
slot 30 with a fresh authenticated serial/readback, restored both
144.390-MHz VFOs and confirmed TX idle. No radio calibration changes
or main flash operations.

| Held-out result (all counts fixed before RF capture) | Run 01 | Run 02 | Run 03 |
|---|---:|---:|---:|
| Firmly completed tone writes | 192/192 | 192/192 | 192/192 |
| Pilot strong transitions (4 windows) | 20 | 20 | 20 |
| Heldout strong midpoint checks | 32 | 32 | 32 |
| Heldout audio tone IDs | **114/114** | **114/114** | **114/114** |
| Heldout strong-edge timing RMS (global clock) | 20.34 µs | 16.99 µs | 15.74 µs |
| Heldout strong-edge timing RMS (piecewise pilot clock) | **17.79 µs** | **15.72 µs** | **14.17 µs** |
| Unchanged-word AUDIO-phase RMS, 76 heldout edges | **0.429°** | **0.461°** | **0.441°** |
| Changed-word AUDIO-phase RMS, 34 heldout edges, piecewise/pilot transfer | **8.11°** | **5.36°** | **4.95°** |

**Important:** the phase errors above refer to recovered *audio
tone* phase in the FM discriminator. They are **not** the same
measurement as the complex-RF phase prediction below. The 6-word
terminal guard is excluded by design in all three trials rather
than retrospectively deleting problematic last symbols.
The guard worked as intended: the final unscored symbol's
recovered-audio sinusoid fit RMSE rose to approximately
**2400/2740/2614 Hz** in runs 01/02/03 while the
preceding five guard symbols remained near 50–60 Hz.
The 114 scored heldout symbols all decoded correctly.

## Experiment layout

- 192 REG_71 writes at nominal 150 updates/s, 8 noncommensurate
  audio tones: 502/782/1091/1424/1693/1987/2273/2519 Hz.
- Four known pilot windows, half-open symbol-index intervals:
  **[0,18), [48,66), [96,114), [156,174)**, each
  repeating the same 18-symbol motif of six different tones,
  each repeated three times. All 20 internal pilot strong
  tone-frequency changes are predeclared.
- **114** heldout tone symbols outside the four pilot windows
  and before the guard; **6** deliberately unscored terminal
  guard symbols at **[186,192)** (constant tone 0).
- Boundaries between pilot, heldout and guard are excluded from
  transition-phase training AND transition-phase scoring.
  This leaves **68 pilot-only adjacent edges**, **110 heldout-only
  adjacent edges**, **5 guard-only edges** and **8 cross-region
  boundary edges**.
- Global and piecewise symbol schedules are fitted from only
  the pilot edges. The piecewise model interpolates mean
  known-tone high-contrast edge times at the four pilot
  window centers and extrapolates linearly at the ends.
- Two (not one) SDR-relative timing clocks are compared
  on **32 completely independent strong heldout midpoint
  events**. The heldout score does not alter either clock.
- Changed-tone effective audio lag is a *single*
  pilot-only phase-transfer parameter. Same-word and
  changing-tone heldout audio phase errors are scored
  only after freezing its value.

The 48-symbol front-only pilot of TX-15R had
**24.5/58.8 µs median absolute** heldout timing error and
**13.8/30.4°** changed-tone audio-phase RMS. The
distributed-pilot approach reduces the errors on this
*different* waveform. The two layouts are not a controlled
apples-to-apples same-source comparison, but this is a
strong practical improvement.

## Actual complex-RF phase: a separate pilot-frozen test

The complex RF-IQ path uses the saved U8 RTL-SDR samples,
applies exactly the previously verified frequency correction
(`+280500 Hz` relative to the SDR tuner), downsamples from
1,024,000 to 64,000 complex samples/s, and **unwraps complex
RF phase directly**. It does not substitute the previously
recovered FM-audio phase for the carrier phase score.

The model is a **continuous-phase FM hypothesis**:

```text
audio_theta(t) = theta0 + 2*pi*integral(programmed_tone_hz(t), dt)

RF_phase(t) = RF_phase0
            + 2*pi*(relative_CFO*t + CFO_drift*t*t/2)
            + 2*pi*integral(
                 pilot_fitted_deviation(programmed_tone_hz(t))
                 * sin(audio_theta(t)), dt
              )
```

The two unknown initial audio oscillator quadratures are
linearized into two numerical integral basis vectors.
The fit has **five RF-IQ parameters**: constant carrier
phase, carrier offset, first-order carrier-offset drift
(quadratic RF-phase term), and two FM quadrature coefficients.
The six known pilot-tone deviations are also fitted from
pilot-only sinusoidal FM-discriminator amplitudes; the two
unseen pilot tones 3/4 are interpolated using only those
six pilot values. No pilot is selected by looking at the
heldout answer, and no heldout RF phase samples enter a
fit. All fits are evaluated at a fixed 8:1 subsampling and
exclude ±1 ms transition edges, poor carrier magnitude,
guard and pilot/heldout boundaries.

The null model fits **the exact same carrier phase, CFO
and CFO-drift nuisance variables** to the exact same pilot
IQ samples but **does not include programmed FM**.
A further **post-hoc wrong-sign FM diagnostic** freezes
the true pilot-fitted FM parameters but reverses their sign
in the *heldout prediction*; it is not a second transmission.

### Measured heldout complex RF-IQ phase

The scalar coherence below is
`abs(mean(exp(j*(phase_measured - phase_predicted))))`
over the same heldout RF-IQ sample positions for all
hypotheses. **Coherence is NOT complex EVM or modulation
constellation error**; heldout samples within each symbol
are correlated and not independent trials.

| Run | Carrier-only coherence (quadratic CFO) | Programmed FM coherence (quadratic CFO) | Inverted-FM diagnostic coherence | Programmed-FM heldout RF phase RMS |
|---|---:|---:|---:|---:|
| 01 | **0.416** | **0.936** | 0.057 | 22.64° |
| 02 | **0.342** | **0.738** | 0.217 | 52.07° |
| **03 (independent model validation)** | **0.361** | **0.826** | **0.035** | **35.64°** |

For the table above, the programmed FM result for run 03
uses the **distributed piecewise timing model**; runs
01/02 use the *higher-scoring* globally linear timing
variant, selected for tabular illustration after their
collection. Both timing variants are shown in the JSON;
the pre-locked run-03 code scores both as specified.
The **piecewise run-03** result is the
true heldout result for the fixed distributed-pilot
clock algorithm: carrier-only **0.361**, predicted FM
**0.826**, inverted-FM **0.035** and RF phase RMS
**35.64°**.

The piecewise result for runs 01/02 is FM coherence
**0.923/0.724** vs carrier-only **0.416/0.342**,
with heldout phase RMS **24.64°/53.12°**.

A constant-CFO pilot fit gave RF-IQ prediction
coherence of only **0.136/0.254** in the first two
captures and poor pilot fit. That failure led us to
try **first-order relative RF LO drift**. Adding a
quadratic carrier-phase nuisance term greatly improved
the model and was chosen after inspecting runs 01/02.
It is therefore **exploratory** on those two captures,
regardless of pilot/heldout scoring isolation.

**The resulting method was committed as
`fcb28b7` *before* run 03 was emitted.** Run 03 is
an independent repeat testing the chosen model; the
later inverted-FM sign diagnostic is still post-hoc.

The pilot-fitted quadratic model's relative RF CFO
drift on these runs is approximately **-9 to -12.5
Hz/s**. It includes TX and RX oscillators, SDR tuner
offset, calibration and residual artifacts; **it is
not a measurement of K6 MCU or RFIC oscillator drift**.

The fitted magnitude of the two FM quadrature
coefficients is approximately 1.02/1.03 on the first
two captures and 1.11 in run 03, supporting a
comparable FM amplitude response, but it is not
an RF output amplitude calibration.

### Why the distinction is important

We now have three layers of real, independently useful
measurement:

1. **Programmable tone symbols** actually transmitted
   through the B-chip, with 114/114 correct heldout
   tone IDs in each of three conducted RF captures.
2. **Audio tone phase** from the FM discriminator,
   agreeing to 0.4–0.5° RMS across repeated unchanged
   tone writes and around 5–8° RMS at tone changes
   after distributed-pilot timebase correction.
3. **The actual RF carrier's complex phase**, predicted
   from the *source-programmed tone sequence and pilot
   calibration* on independent holdouts with
   0.72–0.94 circular coherence across these three
   captures. The result differs dramatically from
   the carrier-only baseline and wrong-sign modulation
   diagnostic.

The third is **direct evidence that source-controlled
FM modulation produces a predictable RF-carrier-phase
trajectory**. It is NOT evidence that the K6 already
has a programmable, arbitrary carrier-phase DAC, that
an IQ/QAM/OFDM waveform is synthesizable, or that
a software model alone is a working modulation API.

## Implementation, provenance and safety

- `c/k6/mod/tx8_distributed_phase_probe.h` —
  static 192-word waveform, 72 distributed pilot,
  114 data, 6 tail guard
- `c/k6/mod/tx8probe.c` — V4 selector,
  mutually exclusive V2/V3/V4; native
  idle build unchanged, explicit RF fixture and
  watchdog compile-time acknowledgments
- `tools/k6_tx15s_rf_bench.py` —
  isolated one-shot, signed slot backup/readback
  and VFO restoration, no auto-retry
- `tools/k6_tx15s_rf_analyze.py` —
  disconnected FM discriminator audio-phase and
  pilot-timing holdout evaluation, read-only
- `tools/k6_tx15s_carrier_iq.py` —
  separately extracts **complex RF IQ**, performs
  pilot-only 5-param phase fit and compares
  original FM to the carrier-only / inverted-FM
  control on the RF-IQ heldout
- `tests/test_k6_tx15s_distributed.py` —
  Cortex-M0 PA-off and RF-on emulator,
  all 192 writes and restore, compile-time
  safety gates, source/holdout and synthetic
  piecewise-clock verification
- `tests/test_k6_tx15s_carrier_iq.py` —
  synthetic RF complex-phase baseline, wrong-
  waveform and phase drift controls, pilot/holdout
  isolation, replay of all three physical captures

Exact RF-armed module code SHA256:
`3f2e674602753a3746b136c6dccd100572537b2ed10d2de9e14459485c050f38`.
The armed wrapper is **outside the Git repo** at
`~/flux-bench/tx-injection-20261122/tone-oscillator/tx15s-dist150-enabled.c`.
It enforces `TX8_GAIN 24`, test
frequency `433500000u`, `TX8_RF_ENABLE`, RF fixture
acknowledgment, and a firmware-level bounded
**1800-ms TX lease**. Each transaction saved original
slot 30 envelope and readback verification receipts.
Module ID 0x4829 B-chip and expected firmware build
`95d6b260d4122287` were checked.

Immutable physical files under:

`~/flux-bench/tx-injection-20261122/tone-oscillator/`

For each run 01, 02, 03:

```text
tx15s-dist150-runNN.npy                  raw RF SDR IQ
tx15s-dist150-runNN.json                 TX and restore receipt
slot30-before-tx15s-dist150-runNN.k6m    original signed backup
tx15s-dist150-runNN-audio-v1.json        audio-phase and clock holdout
tx15s-dist150-runNN-carrier-v1.json      constant-CFO exploratory model (01/02)
tx15s-dist150-runNN-carrier-v2.json      first quadratic-CFO analysis
tx15s-dist150-runNN-carrier-v3.json      quadratic-CFO and post-hoc inverted-FM
```

Raw I/Q SHA256 for run 01:
`21a23ec1c0b463beefff727ed6f2a1ab00aae0d947032ebf3352fa75aefc11f5`.

Run 02:
`ab90710e2442c6848b2e31e402b1bd519af31fbca9c7eda8994e82959435ec2b`.

Run 03:
`08e95af223b819c731cbdf2771b228fcf6abcc27edbc6e366caa539ef5e8c2df`.

### Reproduce, no radio device required

```sh
cd /storage5/dev/flux-native-fsk-pdm
IQ=~/flux-bench/tx-injection-20261122/tone-oscillator

python3 tools/k6_tx15s_rf_analyze.py \
  --iq "$IQ/tx15s-dist150-run03.npy" \
  --out /tmp/tx15s-audio-new-unique.json

python3 tools/k6_tx15s_carrier_iq.py \
  --iq "$IQ/tx15s-dist150-run03.npy" \
  --out /tmp/tx15s-carrier-new-unique.json

PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
  tests/test_k6_tx15s_distributed.py \
  tests/test_k6_tx15s_carrier_iq.py
```

## Next critical path

The critical unknown is no longer whether we can observe
any RF phase consequence of the native tone program. We can.
The unknown is whether we can **select desired RF carrier
phase *changes* fast enough for useful modulation**.

Suggested next candidates, without removing prior work:

1. **TX-15T phase-steering experiment:** predeclare two or
   more source waveforms chosen to produce different
   integrated carrier-phase trajectories. Use *the same*
   third-party SDR measurement with a heldout
   distinguishability/effect-size criterion. Investigate
   REG_70 oscillator gain/enable gating and regulated
   timing as potential additional phase actuators.
   Avoid claiming an arbitrary RF-phase DAC until
   desired per-symbol RF phase actually follows
   programmed targets.
2. **TX-17 direct signed synthesizer-frequency control:**
   remains a high-upside independent path. Proceed
   RX-only with the B-chip's 0x38/0x39 latch under a
   qualified external reference; do not infer B behavior
   from older non-B silicon or use weak NOAA carrier
   observations as proof.
3. **TX-20 APC + TX-15/17 combined polar control:**
   speculative; amplitude hysteresis and settling remain
   hard limitations. No simultaneous polar RF test has
   qualified yet.
4. **External I/Q baseband/IF daughterboard:** likely the
   most realistic approach if true arbitrary IQ is
   mandatory and existing chip paths plateau.

An independent clock reference for MCU and SDR
would also improve the interpretation of phase errors.
These results already justify continuing source-aware
RF-carrier phase experimentation, *not* asserting
general SDR compatibility.
