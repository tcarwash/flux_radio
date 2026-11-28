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
the resident Sotto decoder and audio output service. That early build preceded
later direct ARM callback and complete normal module-dispatch tests,
described below; its reported flash size is historical, not the newest image.

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
**has executed natively, directly inside the compiled ARM firmware,
and through the full compiled module runner**. Physical peripheral timing
and end-to-end UART or on-device Sotto speaker processing remain open gates.

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

## Real cable-loader and normal module runner (2026-11-24)

The next executable qualification uncovered a subtle release blocker:
the Rust host advertised API31 and executed the linked API31 function,
but the separately built C module verifier still enforced the older
API27 ceiling (KM_LEVEL) and **rejected the otherwise valid level-31
module envelope as KM_BAD_HDR on upload chunk 26/26**. The C
library is built by tools/k6_build.sh, distinct from tools/k6_build.py.
Both C builder paths now raise the module-verifier ceiling to
KM_LEVEL=31 **only** when K6_FSK4800_RX=1, with mode-registry
and mailbox prerequisites. The parent Aura build now passes these
opt-in flags to the actual C library build. Ordinary firmware and
API27 packet transports keep their existing ceiling and ABI.

After the fix, the same **110,852-byte RX-only firmware** accepted a
real K6M **1,464-byte CRC32-tagged envelope** containing the actual
**1,440-byte compiled Cortex-M0 FDV1 consumer**. A new gate,
tools/k6_sotto_rx4800_dispatch_gate.py, calls the firmware's real
km_cable_packet() for **all 26 whitened, CRC-checked 64-byte module
chunks**, first confirming that a corrupted packet CRC is rejected.
The accepted envelope is byte-for-byte compared with RAM. It then
invokes the real Rust host run_module, which **initializes MOD_CTX
itself**, switches stacks through the actual C km_run_on_stack and
runs the module's real C k6_main via km_run. The test observes
**exactly one** normal module dispatch and **return code 6** from the
real module. The callback received **6/6 SoapyFang IQ packets,
198/198 FIFO words, zero FIFO underflow or overrun**, with no call
to the radio's enter_tx. After return, the host clears its module
context and restores its radio state. This is no longer a direct API31
debug invocation or an emulated stand-in for a Rust callback.

This is a **cable-only CRC32** development build. The CPU test
injects the packets into the **compiled C ingress function**, rather
than driving a complete UART/CPS cable session, installing in the SPI
library, or qualifying the **production keyed SipHash** authorization
path. The parser-only module validates BEGIN/VOICE/END but does **not**
decode speaker PCM on the KD32F328. Those distinctions remain
important when assessing whether a binary is flashable and *usable*.

For reproducibility (no real radio involved):

    K6_AURA_RESET_GENERATED=1 K6_FSK4800_RX=1 K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 sh firmware/k6-aura/build-sotto-data.sh /tmp/flux-rx31-offline-host
    ~/.local/share/flux-rf-venv/bin/python tools/k6_sotto_rx4800_dispatch_gate.py --elf firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura --image firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin --sotto-tree . --json /tmp/flux-rx31-dispatch-report.json

The K6_AURA_RESET_GENERATED flag explicitly resets only the disposable
pinned generated Aura checkout; do not use it on a non-disposable
worktree. The matching opt-in regression test is
tests/test_k6_sotto_rx4800_dispatch_gate.py, using the same
K6_SIM_SOTTO_RX31_IMAGE and K6_SIM_SOTTO_RX31_ELF paths as
the direct callback test. The two tests have **different scopes**,
and both remain useful for identifying failures at different layers.
No hardware, local serial port, SDR transmitter or shared bench lock
is used.

## Opt-in real API28 SPI resources (2026-11-24)

The same main-compatible API31 Base+Data research host now supports an
additional **opt-in K6_SOTTO_RESOURCES=1** service. It fills the existing
API28 resource_open/resource_read table slots *without moving or changing*
API26 provider discovery, API27 KISS mailbox or API29/30 placeholder slots.
Normal firmware and RX31 builds without this option retain the -1 stubs.

The real linked ARM callbacks read the fixed read-only **K6CB codebook**
at 0x100000 (32-byte header plus 30,720 table bytes) and an installed
**K6SC IVF deck** (4,182 bytes) from the existing logical library
index. The deck is located by kl_locate, including the packed
dual-directory **KUL2** layout, not by assuming slot * 16 KiB. A deck
open checks its K6M envelope type/slot, payload length and K6SC magic.
The codec is still responsible for verifying the *full* K6CB/K6SC CRC
before accepting bytes as trusted data.

Reads fail closed on unopened deck, other resource kinds, zero length,
over-256-byte requests, out-of-bounds offsets, non-SRAM buffer pointers,
invalid slot or missing header. A failed open clears the previous deck
handle, which also resets upon entering and leaving the real Rust
module runner. The service allows bounded codebook reads during TX
for a future encoder, but does **not** key or configure RF. It does not
erase or program SPI, install modules, or replace the existing loader.

A fresh real ARM build with both options enabled is **111,288 bytes**
out of the 122,880-byte app limit, **11,592 B headroom**. This is
**436 bytes above the previous RX-only build**, with API29 and API30
still unavailable. SHA-256 of the tested binary:
130529f6c39acd61d86ddd0ef45343ae662d044869e60810773f35af43e071be.

The independent opt-in acceptance gate
tools/k6_sotto_api28_resource_gate.py runs a real compiled C resource
consumer via the **CRC32 cable loader and normal ARM run_module stack
switch**, with virtual K6CB SPI and a CRC-protected packed KUL2 deck.
It checks actual K6CB/K6SC header and end ranges, null/peripheral
pointers, read limits, invalid slot handle reset, codebook/deck
read-only behavior and safe module return. A second *real compiled ARM*
consumer (tests/data/k6/sotto_codec_resource_crc_probe.c) uses the
unmodified Sotto encoder resource initializer from sotto_tx_codec_io.h
to verify the complete K6CB and K6SC contents, not merely headers.
The module succeeds on the canonical assets and fails closed if either
the codebook data or deck payload is bit-corrupted while its header
remains valid. Both negative paths preserve SPI and RF state.

The companion
tests/test_k6_sotto_api28_resources_host.py verifies overlay ordering,
idempotence and opt-in feature gating. Real radio, physical SPI,
keyed SipHash authorization, microphone and speaker remain untested.

Build/test entirely offline:

    K6_AURA_RESET_GENERATED=1 K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 K6_FSK4800_RX=1 K6_SOTTO_RESOURCES=1 sh firmware/k6-aura/build-sotto-data.sh /tmp/flux-sotto-resources-host
    K6_SIM_SOTTO_RX31_IMAGE=firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin K6_SIM_SOTTO_RX31_ELF=firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura K6_SIM_SOTTO_API28_RESOURCES=1 ~/.local/share/flux-rf-venv/bin/python -m pytest -q tests/test_k6_sotto_api28_resource_gate.py

## Opt-in real API29 PA-muted async FSK TX (2026-11-24)

The same Base+Data research host now supports **K6_SOTTO_ASYNC_FSK=1**,
which requires K6_SOTTO_RESOURCES=1 and the existing API31 receiver
configuration. This enables the pre-existing *append-only* API29
fsk_async callback without moving the API26/27 mode registry/mailbox,
API28 resources, API30 microphone slot or API31 receive slot.

**TX safety contract** (compiled Cortex-M0 host, not merely a mock):

- For level-30/31 Sotto modules in this **explicitly opt-in build**,
  tx_on enters normal licensed/band-locked FM TX **with its PA muted**.
  Other firmware builds and older modules retain their original TX
  paths. In the RX-only build, new-tail modules still cannot key TX.
- fsk_async op=0 accepts **exactly 33 aligned, module-arena u16
  words** whose final guard is 0xa55a. It checks the B-chip identity,
  active TX lease, module key/cancel/power state and existing FSK owner;
  snapshots the nine affected FD6818B registers; resets FIFO; loads
  33 words; checks for stale TX-complete; and **only then enables PA**
  and starts the transmitter. A second start is an error, not a refill.
- op=1 returns 0 while busy, 1 **only after the modeled hardware
  TX-done IRQ**, or -2 on a bad lease, state, PA or **700ms** finite
  burst timeout. Success immediately mutes PA and restores RF
  registers, but leaves the module's own TX lease active until tx_off.
- op=2 aborts and releases the lease; module return, tx_off and
  txdog timeout also call cleanup. Cleanup cannot recursively invoke
  the TX watchdog. No ISR, FIFO refill, unchecked PA transition or
  new writeable RF register API is introduced.

The new executable offline gate
tools/k6_sotto_api29_async_gate.py uses the **linked ARM firmware**
and normal C CRC32 cable loader/Rust run_module/ARM stack switch
rather than callback injection. It qualifies five compiled C modules:
a successful 33-word burst, explicit **RX -> TX -> RX** transition,
guard-word refusal **before any RF**, early abort after start, and a
**900ms delayed completion poll** that must fail its 700ms deadline
and release the transmitter while preserving normal RX.
It traces physical-model PA register writes to prove all **33 FIFO
words precede PA enable**, requires RF return to its observed normal
RX value and no active virtual transmitter after each case. The
successful modules' virtual IQ is independently sampled and decoded
by the native FSK Cortex-M0 receive fixture, which recovered each
66-byte transmitted payload **bit-for-bit** with no modeled FIFO
overrun or underflow. The fake-radio test does not key the actual K6.

A fresh **API28+API29+API31 development app** is **112,136 bytes** of
122,880, leaving **10,744 B** flash headroom (848 bytes over the
API28+API31 image). Tested binary SHA-256:
945613041eab4a21984566d8028e0e16deefbe799bc7f35f91288e554a012dc2.
The finite burst profile and completion timing are **model timing**,
not verified FD6818B hardware measurements. Production keyed
installation, actual microphone ISR, selectable mode and on-device
decoder/speaker remain outstanding. API30 remains an explicit -1
stub: this is **not a live two-way Sotto voice release**.

Rebuild with an isolated disposable Aura checkout and run the
opt-in gate using byte-matched ELF and application image:

    K6_AURA_RESET_GENERATED=1 K6_FSK4800_RX=1 K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 K6_SOTTO_RESOURCES=1 K6_SOTTO_ASYNC_FSK=1 sh firmware/k6-aura/build-sotto-data.sh /tmp/flux-sotto-async29
    K6_SIM_SOTTO_RX31_IMAGE=firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin K6_SIM_SOTTO_RX31_ELF=firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura K6_SIM_SOTTO_ASYNC_FSK=1 ~/.local/share/flux-rf-venv/bin/python -m pytest -q tests/test_k6_sotto_api29_async_gate.py tests/test_k6_sotto_api29_overlay.py

## Opt-in API30 PA11/TIM17 8-kHz microphone ISR (2026-11-24)

The experimental Base+Data host now also supports
**K6_SOTTO_MIC=1**, strictly dependent on the opt-in API29
FSK transmitter, API28 resources, API31 receiver and API26/27
mode/KISS tail. The module API30 slot is **reused in place**;
no earlier callback, KISS mailbox scalar or API31 offset moved.
Ordinary RX/packet firmware without the flag still has the original
unavailable API30 stub.

The port keeps the existing researched PA11 single-wire sigma-delta
sampler in firmware/k6-aura/sotto-api30-pcm-capture.rs. It uses
TIM17 at 8,000 sample interrupts/second (125us period), 48 PDM
turns per interrupt and a 4x80-sample 16-bit PCM ring (644 bytes
including volatile producer/consumer/offset/lost counters). The
module API provides read-only physical PTT status (op=3), begin
capture (op=0, only after a valid PA-muted TX lease and guarded
0x4829 RFIC route), stop (op=1), and sample ISR peak cycles (op=2).
It refuses unaligned/out-of-arena pointers, wrong ring size,
reentrant start, unavailable watchdog vector remap and active
live-serial ownership of the shared microphone/serial port.

On a valid start, the firmware borrows the TIM17 interrupt vector
from the armed SRAM txdog remap and verifies its alias writeback.
The ISR doesn't write RF registers, own PTT or turn on PA.
Explicit stop and every TX/module/txdog de-key restore the saved
radio microphone registers, stop timer/IRQ, clear the ring pointer,
restore the vector *before* the watchdog switches address zero back
to flash, and release the transmitter.

**Actual compiled ARM qualification:** The opt-in
tools/k6_sotto_api30_mic_gate.py boots the real K6 firmware ELF
and matching image in K6Board/Unicorn, installs a real CRC32
cable module and invokes the normal stack-switching module
dispatcher. It fixes **only the test simulator's** SRAM-zero
alias fidelity: the original emulator copied the vector table
only on remap, but hardware exposes a live alias and thus sees
subsequent vector writes. Firmware vector guards are unchanged.

The test invokes the **real compiled TIM17 Cortex-M0 ISR 95
times** at the modeled 8-kHz cadence with a square-wave PDM
GPIO source. It produced an 80-sample ring block (63 nonzero
samples, no ring-loss flag) and observed a peak modeled ISR cost
of **3,072 / 12,000 cycles** per sample window. A normal stop
returns success (10), IRQ and timer stopped, module ring cleared,
RFIC/PA restored. A second test deliberately returns from the
module without stopping microphone or TX; the host reclaims
both resources and returns code 11 with no timer, IRQ or PA leak.

**Limitations:** This is *not a hardware PCM quality/SNR test*.
The emulator's GPIO source is synthetic and its instruction-cycle
estimate is not measured KD32F328 interrupt timing. It does not
run a full microphone-encoded Sotto transmission or the on-radio
RX decoder and speaker. Hardware microphone capture still requires
an unplugged serial cable, fresh exclusive #11 bench lease and
operator validation; this build did not touch the radio.

Full four-service development firmware size (API28+29+30+31)
is **113,584 / 122,880 bytes** (9,296 spare) in this opt-in
Base+Data profile, SHA-256:
3d88bc0a8ed2e992701596c4ce04e8959125abc914799ab7b7defa4b11670013.

To rebuild with exact opt-in settings and run the offline gate:

    K6_AURA_RESET_GENERATED=1 K6_MODE_REGISTRY=1 K6_PACKET_MAILBOX=1 K6_FSK4800_RX=1 K6_SOTTO_RESOURCES=1 K6_SOTTO_ASYNC_FSK=1 K6_SOTTO_MIC=1 sh firmware/k6-aura/build-sotto-data.sh /tmp/flux-api30-sim
    K6_SIM_SOTTO_RX31_IMAGE=firmware/k6-aura/out-fd6818b/flux-fd6818b-pack.bin K6_SIM_SOTTO_RX31_ELF=firmware/k6-aura/build/aura-fd6818b-port/target/thumbv6m-none-eabi/release/aura K6_SIM_SOTTO_MIC=1 ~/.local/share/flux-rf-venv/bin/python -m pytest -q tests/test_k6_sotto_api30_mic_gate.py tests/test_k6_sotto_api30_mic_overlay.py

## Boundaries remaining for flashable half-duplex voice

1. Complete the UART/CPS and keyed install/SPI library path. The
   **actual run_module and km_run_on_stack execution is now tested**
   after CRC32 cable ingress, but on-device keyed SPI installation and
   normal selectable VFO binding remain open.
2. **API28 resources, API29 finite FSK TX, API30 microphone
   sampling and API31 RX** now compile into one opt-in image.
   Their individual ARM services pass offline qualification, including
   the modeled PA11/TIM17 ISR. The remaining integration is the
   *actual live Sotto encoder* calling these callbacks together,
   installed VFO mode/PTT, and on-K6 decoder/speaker, followed by
   silicon timing and combined memory/stack gates.
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
