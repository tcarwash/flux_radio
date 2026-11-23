# Flux TX-17C — B-chip direct synthesizer low-word TX latch experiment

**Status: fixed experimental protocol and analyzer before physical RF.**
**Source:** `c/k6/mod/tx17c_direct_frequency_latch.c`.
**Offline analyzer:** `tools/k6_tx17c_direct_frequency_analyze.py`.
**Physical one-shot:** `tools/k6_tx17c_rf_bench.py`.
**Test modules:** `tests/test_k6_tx17c_direct_frequency.py`,
`tests/test_k6_tx17c_frequency_analyze.py`.

## Why investigate this

The K6's 0x4829 FD6818B B-chip has proven serial-controlled FM
audio modulation and pilot-conditional RF phase steering (TX-15U).
A *direct signed RF frequency* actuator could be significantly better
for general phase synthesis, but prior evidence from the old non-B
0x4819 receiver that 0x38/0x39 writes latch until RXON **must not
be generalized** to B. TX-17 RX-only NOAA references were weak,
so no physical B-specific register-latch test had been executed.

This test eliminates dependence on an external RX reference by
using the already operator-confirmed dummy-loaded 433.500-MHz
transmitter and an air-coupled RTL-SDR to observe actual RF.
The BK4829 synthesizer implements frequency generation and FM
and uses 0x38/0x39 to tune the center. Neither existence of those
registers nor immediate readback proves that they affect the
carrier without a rekey.

## Specific hypothesis / negative control

**Predeclared yes/no:** after **one** initial TXON, the K6
writes a bounded change to **REG 0x38 only** (low 16 bits of
the LO-frequency word, in nominal 10-Hz units), and the
transmitted RF carrier shifts immediately while still keyed.

The register high word 0x39 is never changed during the
steps, and 0x30 is never written. No retune, RXON, TXON
or PA off/on transition occurs between test steps.

Expected schedule relative to the **received PA rise**:

| Region | Duration | REG_38 relative change | Expected instantaneous RF offset |
|---|---:|---:|---:|
| Introduction / reference | 150 ms | 0 | 0 Hz |
| Step 0 | 160 ms | 0 | 0 Hz |
| Step 1 | 160 ms | +50 | +500 Hz |
| Step 2 | 160 ms | 0 | 0 Hz |
| Step 3 | 160 ms | -50 | -500 Hz |
| Step 4 | 160 ms | 0 | 0 Hz |
| Step 5 | 160 ms | +100 | +1000 Hz |
| Restoration tail | 150 ms | 0 | 0 Hz |

Expected keyed duration is approximately 1.26 s,
inside a **1,800-ms firmware TX lease**. Each write is
followed by immediate REG_38 readback and firmware
aborts on refusal or inconsistent readback.

The B-chip's actual low-word/nominal 10-Hz frequency
relationship remains an experimental question.
The user-level B driver names 0x38/0x39
REG_FREQ_LO/REG_FREQ_HI and writes the calibrated
channel frequency in 10-Hz units.

### Guards and recovery

- The default source is **inert** unless
  `TX17_BENCH_ENABLE=1` is built.
- Active RF requires explicit `TX17_RF_ENABLE=1`
  plus `TX17_FIXTURE_ACK=1` and
  `TX17_WATCHDOG_ACK=1`. Exact source
  SHA256 is pinned in the host-side bench.
- Firmware ABI level 3, B-chip identifier
  `0x4829`, channel `433500000u`, no pressed
  key, and RSSI occupancy guard twice.
- TXON reads and saves REG_38, REG_39,
  REG_70, REG_71; it refuses any high word
  other than `0x0295` or a low word
  outside `[0x7000,0x8000]`, containing the
  expected 433.5-MHz word `0x029577F0`.
  Up to 100 low-word counts change the
  channel by at most approximately 1 kHz,
  with no low-word overflow/carry.
- On all normal and aborting paths, PA is
  disabled; the saved original frequency
  words, gain and tone configuration are
  restored; PLL word readback is checked;
  TXOFF ends the bounded lease.
- The host bench checks exact expected
  firmware build, current original slot-30
  signed executable, both initial VFOs at
  144.390 MHz, idle/not transmitting,
  saved raw SDR baseline occupancy and
  uncorrupted signed source image.
- The original module envelope is backed
  up before any write, a temporary module
  is installed with a newer signed serial,
  and a **known exact** executable is restored
  with verified readback. Both VFOs are
  returned to 144.390 MHz. The bench never
  silently retries.
- No main MCU firmware flash, trim flash,
  calibration records or unknown GPIO/PLL
  registers are changed by this test.
  The user has authorized broader
  experiments; those remain separate
  opt-in tests with recovery plans.

### Bench variants

- `variant=0`: PA off, still uses
  bounded TXON and may leak RF. Only
  intended to establish register readback,
  safe restore and the module timing.
  **Not** a conclusive carrier-frequency test.
- `variant=1`: PA on, dummy-loaded, SDRed
  complex RF capture. This is the physical
  B-specific direct-register latch test.

Both encoded C images have independent pinned
SHA256 in `tools/k6_tx17c_rf_bench.py`:

- PA-off variant 0:
  `7995d322e5c8b197abc220d3716f5c9ddde4dffcd0808fba6cb89f3fde5be9b5`
- RF-on variant 1:
  `149a5a5a5c037b7632257d1c58b315a57eeb1918f1ca48bfd0bc0a13a8740d4b`

### Precommitted physical analysis

`k6_tx17c_direct_frequency_analyze.py` extracts the original
air-coupled RTL-SDR complex IQ (5 seconds, 1.024 MS/s),
applies the existing receive frequency correction, and
works at 64 kcomplex samples/s. It detects the actual PA rise
from a 10-ms smoothed complex magnitude envelope, never from
the requested commands alone.

For each expected steady plateau, it discards **35 ms** near
the register transition and measures robust median RF
instantaneous frequency. The reference is the median of
the five unaffected plateau controls. It subtracts the
common SDR carrier offset/drift rather than claiming absolute
RF frequency calibration.

An immediate B synth response is **confirmed** if
+50/-50/+100 word changes each recover within
**±150 Hz** of the predicted **+500/-500/+1000 Hz**,
all control medians remain within **±90 Hz**,
and the PA signal remains strong enough to observe.

A **negative latch** result requires
all three nominally stepped plateaus have
absolute RF deviations below 90 Hz and all
controls below 90 Hz while RF is present.
It would mean **no measured immediate change without
RXON/TXON on this B-chip**, NOT proof that
frequency words never retune.

Anything else is **inconclusive**. A useful path must
also survive separate PLL settling, discontinuity,
spectral emissions and phase-continuity tests.
There is **no arbitrary carrier IQ or RF-phase DAC
claim from mere frequency steps**.

### No-radio reproducer

```sh
cd /storage5/dev/flux-native-fsk-pdm
PYTHONPATH=/tmp/flux-fsklab-unicorn-deps \
python3 -m pytest -q \
 tests/test_k6_tx17c_direct_frequency.py \
 tests/test_k6_tx17c_frequency_analyze.py

python3 tools/k6_tx17c_direct_frequency_analyze.py \
  --iq ~/flux-bench/tx-injection-20261122/tone-oscillator/tx17c-pll-lowword-1-run01.npy \
  --out /tmp/tx17c-new-analysis-unique.json
```

**Interpretation must distinguish** readback success,
time-resolved over-the-air RF response, PLL settle/phase
behavior and radio restoration. Never promote a
readback-only success to a signed-frequency DAC.
