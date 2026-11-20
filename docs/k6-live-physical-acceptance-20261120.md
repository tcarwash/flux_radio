# Flux live serial: physical RX-only acceptance (2026-11-20)

## Device and preservation

- Physical K6 FD6818B, programming cable at `/dev/ttyUSB0`.
- Preflight: firmware `ce46aac935c96e97`, packed library protocol 5,
  26 indexed library objects, slot 26 empty, awake RX standby with
  VFOA 147.790 MHz FM wide and VFOB 147.180 MHz FM wide in memory mode.
- Complete **2 MiB SPI snapshot** before update: **60.724 seconds** at the
  negotiated streaming rate. SHA-256
  `cf6d50e678cee083ecaed463b28793e6c951ac27c3778e7bd9afc69aa79c830b`.
- Versus the previous snapshot, 214 bytes differed **only inside**
  packed KUL2 library physical storage. All 26 logical objects were
  intact and verified. Calibration and an independent old-format 4 KiB
  readback matched. Rollback image `ce46aac935c96e97` saved separately.
- All machine-readable test reports and protected snapshots are located
  on the development host at `~/flux-bench/live-acceptance-20261120/`
  (`preflight.json`, `spi-before.bin`, `rollback-ce46.bin`,
  `flash.json`, `postflight.json`, `aborted-read.json`,
  `observe-idle.jsonl`, `abandon-cli.json`).

## Updated image accepted on physical radio

The previously compiled, simulator-validated power-off guard firmware,
**build `f693147e70527a58`** (114,976 bytes), was explicitly flashed with
the legacy firmware bootloader; the normal live path was not used to
flash the MCU. The bootloader confirmed completion and restart. The
radio booted the expected build and passed the following **RX-only**
checks:

- Original 26 indexed logical library objects and calibration unchanged.
  Calibration SHA-256
  `0379f456f03d3fa13fdac0638d00ffab7a39749dc7dc9d19ea3d140711d24b05`.
  Independent 32 KiB physical flash spot matched the preflight backup.
- **65 consecutive live status samples** stayed in awake RX standby
  with unchanged VFOs and no reported TX. Battery held at **7.34 V**.
  MCU ADC/temperature-index telemetry remained responsive.
- Physical cable / real Hamlib NET rigctl: frequency `147790000`,
  mode `FM 15000`, `VFOA`, actual PTT status `0`, RSSI/STRENGTH
  and DCD readback all passed. Explicit `T 1`, `T 0`,
  `\set_ptt 1` and `\set_ptt 0` returned `RPRT -11` (unsupported).
  The bridge did not permit remote TX.
- **Three abandoned read-only long streams**: the host consumed only
  the first 256-byte frame of a 32 KiB download, remained silent,
  allowed the radio to restore 115200 baud autonomously, and then
  verified standby RX, unchanged VFOs and independent 4 KiB old-format
  readback. A fourth run using the reusable acceptance CLI also passed.
  This exercises a *lost host reader*, not physical USB disconnection.
- The new read-only observation CLI ran on the radio for two seconds
  with **8 valid status samples**, no reported TX or transition.

The radio was left running build `f693147e70527a58`, idle in RX.
The research worktrees were not modified.

## What is still unverified

This pass **did not physically hold the radio's PTT**, toggle its
power switch, disconnect its USB cable, measure emitted RF power,
or run native code while a hardware interruption occurs. Virtual PA10
PTT and PA15 power-off tests passed using the compiled firmware, but
the simulator is not independent RF safety evidence. A returned live
`standby_idle=false` value alone does not uniquely identify a power-off
transition.

The remaining gate requires an **operator physically at the bench**
with an appropriately rated dummy load, independent RF power monitoring,
a stable supply, and the preserved rollback image. The user/operator
must control local PTT, power and cable actions; the acceptance CLI does
not do these things.

To record operator-assisted transitions without causing a transmission:

```sh
python3 tools/k6_live_acceptance.py --port /dev/ttyUSB0 \
  --expect-build f693147e70527a58 observe \
  --duration 45 --interval 0.25 \
  --output ~/flux-bench/manual-ptt-power.jsonl
```

The observer logs each status (and each missing response as unknown)
for correlation with independent screen/RF notes; its operation does
**not** certify RF behavior. Perform held PTT and power-off actions
only with the local monitored setup, not remotely or automatically.

To repeat the separate, already accepted **read-only** lost-reader test:

```sh
python3 tools/k6_live_acceptance.py --port /dev/ttyUSB0 \
  --expect-build f693147e70527a58 abandon-stream \
  --backup ~/flux-bench/live-acceptance-20261120/spi-before.bin \
  --count 3
```

Until the supervised hardware tests pass, **remote PTT stays disabled**
and the Catalog native-code runner remains experimental and
explicitly opt-in. A cooperative cancellation acknowledgement is
not a hardware kill switch.
