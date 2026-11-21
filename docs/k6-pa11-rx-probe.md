# PA11 receive-audio capture probe (FD6818B 0x4829)

A bench-ready, receive-only diagnostic: while the radio is already in RX, `c/k6/mod/pa11rx.c`
listens to the existing AF output through PA11 run as a one-pin delta-sigma converter
(`c/k6/mod/pa11adc.h`) and reports count, mean, variance, min/max, elapsed time and a rough
tone hint over the programming-cable serial log. Feasibility probe for future digital modems
(AFSK first); not a modem, not a firmware release, no TX path.

## What it does

1. Checks API level 2, saves GPIOA PA11 mode/speed/output latch, the PB2 speaker-amp bit,
   and reads REG_47/REG_48 for the report (never writes them).
2. Sets PA11 speed to maximum, switches the speaker amplifier off (the converter's pulses
   reach it), drops two unsettled samples, then takes 32 samples at hold 3, w 90.
3. Prints `pa11rx start`, the untouched `r47`/`r48`, then `n`/`mean`/`var`, `lo`/`hi`/`zc`
   (zero crossings) /`lag` (lag-1 correlation numerator, signed), and `t`/`tonal`/`done`.
4. Restores PA11 mode/speed/latch and PB2 on every exit, including key-abort (`pa11rx key`,
   returns -2) and timeout (`pa11rx timeout`, returns -5). Success returns 32.

Sample rate and levels follow the 0x4819 measurement (17.8 kHz, rest ~0.68 full scale at
hold 3); none of it is proven on the 0x4829, so the probe reports raw counts only.

## What it deliberately does not do

- No TX/PTT, no register 0x30 write, no PA enable, no transmit cue of any kind.
- No FD6818 register is written, including the RF AF-output select: the 0x4819 select map
  (`docs/k6-afsweep.md`, REG_47 bits 11:8) is **not** assumed on the 0x4829, so the AF
  routing is left exactly as the running image set it. A capture therefore reflects
  whatever the current image routes, which is a known limitation, not a calibration.
- No calibration, SPI flash, firmware base build, module ABI, or CPU/SPI changes.

## Bench setup

- Radio already receiving (any mode/frequency the image is parked on); programming cable
  attached for the serial log; speaker state does not matter (the amp is off for ~ms).
- A quiet channel first (muted/weak-signal baseline), then a known tone or voice: the two
  runs must separate. Upstream's 0x4819 numbers (muted noise 0.00017 FS vs hiss 0.051 in
  300-3000 Hz) are the shape to look for, not a pass threshold on this chip.
- Read back with `tools/k6_micprobe.py`-style serial logging; keep the capture short and
  the radio attended (any key aborts cleanly).

## Risk

Experimental: RAM-only module, API level 2 (no TX/flash surface), bounded run (~ms of
conversion plus API calls, 15 s software timeout), with pin/amplifier restore on normal,
key-abort and timeout exits. Residual risks: PA11 drive pulses touch a shared audio node
(the speaker amplifier is disabled during capture); unexpected faults can bypass cleanup,
freeze the module, or require a device power-cycle. The software timeout cannot recover
from a hard fault or a hung sampler. Do not run without a known recovery path.

## Limits

- On this tested 0x4829 board, FM AF reaches PA11 when squelch opens;
  exact filter response, level calibration, and support on other boards
  remain unproven. The initial 32-sample probe is dominated by settling.
- Cable caveat (upstream, 2026-11-20, `docs/k6-mic-probe.md`): every prior mic/PA11 run had
  the programming cable in the jack, and the cable sits on the mic line; negatives from
  those runs say nothing definitive, and any new run must record cable in/out.
- 32 samples at a nominal ~17.8 kHz is ~2 ms of audio: enough for level/hiss checks, not
  for decoding anything.
- `lag`/`tonal` is a hint, not a detector: zero crossings plus correlation sign only.

## Expected result

- Muted/quiet channel: mean near the converter rest level, variance near zero, `zc` ~0.
- Live receiver hiss or tone: clearly larger variance and min/max spread than the quiet
  run, `zc` > 0. Muted vs active must separate by an order of magnitude or the path is
  dead on this chip.
- Return-state check after every run: PA11 back to its prior mode, PB2 back to its prior
  state, no FD writes in the log, `done`/`key` line present.

## Physical validation (initial)

On 2026-11-20 the initial 32-sample module executed repeatedly on the FD6818B
radio over live serial without a flash or SPI write. After each run the radio
returned to idle RX. The quiet channel produced a very repeatable 0..1360
sample range; on 162.550 MHz with squelch open the range expanded to
0..2443. These are dominated by startup settling, **not** representative
steady-state PCM or an audio SNR measurement.

The follow-up 1,024-sample pa11pcm module with 4,096-sample warmup resolved
this transient and demonstrated a strong quiet-versus-live audio separation.
See docs/k6-pa11-pcm-lab.md for raw count ranges, timebase, and limitations.
Key-abort physical behavior is not yet separately validated.
