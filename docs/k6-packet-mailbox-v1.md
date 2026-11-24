# K6 Data-pack packet-provider mailbox — experimental API27 (not enabled by default)

This is an **offline integration prototype**, not a production packet
dispatcher. Do not enable in a normal radio build or merge into main
until the explicit release gates below are met.

The host-owned V1 TX slot is 128 bytes with an inline payload, generation
number, mode identity, CRC16/CCITT-FALSE, declared capability and expiry.
The RX path is a four-frame bounded FIFO. Neither descriptor contains
a pointer to module scratch memory, so it remains valid across a module
arena swap. The C reference contract is
c/k6/mod/k6_packet_mailbox.h; the opt-in Rust host overlay is
firmware/k6-aura/apply-packet-mailbox.py.

API26 remains the read-only installed-mode enumeration API. API27 appends
mb_version, stage_packet, poll_result, take_rx, claim_packet,
complete_packet and publish_rx to the module table in that exact order.
Compiling the API27 tail requires K6_MODE_REGISTRY=1 and
K6_PACKET_MAILBOX=1; all existing API1–25 fields stay in place. Never
assume the different API29 radio firmware has these fields: callers must
require the **exact level 27 and mailbox version 1**.

## Offline status

- Native C mailbox lifecycle (one-shot consume, wrong mode, corrupted CRC,
  expiry and FIFO backpressure): 14 passing tests.
- Rust/C ABI, overlay/idempotence and opt-in gates: 7 additional passing
  tests. Combined 21/21 passing.
- A full offline FD6818B firmware build with the optional API27 Rust
  overlay completed successfully, binary 113,620 bytes.
- The LOOPBACK-EMU provider is a no-RF fixture exposing action35;
  Sotto's existing installed test modes are **VOICE-only** and cannot
  be used as packet datagram providers.

## Blocking work before hardware or main

1. **Host dispatch lifecycle:** host recognizes TNC -40 return,
   identifies and authenticates a matching signed installed module, runs
   action35 *after* TNC has returned, and never nests module executions.
2. **Phase, time and capability enforcement:** currently the phase flag
   is permanently false, so API27 stage_packet() refuses everything.
   Before enabling, set the phase for the verified TNC only, implement a
   real host uptime/TTL and selected-provider capability gate, and make
   all unexpected returns/crashes consume/fail the one pending packet.
3. **UART continuity:** the module currently relinquishes raw KISS
   ownership when staging. The host must explicitly define and test
   how it buffers or backpressures concurrent PC input across the
   provider arena swap, and how it returns the completed TX/RX status
   without generating false ACKs. Until then, it must refuse remote
   multi-modem KISS packets, never silently drop or replay.
4. **Signed provider registration:** LOOPBACK-EMU is only a test fixture.
   A real modem must explicitly advertise a packet-capable, authenticated
   entry point; installed voice-only Sotto must remain TX-refused for KISS.
5. **Acceptance:** complete real module emulation for both ends of a
   dispatcher turn, install/run/cleanup on a bench-owned dummy-loaded K6,
   review failures, and prove the original firmware+packed slots are
   restored before merging.

The default Data-pack FLUX TNC, with native guarded APRS-1200, is
covered independently by the Cortex-M0 Unicorn and host KISS protocol
tests in draft PR #31. It does not yet fulfill the arbitrary-modem goal.
