# Flux TX-15T / TX-15U — intentional RF-carrier phase steering, physical evidence

**Hardware:** FD6818B/BK4829 B-chip K6, chip ID `0x4829`, Flux
firmware build `95d6b260d4122287`, conducted RF output into the
operator-confirmed dummy load at 433.500 MHz, air-coupled RTL-SDR
complex-IQ observation at 1.024 MS/s.

**Local date:** November 22, 2026 (Pacific), corresponding to
November 23 UTC. **Branch:** `research/native-fsk-pdm`.
**Status:** Eight real, bounded conducted-RF captures with archive
integrity and verified hardware restoration. The final **TX-15U
method was frozen before four genuinely independent new emissions;
4/4 passed**: two non-steering controls and two steered pulses.

**What was demonstrated:** a short change to six RFIC tone-register
words produces a **pilot-predicted, retained displacement in the
measured complex RF-carrier phase** after the same tone resumes.
The effect is not merely a different decoded FSK tone or
frequency discriminator audio phase. It is **NOT** an independently
commandable absolute RF phase DAC, arbitrary I/Q transmitter,
QAM/OFDM transmitter, spectral-mask qualification, or a calibrated
TX-only carrier phase measurement. The SDR's free-running oscillator
and the transmitter/receiver response remain entangled.

## Experiment and frozen data chronology

The chronology matters; do not report retrospective fits as
prospective independent tests.

1. **TX-15S** had already physically demonstrated pilot-conditioned
   prediction of a known continuous-phase FM source on real complex
   RF IQ. All four 18-word timing pilot windows, source phase modeling
   and the six-symbol shutdown guard were established in that work.
   See [TX-15S physical evidence](k6-tx15s-distributed-pilot-carrier-physical-20261122.md).
2. In **commit `582fa53`**, BEFORE any TX-15T RF capture, two
   exact-hash-pinned A/B firmware variants, the six-word phase pulse,
   and a predeclared engineering scorer/thresholds were frozen.
   See [TX-15T original prospective protocol](k6-tx15t-phase-steering-prospective-20261122.md).
3. Four TX-15T **development** recordings in chronological order
   **A01, B01, B02, A02** were made with the original frozen scorer.
   The original protocol passed **three** and **failed one control**.
   It is essential to retain the original **A02 FAIL**.
4. Diagnostics on those four recordings found a small accumulated
   effective audio-oscillator phase-rate error. A one-scalar
   **pilot-audio-only source-rate correction** improved the model.
   All changes were implemented and frozen in **commit `16c9641`
   BEFORE any subsequent physical capture**, with the *original
   TX-15T control/pulse hardware binaries unchanged*. See the
   [TX-15U prospective analysis plan](k6-tx15u-pilot-audio-rate-prospective-20261122.md).
5. Four genuinely **independent TX-15U validation** recordings were
   then emitted and scored against the frozen new analysis, in the
   deliberately differing chronological order **A03, B03, B04, A04**.
   **All four passed the previously locked qualification thresholds**.
   No analysis or hardware source change intervened between validation
   captures. The last two emissions reversed A/B order.

## Source change: exactly six RFIC tone writes

Two static, safe, pre-compiled test programs contain **192 REG_71
writes**, nominally **150 symbols/s**, within a firmware **1800-ms
TX lease**. The same 72 pilot slots in four windows
`[0,18), [48,66), [96,114), [156,174)`, all earlier
data, and the same 6-slot non-scored terminal guard
`[186,192)` appear in both source versions.

| Slot indices | A: control | B: phase-steering pulse |
|---|---|---|
| 174–176 | tone 4 (1693 Hz) | identical |
| 177–179 | tone 0 (502 Hz) | **tone 1 (782 Hz)** |
| 180–182 | tone 7 (2519 Hz) | **tone 2 (1091 Hz)** |
| 183–185 | tone 0 (502 Hz) | identical |
| 186–191 | tone 0, guarded TX tail | identical |

The two 3-symbol changes create a roughly **40-ms programmed
audio-FM frequency excursion**. Under the three earlier TX-15S
reference clocks, A and B's *modeled audio-oscillator phases*
rejoin within about **4–6 degrees**. Any difference in the
following common-frequency RF carrier phase is candidate **retained
phase steering**, not simply transmitting a different tone
in the scoring interval. This rejoin claim is approximate and
receiver-timebase-relative, not direct probe of the chip's oscillator.

No native K6 firmware flash, calibration writing, RF emissions
outside the known conducted bench, or simultaneous APC+FSK is involved.

## Original TX-15T study — retain the failed control

The original pilot-only RF-carrier phase model allowed relative RF
carrier CFO, quadratic phase/linear CFO drift, two FM quadrature
components and a pilot-last-window residual CFO correction.
Predicted/observed phase shifts were compared within the SAME
recording, never using absolute carrier phase across different
emissions. The correct program and the counterfactual were scored
on exactly the same heldout RF IQ samples.

| TX-15T development capture | Pilot predicted B−A step | Observed A-relative step | Correct/wrong active RF-phase coherence | Original locked decision |
|---|---:|---:|---:|---|
| A01 | −66.8° | +2.9° | 0.702 / 0.451 | Pass |
| B01 | +139.8° | +122.3° | 0.988 / 0.552 | Pass |
| B02 | +87.0° | +119.9° | 0.661 / 0.318 | Pass |
| **A02** | +94.5° | **+79.3°** | **0.306 / 0.238** | **FAIL** |

A02 produced all 192 writes on schedule, recovered all 114
heldout tone IDs and maintained strong RF amplitude. Its
recovered FM audio tone phase was relatively stable, but the
source-based **RF-carrier phase predictor** suffered large
error, especially in lower-frequency (502-Hz) FM tone
segments. The original **one failed control among four**
is NOT erased by the subsequent TX-15U development.

The older data suggested that a common effective audio
tone-rate offset of order **−0.15 Hz** relative to the SDR
timebase would correct accumulated phase error. Fitting that
scalar on pilot-only audio phase produced the following
**RETROSPECTIVE (DEVELOPMENT) observations only**:

| Old capture | Old correct-model coherence | Revised model coherence | A-relative observed phase under revised model |
|---|---:|---:|---:|
| A01 | 0.702 | 0.938 | −5.0° |
| B01 | 0.988 | 0.978 | +131.6° |
| B02 | 0.661 | 0.964 | +39.9° |
| A02 | 0.306 | 0.900 | −6.8° |

The revised model passes all development captures, but that
result **is not independent validation** and must not be
combined with the prospective pass count.

## TX-15U pilot-only model and engineering acceptance

The new TX-15U scorer was committed before independent
recordings. It reads archived raw complex RF IQ (not the FM
discriminator alone) and:

- Fits symbol timebase from four *predeclared* strong frequency
  transition pilot windows, without consuming heldout edges.
- Fits tone phase from the FM-discriminator only on pilot
  slots; subtracts accumulated nominal source phase, unwraps
  residuals within each repeated identical-tone group, and
  removes per-tone static phase intercepts. A single OLS
  slope gives the effective **additive audio frequency
  offset Δf**. Exactly six pilot tone groups, at least eight
  valid pilot words per group, residual phase RMS ≤30°,
  and |Δf| ≤0.60 Hz are enforced.
- Builds **both** source predictions using the same
  pilot-fitted Δf. This modifies only the analysis; the
  K6 firmware still transmits exactly the original
  A/B source frequencies.
- Fits the same five complex-RF phase parameters to
  identical pilot IQ for both candidate source programs:
  relative phase, CFO, first-order CFO drift and
  two audio-FM quadratures. A local last-six-pilot
  residual CFO slope (also PILOT ONLY) is common
  to both models.
- Scores identical heldout complex-RF sample positions:
  active steering words **177–185**, clean guard
  **187–190** and last pilot anchor **168–173**.
  Excludes word 191, which physically contains dekeying
  transients. The guard phase is measured relative to
  the same recording's late pilot after subtracting
  the control A phase prediction.
- Keeps original TX-15T qualification thresholds:
  A guard step within ±40° of zero and correct A
  model coherence at least 0.10 better than wrong B;
  B pilot-predicted phase magnitude ≥35°, observed
  ≥25° same sign, angular prediction error ≤45°,
  and correct B coherence advantage ≥0.10.

The pilot measurement conditions the *sign and magnitude*
of the expected phase displacement. A fixed programmed
pulse **does not** set an absolute phase angle without
measuring/controlling the audio oscillator's initial phase.
The fitted effective rate includes TX/RX/SDR biases and
should not be described as a calibrated chip clock error.

### Independent, prospective RF results — four of four pass

| New capture | Pilot Δf | B−A phase predicted from its pilot | Measured A-relative RF phase | Correct/wrong program RF-phase coherence | Locked pass |
|---|---:|---:|---:|---:|---|
| A03 (control) | +0.011 Hz | +134.6° *(hypothetical B)* | **−1.4°** | **0.938 / 0.530** | Yes |
| B03 (pulse) | −0.084 Hz | **+93.8°** | **+90.1°** | **0.964 / 0.521** | Yes |
| B04 (pulse first) | −0.048 Hz | **+69.3°** | **+74.5°** | **0.976 / 0.522** | Yes |
| A04 (control second) | −0.051 Hz | +118.4° *(hypothetical B)* | **−1.5°** | **0.855 / 0.533** | Yes |

**Two independent B phase pulses:**
- B03 retained RF-carrier phase: predicted **+93.79°**,
  measured **+90.10°**, circular error **−3.69°**.
- B04 retained RF-carrier phase: predicted **+69.26°**,
  measured **+74.52°**, circular error **+5.26°**.

**Two independent A controls:** measured apparent phase
shifts only **−1.40° and −1.50°** despite their
pilot data predicting that B would have caused very
different positive phase displacements. The correct
A source hypothesis beat counterfactual B on both.

**Interpretation:** strong evidence that changing exactly
six B-chip tone-oscillator words can cause a predictable
RF-carrier phase displacement that persists after the
same nominal audio tone resumes. This is meaningful
progress toward phase-controllable digital FM
modulation. It is not an assertion that any arbitrary
RF-carrier phase angle can be selected, that the
effect extrapolates to high rates, or that both
amplitude and phase can be independently commanded.

The whole study has only **four prospective recordings**:
two control and two pulse; the many adjacent SDR samples
within each recording are correlated and should NOT be
treated as independent trials for a confidence interval.

## Exact physical provenance and safety verification

All **eight TX-15T/A/B** conducted-RF transactions
had firmware-report `n=192 late=0`, completed
within the 1800-ms lease, captured raw air-coupled
SDR U8 RF complex IQ, saved the original slot-30
module envelope and verified restoration of the
trusted original module. The VFOs were restored to
**144.390 MHz**, both idle RX. No MCU firmware
was flashed, and the other serial-connected lab
device was not disturbed.

- Original slot-30 code SHA256:
  `6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`
- Temporary A control executable SHA256:
  `de53fc01aacb6a5e63fbf5e1bc73ffcc0b53fe30d1b6d4822cc9ed28ee9f6989`
- Temporary B pulse executable SHA256:
  `bbbe7b3bc47f651d9f7c5df2a4ff0be7c9069048fa6f3e2bc5b5a88624c7299b`
- Frozen original A/B code and thresholds:
  `582fa5391918e852941ac39a1a26f4f160ebed6a`
- Frozen TX-15U audio-rate analysis, prior to new runs 03/04:
  `16c9641aad599f424f36bd6bb65a69f0c7a66ba1`
- **Source-of-truth archived evidence index:**
  `~/flux-bench/tx-injection-20261122/tone-oscillator/tx15t-tx15u-physical-evidence-index-v1.json`

The evidence index gives, for each of eight recordings:
raw-IQ SHA256, exact capture/transaction filenames,
both source executable hashes, full firmware/restore
receipts, independent vs development label,
original TX-15T score (when available),
revised TX-15U score, estimated effective rate,
predicted/measured phase displacement, and
correct-vs-counterfactual RF-phase coherence.
All eight raw IQ hashes were independently recomputed
and compared with their TX-15U analysis reports;
the corresponding firmware execution receipts,
original source executable hashes and module
restoration checks were also verified.

For each `tx15t-phase-steer-{0|1}-run{01..04}`:

```text
<stem>.npy                          original raw 1.024-MS/s RTL-SDR U8 complex IQ
<stem>.json                         physical transaction and restoration receipt
slot30-before-<stem>.k6m             trusted original module full envelope
<stem>-locked-v1.json                original TX15T scorer, first four captures (development)
<stem>-TX15U-posthoc-v1.json         revised scorer, runs 01/02 DEVELOPMENT ONLY
<stem>-TX15U-locked-v1.json          revised scorer, runs 03/04 PROSPECTIVE VALIDATION
```

**Do not overwrite or reinterpret original data.**
No A/B physical source has been changed since the
first predeclared source commit.

### Reproduce without any physical radio/SDR connection

```sh
cd /storage5/dev/flux-native-fsk-pdm
IQ=~/flux-bench/tx-injection-20261122/tone-oscillator

python3 tools/k6_tx15u_phase_steering_clock.py \
  --iq "$IQ/tx15t-phase-steer-1-run03.npy" \
  --variant 1 \
  --out /tmp/tx15u-B03-new-unique.json

python3 tools/k6_tx15u_phase_steering_clock.py \
  --iq "$IQ/tx15t-phase-steer-0-run04.npy" \
  --variant 0 \
  --out /tmp/tx15u-A04-new-unique.json

PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
  tests/test_k6_tx15t_steering.py \
  tests/test_k6_tx15u_phase_steering_clock.py
```

Both analyzers are READ ONLY; they neither transmit RF nor
write to the radio/SPI. All sources remain opt-in and
guarded. The in-repo tests explicitly assert that
**the first frozen A02 control remains a FAILURE**
while verifying the later independently passed A03/A04
and B03/B04 with the frozen improved estimator.

## Practical next RF-waveform directions

1. **TX-15V phase-state selection and acquisition:** move
   from pilot-conditional predictions after a fixed six-write
   pulse to selecting a requested differential carrier
   phase target (`0°, 90°, 180°, 270°`) using
   *source-controlled* initial oscillator phase or
   another independent control channel. Test signed
   steering targets on fully new RF captures, with
   equalized tone deviations, repeated reference pilots
   and a fixed spectral/power mask qualification.
   Without accessible live RF feedback, the current
   A/B primitive remains *pilot-conditional*, not a
   general closed-loop phase modulator.
2. **TX-17 B-chip RF synthesizer offset/latch** (RX-only
   first) stays an independent high-upside route. It
   needs a verified strong, stable external receive
   reference before changing 0x38/0x39 or XTAL trim.
   Previous weak NOAA reception did not qualify.
3. **TX-20 APC amplitude/polar channel:** amplitude
   actuator is slow/hysteretic. Only combining with
   phase after a separate safe joint experiment could
   support true complex-envelope waveforms.
4. **Direct digital baseband or external IQ daughterboard**
   remains a realistic fallback for arbitrary I/Q,
   QAM/OFDM or broadband SDR-like operation.

**Bottom line:** Flux is no longer limited to fixed audio
tone selection or FSK detection; on this conducted bench
we have physically demonstrated **intentional and
predictable retained RF phase displacement** selected by
a six-register-write FM source pulse, with two fresh
independent controls and two pulses passing a previously
frozen analysis. Full arbitrary RF waveform synthesis
remains **unproven**, and should not be advertised as
implemented.
