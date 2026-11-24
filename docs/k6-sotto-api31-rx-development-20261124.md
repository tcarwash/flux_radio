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
native C decoder in 40-ms codec frames and emits 20-ms speaker blocks.
The newer **paced** path holds a maximum of **4 PCM blocks (1,280 B)**
and **16 compressed frames (96 B)** rather than 16 PCM blocks (5,120 B),
a 3,744-byte reduction in explicit queued storage. Long sessions,
corrupt packets, PTT cancellation and exact PCM identity are tested.
This excludes 640 bytes of temporary codec output, native decoder
state and data-structure overhead; host demand-driven decoding is
**not** yet qualified for the K6's 20-ms speaker deadline.

A new **native Rust callback test harness**
(`tests/data/k6/sotto_rx4800_native_host.rs`) compiles the *unchanged*
production `sotto-fsk4800-rx.rs.inc`, not a rewrite of its logic, using
Rust's own test runner with a deterministic FD6818B FIFO/register
model. It exercises exact 33-word draining, snapshot restoration,
low-active PTT, timeout, key/cancel/power off, malformed pointers,
other-RF-owner preservation, and the complete six-packet dataset
independently recovered from SoapyFang virtual IQ. The Rust callback
**has executed natively and as a direct callback within the compiled ARM firmware**, but not through a normally launched module: real peripheral timing,
Cortex-M0 stack and full-board dispatcher remain open gates.

## Linked ARM firmware RX qualification and fast rearm

The opt-in API31 receiver now implements a fast rearm of consecutive 33-word
FSK frames. Following a completed frame, the receiver retains ownership of
the originally saved FD6818B register snapshot and keeps the radio in RX,
rather than restoring and reconfiguring all nine registers on every packet.
The next matching arm operation writes only FIFO clear (0x59), IRQ
acknowledgment (0x02), and FSK RX enable (0x59). Calling the pump again
without rearming returns -3 rather than returning stale packet data.
Explicit stop, PTT, cancellation, power-off, invalid buffer, timeout and
module return still restore the original radio registers. This uses the
existing half-duplex receive path; it neither keys nor accesses the PA.

The original ARM callback with full restoration recovered 5 of 6 Sotto
packets in the continuous virtual RF session: the final 1.8-ms gap was
too short for the simulated restore/reconfiguration interval. Fast
rearm lowered modeled MCU rearm from approximately 1.8 ms to 0.4 ms.
The exact linked ARM firmware callback now receives **all six packets
(198 FIFO words)** from the actual compiled ARM Sotto TX waveforms
through SoapyFang virtual IQ, with zero FIFO overrun/underflow and
exact agreement with the independently clocked RX oracle.

The reproducible test is tools/k6_sotto_rx4800_firmware_gate.py. It checks
that the ELF and firmware image bytes match, boots the real firmware in
K6Board/Unicorn, reads the registered API31 callback pointer from the
linked firmware table, and directly invokes that Thumb function using
a **test-initialized module context**. It then verifies the real B-chip
register traffic against SoapyFang's RFIC behavioral model, FDV1 CRC
and packet/session ordering, a simulated PTT press, chip-ID rejection,
finite timeout, PA exclusion and complete register restoration. The
matching-image refusal and ARM plus SoapyFang integration are also
covered by tests/test_k6_sotto_rx4800_firmware_gate.py.

A freshly rebuilt Base+Data API31 RX-only firmware image uses
**110,852 of 122,880 application flash bytes**, leaving **12,028 B**.
The reported measurements include only that RX-only development build,
not the integrated voice TX services, on-radio fixed-point RX decoder,
speaker service, or a complete installable Sotto module.

For the existing matching ELF/image, run this from the project root:

    ~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_rx4800_firmware_gate.py --image firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin --elf firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura --sotto-tree . --json /tmp/flux-sotto-rx31-firmware-gate.json

To run the opt-in pytest gate, set K6_SIM_SOTTO_RX31_IMAGE and
K6_SIM_SOTTO_RX31_ELF to those exact two matching paths. Ordinary
test runs skip the whole-firmware case unless the pair is supplied.

**Boundary:** The callback is directly executed as real compiled ARM
instructions and reads modeled physical register transactions; the
test does not call the actual km_run module dispatcher or prove real
K6 silicon timing. The test harness aligns the independently generated
transmitter's timeline with the receiver's boot clock and supplies
its simulated chip ID and VFO frequency. Actual installed VFO binding,
interrupt scheduling, audio PWM and MIC/FSK TX services remain open.

## Boundaries remaining for flashable half-duplex voice

1. Execute the now ARM-exercised API31 callback through the normal
   installed-module km_run dispatcher. Its direct ABI call passes
   full-board virtual RF; module installation and real VFO handoff
   are not yet validated.
2. Join the API28 resource, API29 finite FSK async TX and API30 mic
   callbacks into a **single append-only host**, then bind installed
   Sotto to `RX → PTT TX → RX`. Never expose stubbed callbacks to
   the live TX module as if supported.
3. Move the native fixed-point RX decode and 20-ms speaker PCM service
   into radio firmware or a loadable module, measure total SRAM and flash,
   qualify the paced ring and prebuffer against real 20-ms deadlines.
   The experimental Base + Data headroom
   does **not** establish the combined image fits.
4. Only after firmware and emulator gates pass, use Flux issue #30 for
   a new dummy-load qualification under the shared #11 bench lease.
   Existing HIL approvals cannot be silently reused.

See [SoapyFang voice lab](k6-soapyfang-sotto-lab-20261124.md) for
independent over-the-air modeled session and RX FIFO timing results.
