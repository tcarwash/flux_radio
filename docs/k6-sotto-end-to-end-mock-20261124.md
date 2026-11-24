# Sotto-1200 end-to-end mock voice link

**Scope:** fully offline K6 mock. This runs the actual experimental API29
Cortex-M0 Sotto *transmit* module in Unicorn, passing mock 8-kHz microphone
samples through the exact compiled low-rate codec into native 4800 2FSK FIFO,
simulated IQ/air, an independent real FSK decoder, C and Python FDV1 frame
validators, and the pinned **actual Sotto-1200 fixed-point speech decoder**.
The speaker sink consumes exact 20-ms PCM chunks. No physical radio, SDR,
serial port, bench lease, firmware flash, or hardware RF is accessed.

## One-command E2E run

From a checkout on main with the local Python/Unicorn test dependencies:

```sh
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_e2e_mock.py \
  --sotto-tree /path/to/experimental-Sotto-API29-worktree \
  --out-wav /tmp/sotto-rx.wav \
  --report /tmp/sotto-e2e.json
```

This exercises **install** (mock library slot), **select SOTTO** (real
installed-VFO predicate/cycle), **PTT/capture/encode** (actual ARM experimental
module and modeled API29), **RF transmit** (model register/FIFO control),
**radio receive** (independent sampled IQ and C wire decoder),
**decode** (real pinned fixed-point Sotto1200 C decoder plus 2-superframe
flush), **speaker** (60 contiguous 20-ms blocks for the standard 1.2-s
fixture), **de-key** and **uninstall / FM fallback**. No speech bytes come
from the encoder-side shortcut: the PCM is decoded from the codec bytes
recovered via the independent IQ receiver and exact wire framing.

The chosen Sotto tree must actually have
`c/k6/mod/sotto_live_tx.c`, which is still on the experimental live-module
branch and not the normal `main` checkout. The generated `sotto-rx.wav`
is 8 kHz mono PCM16; it is a *lossy reconstructed waveform*, not a promise of
bit-identical PCM with the microphone input. The native C encoder reference
must match all emitted codec bytes, and independent C/Python wire validators
must agree.

Use `--mic-wav FILE` for any private 8 kHz mono PCM16 test of 0.1–3.0
seconds in 10-ms multiples. The file is read locally; it is **not uploaded
or committed**. Use `--fixture triangle` or `--fixture mixed` otherwise.
The modeling flags `--adc-cycles`, `--spi-us`, `--spi-byte-us`, `--cn`,
and `--prefix-every` let us compare deterministic contention, noise and
documented 00F8 prefix cases. These parameters are not calibrated to
silicon.

## Current mock evidence (2026-11-24)

The 1.2-second built-in mixed-frequency fixture yielded:

- **Input** 9,600 PCM samples at 8 kHz.
- **ARM encoder + virtual air**: 30/30 exact 6-byte superframes, 6/6
  received FDV1 frames (BEGIN, 4 voice packets, END).
- **Independent C and Python receivers**: same complete session and payload.
- **Fixed-point native receiver**: 9,600 PCM samples (1.2 s), nonzero output,
  deterministic hash recorded in the JSON report.
- **Speaker sink**: 60 sequential 160-sample / 20-ms chunks, byte-exact
  against the native decoder.
- **Lifecycle**: no active TX on return, no buffer loss in the nominal case;
  module removal restores FM and prevents stale playback.
- **Model overload** ADC 5,745 cycles/sample + 34-us SPI read setup aborts
  as modeled, with no playable completed session. This is a diagnostic
  *failure* to optimize, not a valid transmission.
- **Verified 2-byte prefix anomaly**: independent 00F8-shifted RF path still
  recovers exact bits and plays exactly the same PCM.

## Important integration boundary

The mock binds the **existing** stable VFO identity `SOTTO TEST` to the
experimental **actual binary** named `SOTTO TX`. The real Aura
installed-mode registry currently recognizes `SOTTO TEST` (and opt-in
`SOTTO RFTEST`), but **does not execute** the live `SOTTO TX` binary
from the mode key/PTT path. This mock adapter models that missing connection
for offline development, not a firmware release. Likewise the native C
decoder is linked on the host: we have *not* proven end-to-end ARM decoder
execution and audio hardware playout on the physical K6. The KISS packet
dispatcher is a separate API27 facility, and Sotto voice must **not** be
advertised as a KISS packet provider.

The next firmware milestones are an explicit, qualified installed-module
identity plus mode-to-runtime invocation, an API29 receive/PCM playout path
with bounded jitter and erasure handling, and hardware ISR/SPI measurement.
The full `sim/k6emu` firmware boot/UI remains a separate emulator; this
harness models the mode selection using the same host predicates, rather
than falsely claiming to run the full Rust firmware.

## Mic-free physical RF option (separate queued job)

The repo already has `c/k6/mod/sotto_tx_fixture.c` with six fixed public
Sotto codec/FDV1 packets, including compile-time default **inert** safety.
A previous guarded physical trial recovered six packets and the exact
fixture without microphone capture (see
[the physical RF report](k6-sotto-modem-module.md#physical-fdv1-qualification-2026-11-23-local-evening)).
A new on-radio test could calibrate FIFO timing, deviation, timing/packet
loss and IQ capture using that **fixed synthetic fixture**, even with the
programming plug suppressing the microphone.

Do not run it by casually invoking the emulator command above. Physical
RF requires the [Flux queue](k6-research-experiment-queue.md), fresh
exclusive whole-bench lease, explicit operator-verified dummy load,
frequency/power/watchdog checks, canonical serial lock, independent SDR
capture and pre/post state hashes, with bounded TX duration and no retries
after ambiguous radio state. The earlier #33 KISS control-plane bench
sessions were *no-RF* and neither their lease nor their approvals can be
repurposed to authorize this distinct new RF experiment. No hardware was
accessed for this offline implementation.
