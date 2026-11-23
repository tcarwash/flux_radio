# Flux TX-15V — B-chip REG_70 tone gate: oscillator phase memory

**Status:** fixed protocol BEFORE real RF. This is a distinct actuator
from the TX-15U frequency-word phase-steering pulse.
**Research branch:** `research/native-fsk-pdm`.
**Physical bench:** K6 FD6818B 0x4829, 433.500-MHz dummy load,
air-coupled RTL-SDR U8 complex IQ, 1.024 MS/s.
**Experiment modules:** `c/k6/mod/tx15v_gate_phase.c`,
`tools/k6_tx15v_rf_bench.py`,
`tools/k6_tx15v_gate_phase_analyze.py`.
**Tests:** `tests/test_k6_tx15v_gate_phase.py`,
`tests/test_k6_tx15v_gate_phase_analyze.py`.

## Hypothesis

The B-chip's audio tone generator is already proven to steer RF carrier
phase by programmable frequency excursions through REG_71. However
a fixed pulse's final phase depends on the oscillator's unknown
initial phase, which our external SDR can estimate only *after*
the waveform begins.

A different modulation entry point is **REG_70 tone enable**. Does
the oscillator's internal phase, while tone is disabled, behave as:

- **free-running:** phase continues accumulating at the last audio rate
  even when no tone appears on FM RF;
- **frozen:** oscillator phase stays at the value reached at disable,
  and resumes from there on re-enable;
- **reset:** oscillator restarts at a repeatable initial phase
  on each REG_70 enable write?

If the phase is reliably reset and the RFIC path has consistent
latency, short source writes may allow controlled phase-state
initialization. If it freezes or free-runs, other phase-control
encodings may still be possible. A phase result from recovered
FM audio alone is **not yet a direct carrier-phase-DAC result**.

## Fixed source stimulus

REG_71 is programmed ONCE to **11264**, approximately **1091 Hz**
and remains at that word throughout transmission. REG_70 tone 1
enable/gain is `0x9800` when ON (gain 24) and 0 when OFF.

PA keys once on the already operator-confirmed dummy load;
**PA stays on throughout all tone enable gates**. Only the
audio FM tone is turned on/off, not the RF carrier.

Fixed ON/OFF schedule relative to initial PA rise:

| Segment | Duration | REG_70 |
|---|---:|---:|
| Pilot ON 0 | 200 ms | ON |
| OFF 1 | 57 ms | OFF |
| Pilot ON 1 | 170 ms | ON |
| OFF 2 | 71 ms | OFF |
| Heldout ON 2 | 170 ms | ON |
| OFF 3 | 83 ms | OFF |
| Heldout ON 3 | 170 ms | ON |
| OFF 4 | 97 ms | OFF |
| Heldout ON 4 | 170 ms | ON |
| Shutdown guard | 100 ms | OFF |

The total source time plus setup and guard is
approximately **1.42 s**, within a **1,800-ms**
TX lease. Four noncommensurate gap durations were
chosen to separate the three phase-memory models.

Radio reads and restores original REG_70/71 on exit,
with immediate register readback after each gate.
The source refuses incorrect B-chip, key-held,
wrong VFO, occupied channel and missing RF
acknowledgments. Default source build is inert.

## Precommitted receiver and acceptance

`tools/k6_tx15v_gate_phase_analyze.py` operates on
**saved** real RF complex IQ but scores the *recovered
FM discriminator audio-tone phase*, not direct carrier IQ EVM.

- Detect actual PA onset from the SDR RF magnitude,
  not host command timestamps. Use source-known
  ON/OFF windows with **13 ms transition margins**
  to exclude TX/RX audio filter settling.
- Fit the common 1091-Hz audio-tone frequency only
  from the first two ON segments (pilot ON0/ON1).
  Search within ±10 Hz of the source frequency,
  with per-segment amplitude, DC and phase nuisance
  terms. Holdout ON2–ON4 are not used for fitting.
- Use phase at ON1 as common initial reference.
  Predict ON2–ON4 phase under three explicit
  hypotheses: tone phase free-runs during OFF,
  tone phase freezes during OFF, or tone
  phase is reset to a fixed starting phase
  on each enable. Scores are circular phase
  residual RMS over the three heldout ON segments.
- Require ON sinusoid amplitude to exceed
  OFF audio RMS by at least **4x**. Otherwise
  the gating itself is unqualified.
- Label a phase model **qualified** only if its
  RMS is ≤**35°** and the next-best model's
  RMS is at least **30° higher**. Keep all
  phase model errors and **inconclusive**
  outcomes; do not re-fit using the heldouts.
- Run two independent physical captures
  only after the source+scorer+regression
  tests are committed. It must reproduce,
  or remain unproven. Synthetic positive
  controls for each phase-memory hypothesis
  and holdout-leakage tests are part of
  the commit.

Only the carrier's **FM audio phase** is inferred
by this test; a successful reset hypothesis will
need a separate source-controlled **complex-RF
carrier phase** validation with equalized amplitude,
explicit RF phase targets, and receiver-LO
calibration to become a usable phase-state
selection primitive.

### Exact hardware source binaries / restoration

The test uses the previously validated slot-30
backup+restore host transaction, exact
firmware `95d6b260d4122287`, both VFOs initially
144.390 MHz, and new signed module serials.
Both physical variants have an 1800-ms TX
watchdog, a bounded low-power 433.500-MHz
fixture and no automatic retry.

- PA-off preflight: compiled code SHA256
  `2c6c1b52126a9e4ac9326084829a347a34195210a3cbba4e26a7d91dfc5bd790`.
- Dummy-load RF-on: compiled SHA256
  `a9987bd70a03781de8c0c9a873238b902bd07abd0de4d4a53b70c7d7284570ed`.

Compiled wrapper sources are outside Git at
`~/flux-bench/tx-injection-20261122/tone-oscillator/tx15v-gate-{0,1}-enabled.c`.
The trusted original slot-30 executable is
`6580529c1ba552dfb9db795ce05d629ad5ac7da95231acab2bda92b349ece522`.

No main firmware flash, calibration records,
PLL center frequency, arbitrary GPIO or unknown
RF register is changed in this experiment.

### Reproducible offline analysis

```sh
cd /storage5/dev/flux-native-fsk-pdm
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
 tests/test_k6_tx15v_gate_phase.py \
 tests/test_k6_tx15v_gate_phase_analyze.py

python3 tools/k6_tx15v_gate_phase_analyze.py \
 --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx15v-gate-1-run01.npy \
 --out /tmp/tx15v-check-unique.json
```
