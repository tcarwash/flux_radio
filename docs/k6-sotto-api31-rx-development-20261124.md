# Native K6 FD6818B 4,800-baud Sotto RX: isolated API31 development gate

2026-11-24. **Experimental software only.** No radio, serial port, SDR
transmitter, flash, dummy load or shared hardware lease was accessed.
This **does not** constitute an on-radio or two-way flashable Sotto release.

## What has been implemented and verified

The current main-compatible firmware's API24 `fsk_configured` allows a
special-case native 4,800-baud **TX** but not 4,800 RX. Its legacy
RX read path does not handle 33-word FIFO threshold draining, preemption
or register restoration; changing the meaning of API24 would break the
existing compatibility contract.

The opt-in **`K6_FSK4800_RX=1`** Base + Data firmware instead appends a
new `fsk4800_rx` callback after all existing levels 26/27/28–30, preserving
the scalar `mb_version` slot in the packet mailbox ABI. The first test
host advertises level 31 and uses **unavailable stubs** for 28–30; it
rejects transmitting from modules requiring those levels. Consequently
the already compiled Sotto API30 TX module is **not usable** against
this RX-only development host yet.

The new experimental callback has bounded operations:

- `op=0`: verify chip identity and RX ownership, save RF registers,
  configure direct 4,800-baud 2FSK, threshold 1–7 (default 4), and
  arm 33-word / 66-byte receive with a 5-second deadline
- `op=1`: non-blocking FSK FIFO pump into 66 bytes of checked module
  scratch; threshold flag drains bounded words, DONE flag drains final
  words; returns 0 pending, 33 complete, negative on refusal/error
- `op=2`: stop and restore pre-RX registers; safe to repeat after abort

The path never calls PA, keys RF, steals hardware timers or modifies
user SPI flash. PTT/key/cancel, module-return cleanup, TX takeover
and deadline are fail-closed. RX FIFO/IRQ register values follow
`tests/data/k6/fsk_rx_emu.c` and SoapyFang; **FD6818B hardware behavior
is not yet demonstrated**. API31 is not available in default firmware.

**Independent read-only Antigravity audit:** `9cebef9b5bd0` using
`gemini-4-hydrogen-medium`, no code edits or tests. Its ABI and
resource recommendations were independently checked against source.

## Compiled build / emulator evidence

The isolated build was run with:

```sh
K6_FSK4800_RX=1 K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 \
  sh firmware/k6-aura/build-sotto-data.sh \
  /tmp/flux-sotto-api31-data-20261124
```

It generated a **110,740-byte** ARM firmware image, with **12,140 bytes**
remaining under the 122,880-byte application budget. The experimental
image was booted for **192,000 emulated instructions** with **no Unicorn
firmware fault**. Those measurements are build-specific and do not include
the resident Sotto decoder and audio output service. The RX callback was
compiled into the image but has **not yet been directly exercised through
the complete Rust-host radio module dispatcher**.

The separate actual ARM diagnostic consumer
`tests/data/k6/sotto_rx31_call_emu.c` compiled to **1,440 bytes**.
When its experimental API31 callback was scripted using the *actual
66-byte physical packets* recovered by the SoapyFang RFBackend and
Cortex-M0 FSK polling fixture, it decoded a valid BEGIN/VOICE/END Sotto
session (6 packets). Its tests refuse a bad CRC, a missing END,
a simulated API failure, PTT exit, and a level-30 host. This tests
**C consumer semantics with real received IQ-derived packet bytes**, not
the newly compiled Rust callback or physical radio.

The incremental decoder `tools/k6_sotto_rx_stream.py` uses the pinned
native C decoder in 40-ms codec frames, emits 20-ms speaker blocks,
bounds queue occupancy to 16 blocks (5,120 bytes), and drains between
up-to-eight-frame packets. Its recovered PCM is bit-exact to batch
decoding, and PTT/queue overflow drops old audio. The ring and decoder
memory still need **on-K6 SRAM/stack qualification**.

## Boundaries remaining for flashable half-duplex voice

1. Execute and instrument the newly compiled **Rust API31 callback**
   with the real firmware image under Unicorn and SoapyFang's FSK FIFO,
   checking register writes/reads, PTT cleanup and long idle sessions.
2. Join the API28 resource, API29 finite FSK async TX and API30 mic
   callbacks into a **single append-only host**, then bind installed
   Sotto to `RX → PTT TX → RX`. Never expose stubbed callbacks to
   the live TX module as if supported.
3. Move the native fixed-point RX decode and 20-ms speaker PCM service
   into radio firmware or a loadable module, measure total SRAM and flash,
   and shrink the ring if needed. The experimental Base + Data headroom
   does **not** establish the combined image fits.
4. Only after firmware and emulator gates pass, use Flux issue #30 for
   a new dummy-load qualification under the shared #11 bench lease.
   Existing HIL approvals cannot be silently reused.

See [SoapyFang voice lab](k6-soapyfang-sotto-lab-20261124.md) for
independent over-the-air modeled session and RX FIFO timing results.
