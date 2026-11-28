# Sotto live ARM microphone -> FSK -> virtual IQ, 2026-11-24

**Offline development only.** The K6 Base+Data API28–31 image, compiled
Cortex-M0 live Sotto module, codec SPI codebook/deck, microphone TIM17 ISR,
software packet queue, and async-FSK TX API are executed in Unicorn.
SoapyFang virtual RF/IQ and an independently compiled ARM receiver verify
actual emitted packets. No physical K6, cable, SDR, PA, or microphone is used.

## Build

The original combined host was a `K6_RAM0=stack` 11,776-byte arena. It fits
firmware flash but **cannot fit** the live module (9,756 bytes with optimized
in-place packet sealing) plus its ~4,086 bytes of streaming/codec scratch.
The following experimental profile enables the 14,976-byte expanded arena,
with `k6_sotto_ram0_state` moving selected host static state to protected RAM0.
The normal RAM0 stack/cable build is not changed by this gating.

Run from the Flux repo root in an **isolated worktree** with a generated Aura
checkout; the explicitly requested `K6_AURA_RESET_GENERATED=1` affects only
that generated checkout, not main or physical hardware:

```sh
K6_AURA_RESET_GENERATED=1 K6_RAM0=1 K6_LIVE_SERIAL=0 K6_REMOTE_UI=0 \
K6_SERIAL_TRANSPORT=1 K6_USER_STORE=1 K6_MEMORY_BANK=0 \
K6_CONFIG_FSK=1 K6_FSK_RX_SERVICE=1 K6_LEAN_TXDOG=1 \
K6_NATIVE_FM=0 K6_FONT_SUBSET=1 K6_RASTER_BOOT=1 \
K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 K6_FSK4800_RX=1 \
K6_SOTTO_RESOURCES=1 K6_SOTTO_ASYNC_FSK=1 K6_SOTTO_MIC=1 \
K6_AURA_FEATURES=blowfang-host,fd6818b,lean,spilib,txdog-quiet \
K6_IMAGE_NAME=flux-fd6818b-pack \
sh firmware/k6-aura/build-fd6818b.sh
```

`K6_LIVE_SERIAL=0` is an isolated, cable-only virtual-voice qualification
profile, not the default Base+Data release. The final VFO/mode and production
keyed installs must be integrated separately.

The build's memory audit must PASS unchanged, with 14,976-byte arena, at
least 2,780-byte main-stack reservation and no RAM0 overlap. The live module
is built by `tools/k6_sotto_module.py` using pinned Sotto codec sources, and
staged through the actual C CRC32 development cable loader and normal
Rust/C compiled module runner. The runtime's `scratch()` is checked against
`sizeof(sl_engine_t)+slim_enc_bytes()` before any TX.

## Execute

```sh
ELF=firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura
IMAGE=firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin
~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_live_arm_gate.py \
  --elf "$ELF" --image "$IMAGE" --sotto-tree . --blocks 80

K6_SIM_SOTTO_LIVE_ARM=1 K6_SIM_SOTTO_LIVE_ELF="$ELF" \
K6_SIM_SOTTO_LIVE_IMAGE="$IMAGE" \
~/.local/share/flux-rf-venv/bin/python -m pytest -q \
  tests/test_k6_sotto_live_arm_gate.py \
  tests/test_k6_sotto_live_packet_parity.py
```

The test injects active-low virtual PA10 PTT, synthetic PA11 PDM and the
real compiled TIM17 interrupt at an 8-kHz modeled cadence **while** the
ARM encoder is running. PTT is released after 8, 32 or 80 ten-millisecond
PCM blocks. It runs the real Sotto encoder and async-FSK queue to completion,
observes TX-done, then decodes the emitted virtual IQ independently, requiring
FDV1 BEGIN/VOICE/END session completeness. The actual recovered 6-byte
codec frames are decoded with the pinned native Sotto fixed-point decoder
and paced through a bounded host 20-ms speaker queue, byte-identical to batch
decoding; this **does not** exercise a physical radio speaker. It checks ISR cycle cost and PCM
backpressure, no dropped blocks, no FIFO under/overrun, no outstanding PA/TX,
and unchanged virtual SPI codec resources.

The companion native packet parity test verifies 2,097 exact BEGIN/VOICE/END
packet comparisons with the unmodified public packet encoders, plus a
144-segment epoch with queue wrap and backpressure.

## Still outstanding

- Real PA11 microphone audio and ISR load/timing on a physical K6.
- Full Sotto receiver/decoder -> PWM speaker playout in normal installed mode.
- Installer authentication/signing and production SPI packaging.
- Full Base+Data/Designer live-serial/stack coexistence and real radio RF.

The emulator is not an RF calibration or audio quality measurement. It proves
**simulated live TX**, not a working two-way voice product.

## Updated full-service profile and two-ARM virtual link

The optional baseline above qualifies a reduced emulator-only firmware. A
second image using `K6_RAM0=1 K6_LIVE_SERIAL=1 K6_REMOTE_UI=1
K6_MEMORY_BANK=1` (with all the same API28–31 flags) **also builds and passes
the unmodified RAM0 audit**. At this checkpoint:

| Build measurement | Full-profile result |
| --- | ---: |
| Firmware flash | 122,180 / 122,880 bytes |
| Flash remaining | 700 bytes |
| Module arena | 14,976 bytes |
| Main stack reservation | 2,796 bytes (required >=2,780) |
| Free protected RAM0 | 88 bytes |
| Compiled live module | 9,756 bytes |
| Module scratch reported at entry | 4,156 bytes |

The live-serial path is **compiled in**, but no physical UART cable is attached
or active in this test. A real shared PA11 microphone/UART exclusion remains
the required behavior when live serial is connected.

For a genuine two-radio virtual link, the live ARM sender's actual 4,800-baud
bursts are passed through `VirtualAir` to a **second booted, independent K6
ARM image**. The second radio loads `tests/data/k6/sotto_rx31_call_emu.c`
through the normal C cable loader, enters the Rust/C module dispatcher and
calls the real API31 4,800-baud receiver. It validates FDV1 session structure
in compiled Cortex-M0 C code, returning the exact number of received packets.
A separate host IQ decoder is retained as a cross-check; the virtual receiver
is tuned from the transmitter's actual frequency rather than the original
433.5 MHz laboratory fixture constant. The second radio is allowed a modeled
0.9-second acquisition window before the first RF burst.

After receiver session verification, the recovered compressed codec payload is
decoded using the pinned native Sotto fixed-point implementation and a bounded
host speaker model, compared sample-for-sample to independent batch decoding.
**This is not the K6 speaker peripheral or an ARM decoder CPU deadline gate.**

The full-profile image currently leaves only 700 bytes of flash. The on-K6
speaker output, production keyed installation, installable VFO/PTT handoff,
and hardware validation are explicitly not closed by these results.

## Regression record

All 29 Sotto-specific test files were exercised from the full-feature
byte-matched development ELF/firmware profile with the opt-in API28/29/30/31
and live ARM gates enabled: **181 passed, 2 skipped, 0 failed** on
November 24, 2026. This is a simulator/development-gate result, not an
installation, RF bench or hardware release sign-off. Main remains unchanged.
