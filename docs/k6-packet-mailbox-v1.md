# K6 Data-pack packet-provider mailbox — experimental API27 (not enabled by default)

This is an **experimental, opt-in firmware integration**, not a
production packet dispatcher. Source integration into main does **not**
enable this in default firmware builds. Only build images with
K6_MODE_REGISTRY=1 and K6_PACKET_MAILBOX=1 for explicit development;
the API27 layout is incompatible with the currently running API29
radio until that radio is reflashed with a matching image.

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
- A full clean offline FD6818B firmware build with API27 mailbox and
  one-shot non-nested LOOPBACK-EMU dispatcher completed successfully,
  binary **114,472 bytes** (120 KiB ceiling). The guarded dispatcher is
  introduced by firmware/k6-aura/apply-packet-dispatch.py and runs only
  the authenticated no-RF loopback provider with action 35. A compiler
  success is NOT an end-to-end packet delivery qualification.
- The LOOPBACK-EMU provider is a no-RF fixture exposing action35;
  Sotto's existing installed test modes are **VOICE-only** and cannot
  be used as packet datagram providers.

## Out-of-band qualification still required

1. **Real provider dispatch:** the host now allows staging only while a
   verified level27 FLUX TNC is running, limits mode ID to 0x0200, uses
   a 30-second TTL and host extended timer, and runs the authenticated
   LOOPBACK-EMU action35 only *after* the TNC returns. This is
   compiler-verified, not yet exercised end-to-end on hardware.
2. **UART continuity:** foreground TNC returns to normal serial before
   provider handoff, and **is not automatically resumed** afterward.
   Host software must define backpressure, recovery of input during
   the module swap, and an externally observable completion result.
   Generic KISS applications must not interpret a silent handoff as
   successful packet transmission. No automatic replay is permitted.
3. **Other modem backends:** signed LOOPBACK-EMU is a no-RF fixture only.
   A real packet modem must implement its own bounded authenticated
   entry point and completion semantics. Sotto remains **voice-only**:
   selecting it does not make it KISS packet-capable.
4. **End-to-end acceptance:** build the exact matching API27 radio image,
   install version-matched modules through the shared bench queue, then
   test real TNC -> provider -> completion, recovery and restored
   storage/serial. No such RF or provider handoff hardware tests are
   claimed here.

The main-branch source code can contain this experiment **disabled by
default** while APRS-only TNC remains independently usable on the
existing API29 radio. API27 KISS packet routing requires explicit opt-in.

The default Data-pack FLUX TNC, with native guarded APRS-1200, is
covered independently by the Cortex-M0 Unicorn and host KISS protocol
tests in draft PR #31. It does not yet fulfill the arbitrary-modem goal.
