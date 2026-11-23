# Flux TX-20 — FD6818B calibrated APC envelope feasibility checkpoint

Date: 2026-11-22 (bench session continued into 2026-11-23 UTC).
Branch: `research/native-fsk-pdm`, source tree
`/storage5/dev/flux-native-fsk-pdm`.

**Goal:** determine whether the FD6818B's calibrated PA automatic-power-control
(APC) target can act as an independent, fast, repeatable RF **amplitude
envelope** control. This is the missing second control dimension for
the proposed arbitrary-IQ-to-polar waveform engine.

**Current result:** The actual radio confirmed a calibrated low-power
APC target of **180** (`REG_36=0xB4A2`), and the firmware-owned PA-off
state was `REG_36=0x0022`. A new opt-in firmware callback and a
7-step staircase C module have been built and statically tested,
and a new experimental firmware image compiled successfully.
**No amplitude staircase has been transmitted and no RF envelope
transfer function has been measured yet.**

## Physical evidence: calibrated low-power PA fingerprint

The same K6/FD6818B ID 0x4829 and user-confirmed dummy load used in
the native-FSK study. A deliberately gated module
`c/k6/mod/apc_fingerprint.c` exercised only the **firmware-owned**
`tx_on(650)` and `tx_pa(0)` callbacks. It **never wrote REG_36**.

Module return:

    APC on=B4A2 off=0022
    result=1

That means the observed UHF driver word was 0xA2 with the PA
enabled and the calibrated *low-power* APC target was 0xB4=180.
When PA was disabled by the firmware callback, register readback
was 0x0022. The receiver returned to idle RX, both VFOs were
restored to 144.390 MHz, and original slot-30 module code was
restored and independently verified.

Capture, metadata, and exact original slot backup:

    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fingerprint-run01.npy
    ~/flux-bench/tx-injection-20261122/apc-lab/apc-fingerprint-run01.json
    ~/flux-bench/tx-injection-20261122/apc-lab/original-slot30-before-apc.k6m

This is **one hardware observation**, not proof of an amplitude
response, output power in watts, or linearity.

## Experimental firmware API level 27

New `firmware/k6-aura/apply-apc-tx-api.py` appends
`apc_set(percent)` to the **existing** module ABI, after level-26
`micin_tx`; old module callbacks remain in their original slots.

`K6_APC_TX=1` opts in to the gated build, requiring
`K6_MICIN_TX=1`, `K6_LIVE_SERIAL=1`,
`K6_LEAN_TXDOG=1`, cable-only mode and FD6818B code.
The normal firmware and regular module `fd_write` still do
**not** permit raw writes to REG_36.

Within a bounded, already-active firmware-owned TX, the new
callback:

1. Checks the independent transmitter watchdog, active TX,
   no competing tone, exact 433.500 MHz test channel, chip
   identity 0x4829, and expected normal module TX state.
2. Uses the live calibrated *low-power* APC target saved by
   the firmware when the TX lease begins. No hardcoded
   radio-specific calibration is written to flash.
3. Accepts **only 100%, 90%, 80%, or 70% of that target**.
   It never raises the target above the radio's calibrated
   baseline and never modifies UHF PA lower-byte driver bits.
4. Verifies the exact register readback. If verification
   fails, the callback forces TX shutdown rather than
   continuing with unknown PA state.
5. Clears its saved target when ordinary `tx_off` returns
   to RX. Any new TX lease obtains its own calibrated baseline.

On the tested unit with baseline 180, the corresponding APC
target bytes would be **180, 162, 144, and 126**, but these
are **register targets, not linear amplitude or power ratios**.
A fractional change in APC target does not imply the same
fractional change in RF voltage or radiated power.

## Proposed bounded waveform staircase

The C module `c/k6/mod/apc_staircase.c` is inert unless an
enabled build explicitly acknowledges dummy load and independent
watchdog. It programs **100, 90, 80, 70, 80, 90, 100 percent**,
each held 100 ms, with a maximum firmware TX lease of 1650 ms.
All writes are made through `apc_set()`, never directly to
REG_36, and the C module independently checks the readback.
A user key press or power-switch-off requests abort. The
module always releases TX and attempts to restore the 100%
target before leaving.

For this hardware the desired register sequence is:

    0xB4A2, 0xA2A2, 0x90A2, 0x7EA2,
    0x90A2, 0xA2A2, 0xB4A2

This is only the requested **register sequence**. No real RF
staircase has been measured. Cortex-M0 has no linked general
integer division library in these PIC modules, so the constant
denominator 100 uses an exactly verified reciprocal multiplication
over the valid 8-bit target range.

## Firmware build artifact and recoverability

A fresh isolated Aura checkout was created to avoid mutating
other Flux working trees or the existing generated firmware
source. The isolated test build omitted the irrelevant designer
theme installer overlay, which assumed an older cable-library
implementation, and used the existing locally pinned Sotto
library via a link in the isolated checkout.

New opt-in build:

    ~/flux-bench/tx-injection-20261122/apc-lab/build-isolated/flux-apc-b-lab.bin
    New firmware build ID: 424b8b573e0fdb8f
    SHA256: 9954ecb04b83a047969ebaa576137c4346ec6abae55ea5e20113beef73c13c70
    Size: 105432 bytes

The **existing running firmware** advertised build ID
`95d6b260d4122287`. A matching, independent rollback
firmware image was located:

    ~/flux-bench/tx-injection-20261122/superset-remoteui/flux-tx-superset-remoteui.bin

The original SPI includes radio-specific calibration and
installed module state and must be backed up separately from
the internal MCU firmware image. The slot-30 original signed
envelope was preserved in the physical fingerprint test.
The fresh **full 2-MiB SPI backup completed**:

    ~/flux-bench/tx-injection-20261122/apc-lab/spi-pre-apc.bin
    Size: 2097152 bytes
    SHA256: 4533e49c7cc20dc37c83704d057613b38af39378af44da863435ab1a8d54d54b

The running firmware's build ID exactly matches the independently
saved rollback image, while the experimental image reports its own
new ID. These are exact binary image identifiers, not proof that
restoring it would recover from all bootloader failures.

**No APC firmware was flashed in this checkpoint.**
The new firmware compile is not itself a guarantee of
device-level safety, PA linearity, or correct RF output.

### Compiled-image emulator acceptance: further work required

The first comprehensive existing live-image simulator run on the new
APC image completed with **8 passed, 10 skipped, 2 failed**.

- The cooperative token-cancellation test fails on **both**
  the candidate and the exact running rollback image; it is
  inherited and not uniquely caused by the APC patch.
- In the PA15 physical-switch simulation, the new image correctly
  refuses to advertise `standby_idle`, but **writes a byte to
  simulated SPI at 0xE000** during switch-off, whereas the
  exact running rollback image passes without changing SPI.

The latter is a **persistent-state behavioral difference** even
though the immediate idle safety guard worked. It needs independent
source/profile reconciliation before flashing. No attempt was made
to ignore the failing tests or call this firmware ready for use.

Simulator log:

    ~/flux-bench/tx-injection-20261122/apc-lab/compiled-emulator-tests.log

The existing running radio stayed unchanged and continued to report
TX idle, RX at 144.390 MHz on both VFOs.

## Validation and limitations

The opt-in API patch is tested against a pinned generated
Rust source copy for append-only ABI layout, idempotence,
unknown-source refusal, exact silicon/watchdog/state guards,
and enforced readback. The C module is compile-checked
against the same API layout and refuses unacknowledged
builds. The earlier physical fingerprint has **11 passing
tests**; the follow-up combined APC source checks have
**30 passing tests**.

Still open:
- Resolving the new PA15 SPI persistence difference and verifying
  the intended existing radio settings/profile across builds.
- End-to-end compiled-image API27 callback simulation,
  including firmware TX lease and independent watchdog.
- Loading an API27 module in a verified cable-only
  firmware without changing unknown radio module data.
- Measured RF target-to-amplitude mapping and 10–90%
  response times, overshoot, and harmonic/spurious behavior.
- Whether the PA's APC loop works at even audio-range
  update rates, let alone sample-rate envelope modulation.
- Recovery and rollback checks after an actual flashed
  firmware experiment.

## Next discriminating work

1. Finish compiled-image emulator checks and verify the
   new image's capability record advertises API level 27.
2. Confirm the original full SPI backup, exact running
   rollback firmware image/build ID, and a known stock
   bootloader recovery mechanism.
3. Verify the temporary module installer recognizes
   **level 27** rather than hard-coding a level-3 envelope.
   Require the exact installed firmware build ID.
4. Only then perform **one** bounded 7-level staircase
   trial on the conducted dummy-load fixture, capturing
   local SDR IQ with fixed gain.
5. Analyze every plateau (relative amplitude and power),
   transition edges, PA settling, RF-on/off leakage,
   spectral spreading and the relationship of stage
   timing to the module's reported status.
6. If the APC loop is slow, nonlinear or generates
   unacceptable spurs, reject it as a waveform-rate
   amplitude actuator and pivot to a physically separate
   RF amplitude modulator or external I/Q stage.
7. Continue independent MICIN, synthesizer, tone, subaudio,
   native-FSK and PA/CW work in the master TX roadmap.

**Arbitrary RF** remains the goal; demonstrating a calibrated
APC setpoint and opt-in callback is only preparation.
