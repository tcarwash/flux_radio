# Flux live digital voice: guarded L64 scheduler + SPI-table slim Sotto MCU integration

Date: 2026-11-23. **OFFLINE integration milestone**, branch
`research/live-dv-guarded-segment-scheduler-20261123`, isolated worktree
`/storage5/dev/flux-live-dv-scheduler`.

**No radio/SDR/USB/PA/PTT/MCU/SPI flash hardware accessed.**
This is NOT yet a flashable firmware image or live on-radio digital
voice. It adds a working, bit-exact *C packet scheduler* and shrinks
the ARM codec code/data sufficiently for the next on-device experiment.

## Why this work matters

Independent prior physical tests proved:
- [FSK-13 guarded native-FSK PHY](https://github.com/tcarwash/flux_radio/blob/research/live-dv-fifo-rootcause-20261123/docs/k6-fsk13-guard33-sotto-physical-pass-20261123.md):
  8/8 byte-exact Sotto L64 frames over K6 radio RF, with `0x00F8`
  prefix insertion occurring on alternate frames. The guard word
  absorbed the displacement; 0/4096 packet bit errors.
- [FSK-14 real K6 microphone RF](https://github.com/tcarwash/flux_radio/blob/research/live-dv-fifo-rootcause-20261123/docs/k6-fsk14-real-mic-guard-physical-pass-20261123.md):
  previous PA11 mic capture, host Sotto encode, real K6 RF TX, RTL-SDR
  recovery, host Sotto decode. All 4 microphone-derived packets
  CRC-exact, 0/2048 bit errors and 26/26 original codec superframes.

The [independent live sampling/cycle model](k6-pa11-sotto-live-sampling-cycle-and-spi-burst-audit-20261123.md)
shows why simply adding `slim_encode10` to the old 200-turn PA11
busy loop **cannot** work: ~96MHz equivalent sampling by itself
leaves practically no CPU for the codec/SPI. An interrupt-driven
90-turn candidate remains hypothetical until physical sampling/codec
concurrency validation. The separate sample-rate agent owns that
research; this branch does not duplicate or preempt its hardware.

## Implemented zero-allocation packet scheduler

`c/k6/mod/dv_live_guard.h` is a freestanding C header for the radio's
Cortex-M0, with no heap, static mutable globals, libc or hardware
dependency. The caller owns its **204-byte** `dv_live_t` state in the
radio's scratch arena. `c/k6/mod/dv_live_guard_m0_probe.c` exports an
ARM-compatible explicit-state ABI, not a radio module.

- Consumer input: one genuine **6-byte Sotto-1200 superframe** from
  `slim_encode10` every ~40ms after codec lookahead.
- Group eight codec superframes = **320ms of speech** and create a new
  complete **64-byte Flux L64 message** with `magic=0xA9`,
  depth=1, packet sequence 0, explicit total=8 and increasing
  8-bit message ID (112,113,114...), original CRC16-CCITT-FALSE and
  PN9 whitening. This addresses *unknown talk duration* without
  altering the existing legacy L64 transport. A final partial
  message holds total 1..7 and correct zero padding.
- Append the **33rd, sacrificial 16-bit word `0xA55A`** so the
  physical RF frame is 66 bytes (`REG_5D=0x4100` in the eventual
  RF driver). This uses the proven FSK-13/14 single-word prefix
  mitigation. All 33 words can be fully preloaded before RF starts;
  no unproven IRQ or sustained FIFO refill is required.
- Bounded **two 66-byte packet slots** plus 48 bytes of pending
  compressed speech and control state; no silent FIFO overflow.
  On a full queue, the eighth incoming codec frame is rejected
  *without consuming it* until a packet drains. The caller must
  hold/retry this codec frame safely or abort if backpressure exceeds
  its PCM/audio capacity; this does not automatically guarantee
  realtime service.
- `peek_words` returns exactly 33 FIFO words with little-endian
  packing. `tx_done(1)` consumes a packet only after independently
  observed RFIC completion; `tx_done(0)` freezes the queue on
  error. No automatic TX retries.
- `end` flushes a 1..7-frame partial group. Exact full eighth
  group needs a separate explicit **end-of-talk** control protocol
  for interoperability; current radio-only carrier drop may be an
  initial cue but is not a guaranteed receiver protocol.
- 8-bit message-id epoch **refuses wrap** instead of confusing the
  receiver after 256 chunks. Need session/epoch negotiation and
  lost packet handling for sustained calls.
- **No IRQ ever mutates** the packet queue: the timer-driven PCM
  ring and the encoder foreground must have separately qualified
  ownership, preemption and overrun handling.

Actual ARMv6-M compilation:
`dv_live_guard_m0_probe.o` is **768 bytes code, zero bytes
`.data`/`.bss`**. Static state is **204B allocated by caller**,
not a normal `.bss` global (not usable by Flux K6M modules).

## Exact real slim codec + packet integration test

`tools/k6_live_dv_guarded_host.c` links the genuine unmodified
`sotto_fx_slim.c` and feeds 80 audio samples every synthetic
10ms block through the real `slim_encode10` API.

On the original saved K6 microphone audio (PRIVATE, not committed):
- **104** successive 10ms blocks, **26** genuine codec superframes,
  including two end-of-file `slim_flush` frames.
- Compressed stream SHA256:
  `8c20829a11ce6c3f22162b6dcc15e0f4378bbdccfbdf967a6eb30b0bd4646ba5`,
  exactly matching the earlier independent slim reference.
- Four fully guarded 66B RF frames generated at message IDs
  **112, 113, 114, 115**, containing 8,8,8,2 superframes.
- All original 64-byte payloads are **byte-for-byte identical**
  to `modem.pager.profile('L','1200',depth=1,short_last=True)`
  on the SAME compressed voice bytes: exact CRC, PN9, reserved
  bytes and headers, with correct 2-byte guard after each packet.
- Whole 4x66B output SHA256:
  `923e1956c18ecfd75cdb5e8778ea6dbff93b55970a95f330054aa4cdf504f537`.
- Original source-release packet readiness 370/690/1010/1040ms.
  A strictly **offline optimistic 152ms/packet** serialized radio
  service model gives start times 370/690/1010/1162ms and completes
  522/842/1162/1314ms. This is **not** measured K6 latency or
  proof that PA11 capture can proceed during transmission.
- A separate synthetic audio fixture passes byte-exact C-vs-Python
  CRC/whitening. Adversarial C tests confirm bounded queue
  backpressure, error-free retry after a successful TX completion,
  abort-on-error, partial final segment, no 8-bit ID wrap, and
  refusal to overwrite private audio files.

Original compressed voice, guarded RF frames and CSV timeline stay in
`/home/tyler/flux-bench/micvoice-20261123/live-dv-guarded-c-20261123/`
(mode 0700 directory, mode 0600 output files).

## K6 codebook/IVF/DCT externalization

The earlier standalone ARMv6-M `-Oz` slim codec ELF embedded the
**30,720-byte** four-stage Sotto codebook plus 3,000+ bytes of IVF
search tables and an **800-byte** DCT table: total **41,210B**
before the live-radio sampling and packet scheduler. That does not
fit the **15,408B** Flux module arena.

We reproduced the actual **upstream fmsotto** table-externalization
strategy and adapted it as a reusable backend:

- `c/k6/mod/dv_live_slim_dct.h`: exact 21-entry integer DCT
  formula used by upstream `fmsotto.c`, saving **652B** in actual
  ARMv6-M `-Oz` linked comparison (41,210B → 40,558B with
  only the DCT transformation). Real K6 compressed bits unchanged.
- `c/k6/mod/dv_live_slim_spi.h/.c`: codec compilation hooks
  `SLIM_CB_FETCH`, `SLIM_IVF_CEN`, `SLIM_IVF_CELL`,
  `SLIM_DCT`, read-only callback; no platform SPI driver,
  radio/IQ access, heap or mutable static state in that backend.
- Shape codebook reads follow the existing K6 SPI codebook region
  `0x100020`. IVF reads use the *existing upstream K6SC v1 deck*
  at a caller-attested signed library slot. The 4,182-byte deck is
  validated on initialization via v1 header plus the established
  32-bit sum; read errors are sticky and must abort TX.
  **This sum is NOT authentication**; the outer radio module must
  independently verify the signed deck/slot before using it.
- `tools/k6_live_dv_spi_deck.py` reproduces the upstream public
  deck from `slim_cells.h` with SHA256
  `5d05fa9fb0194eee04356c3cb4b15471c780d0a915ee67063ed91a54e64ce9f6`.
- `tools/k6_live_dv_spi_model.c` feeds the real encoder through
  the same external table callback using a **host model** of SPI.
  It produces the **exact same 26/26 compressed frames and all
  four byte-exact guarded RF packets**, with synthetic and
  real K6 audio; corrupted/invalid decks are rejected **before
  any encoded packet output**.
- `dv_flash_active()` must be provided by the actual K6 module
  with a **caller-owned scratch context**, for example via the
  upstream proven SRAM-remapped vector pointer. The host model
  holds its own state only in the host application. This branch
  does **not yet implement** a true B-chip hardware context,
  ISR-safe direct SPI1 reader or 8kHz timer ISR.

### Real Cortex-M0 linked memory probe

`tools/k6_live_dv_spi_m0_size_probe.py` builds the full actual
ARMv6-M slim codec + offload hooks + guarded L64 packetizer with
`-Oz`, dead-section elimination, and all exported APIs retained.

<details>

**The ELF is explicitly NON-FLASHABLE**. Its `dv_flash_active()`
returns a NULL context solely to satisfy linker symbols; calling
the codec without a real signed K6 SPI binding would fail. This
ELF is NEVER a valid radio module.

</details>

Measured linked result:

| Resource | Bytes |
| --- | ---: |
| ARMv6-M slim + SPI adapter + packetizer + runtime, read-only data | **8,000** |
| ELF `.data` + `.bss` | **0** |
| Codec state (upstream existing model) | 3,148 |
| Caller-owned bounded packet queue | 204 |
| ARM emulator stack high-water (prior real-mic trace) | 704 |
| Three candidate 160B/10ms PCM buffers | 480 |
| **Remaining from 15,408B Flux module arena** | **2,872** |

This **2,872B is an optimistic engineering budget**, not measured
spare RAM on the actual K6. Future ISR state, SPI scratch, context,
radio register snapshot, PA watchdog, interrupts, alignment,
return-address stack and per-module runtime still consume memory.
The source of the real 704B stack/cycle estimate is the distinct
Unicorn emulator report, not this ROM-size build.

## Critical integration blockers and next physical tests

1. **PA11 sample producer:** the old physically tested 200-turn
   polling sampler is ~96MHz; it cannot coexist with the codec.
   The timer-paced **90-turn PA11 8kHz** sampler remains untested
   for real mic gain, jitter, overrun, ISR preemption and noise.
   Use a receive-only, PA-off physical test under a NEW exclusive
   queue lease, not while the separate #9 FIFO agent owns radio.
2. **Real SPI binding:** port the existing fmsotto direct SPI1
   reader and signed K6SC deck enumeration to the current B-chip
   Flux module ABI. Qualify codebook lookup burst timings and
   interrupt safety, especially the measured worst **191
   codebook fetches in one 10ms call** (6.494ms modeled at
   34us/read) plus IVF fetches. The host flash model validates
   content/layout, not K6 peripheral timing.
3. **MCU integration:** combine actual timer ISR PCM ring,
   caller-owned Sotto state, SPI callbacks, this guarded FIFO
   queue, and native FSK TX setup/restore from proven FSK-13/14
   in a separately precommitted, finite **receive-only
   codec-probe** before any autonomous DV TX.
4. **Radio RX and playout:** implement guard-aware native-FSK
   on-radio RX, original CRC/Sotto decoder, speaker PCM playback,
   jitter/erasure handling and an explicit session/EOT protocol
   for real half-duplex calls.
5. **No unconditional on-air EOT:** current `msg_id` only permits
   at most 256 chunks per epoch; a bounded call or protocol
   extension is required before arbitrary duration.

The currently verified result is a portable, exact C **codec →
existing L64 packet → guarded FSK data** offline pipeline, with
a promising MCU memory footprint. It does NOT justify claiming
a running 8kHz combined sampler, actual radio-to-radio live DV,
or a proven >64-word native-FSK FIFO refill.

## Reproduction (all commands hardware-free)

```sh
python3 -m pytest -q \
  tests/test_k6_live_dv_guard_pipeline.py \
  tests/test_k6_live_dv_spi_model.py

python3 tools/k6_live_dv_spi_m0_size_probe.py \
  --out /tmp/flux-live-dv-memory-probe-new

python3 tools/k6_live_dv_spi_deck.py \
  --out /tmp/flux-live-dv-K6SC-deck-new.bin
```
