# TX-15W — REG70 FM deviation gain ladder, prospective physical protocol

**Status: frozen before physical RF recording.** This experiment
is a third independent B-chip modulation-control vector
after TX-15U frequency-pulse phase steering and
TX-15V oscillator phase-preserving enable gating.
This project does not yet demonstrate an arbitrary
RF-IQ modulator.

## Fixed source control

K6 FD6818B ID 0x4829, tone oscillator REG71 fixed at
**11264 (~1091 Hz)**, 433.500-MHz conducted
dummy-load PA output, serial and original slot30
module preserved. Every change is to **REG70 gain**
while the oscillator enable bit remains set;
no REG38/39 PLL retune, REG36 APC, I2S, mic
or calibration flash access.

Single 1800-ms TX lease (firmware enforced),
PA ON throughout the 13 plateau sequence;
source has a lead, a fixed shutdown guard,
then restores exact original tone/gain registers.

13 gain plateaus, **85 ms each**, in chronological order:

```text
 24, 8, 24, 16, 24, 32, 24, 48, 24, 64, 24, 80, 24
```

There are seven repeated gain24 references, interleaved
with six other gain values. The source writes and
reads back the requested `REG70=0x8000|(gain<<8)`
word on each plateau (the initial 24 is applied
before PA-on); the PA stays keyed to avoid
conflating gain control with RF amplitude keying.
It returns **13**, with 0 and saved gain on
cleanup as appropriate. The source uses
an operator-confirmed dummy load, refuses
unexpected chip, wrong VFO or occupied channel,
and is **inert** outside the explicit bench wrapper.

## Training and holdout score, fixed before RF

`tools/k6_tx15w_gain_ladder_analyze.py` is a
READ-ONLY offline analyzer of archived U8
complex-RF IQ (1.024 MS/s), demodulated
to 64-kS/s FM discriminator AUDIO. The
measured quantity is **FM frequency deviation**;
REG70 is not an RF power gain control.

- Detect RF-onset by strong complex-RF
  magnitude, not command timestamps.
- For each 85-ms slot, drop the first
  and last **14 ms** to exclude analog
  settling. Keep ~57 ms steady audio.
- **Only slots 0 and 2 (both gain24)**
  are used to fit one common oscillator
  audio tone frequency, phase, and
  gain24 amplitude. No later gain
  plateaus can influence this training.
- For each other gain, independently
  extract 1091-Hz sinusoid amplitude,
  phase residual relative to the
  pilot oscillator reference, DC
  offset and local fit RMS. The other
  24 controls test drift, hysteresis
  and source phase continuity.
- A **monotonic gain-dependent** path
  requires that the recovered
  deviations at six ordered test
  gains [8,16,24,32,48,64,80]
  increase at least **5% of the
  gain24 reference amplitude** at
  each adjacent step.
- Repeated 24-gain amplitude is
  stable if its max-minus-min is
  at most **15% of median**.
- Repeated 24-gain phase is stable
  if held-out repeated-reference
  phase RMS is at most **18°**
  (after pilot frequency estimate).
- The combined qualified result
  requires all three criteria.
  Retain each separate outcome and
  complete measurements if combined
  criterion fails. No later pilot
  or threshold retuning to force
  a pass.
- Controls and synthetic source
  (linear gain, flat/nonresponsive
  gain, induced phase discontinuity)
  must pass before real RF.

This is gain/deviation modulation on
an air-coupled uncalibrated receiver:
**not RF absolute power, FCC spectral
compliance, directly selected RF IQ
phase, or standalone arbitrary
complex-envelope synthesis**.
A qualified amplitude response on
the FM discriminator alone still
needs direct complex RF trajectory
prediction and spectral occupancy
before production use.

## Bounded hardware process

The already proven one-shot host script was
adapted as `tools/k6_tx15w_rf_bench.py`
to back up signed original slot 30,
install the exact test image with
monotonic serial, check radio firmware
build `95d6b260d4122287`, B-chip and
both original VFOs 144.390 MHz idle,
check occupancy with both K6 and SDR,
run only one module, save raw IQ
and all receipts, verify restoration
of original module/VFO, no automatic
retry. No MCU flash or calibrations
will be written.

External, non-Git compiled wrappers:
`~/flux-bench/tx-injection-20261122/tone-oscillator/tx15w-gain-{0,1}-enabled.c`

Exact pinned compiled module SHA256:

- PA-off `2b49c6e8d6f05013892d1e6c48c28b7a83790a88d57ea68d33ba00f69b2d2422`
- PA-on `1c8e29ae886a1abfefa3800bffa7f5b1d549448ef50ab1799cb42dbc02effe83`

Source implementation: `c/k6/mod/tx15w_gain_ladder.c`.
Emulation: `tests/test_k6_tx15w_gain_ladder.py`.
Synthetic RF-audio analysis:
`tests/test_k6_tx15w_gain_ladder_analyze.py`.

### Reproduce without hardware

```sh
cd /storage5/dev/flux-native-fsk-pdm
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
 tests/test_k6_tx15w_gain_ladder.py \
 tests/test_k6_tx15w_gain_ladder_analyze.py

python3 tools/k6_tx15w_gain_ladder_analyze.py \
 --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15w-gain-1-run01.npy \
 --out /tmp/tx15w-new-unique.json
```

The RF-on test will be conducted only
after this exact source/scoring/safety
protocol has been committed.
