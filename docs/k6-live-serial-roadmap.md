# Live serial / Hamlib roadmap

Branch dev/live-serial-hamlib, based on origin/main d3ddd6b.
CW checkpoint 746e2f1 on research/tx-injection; GP0 module-run port 592cb36
comes from f734ad1, without importing the experimental RF code. Preserve
the legacy loader and the fixed-width FLXLV1 wire protocol. No radio flash
or RF test without coordination with the research agents.

## Milestones

1. Installed foreground modules with framed reports; CAT get/set frequency,
   mode, VFO, capability query, MCU health; local NET rigctl server.
2. Variable-length v2 request transport. Preserve v1 decoder. Explicit
   capability/schema negotiation at 115200. A proposed new magic separates
   v2 from cable and v1. Bounded frame length, LE job/transaction IDs,
   sequence numbers and CRC; input parsing incremental with timeout and
   deterministic resync. Select payload limit only after measuring ring
   capacity and stack; malformed/truncated frames must never dispatch.
   Use existing CPS response framing where possible. Reserve v1 opcodes
   0x63–0x65 for research diagnostics/health and 0x66–0x6a for current APIs.
3. Read-only live inventory and typed module metadata; signed temporary
   RAM image staging and execution. Reuse km_packet/verification and normal
   dispatcher; validate actual arena/code/scratch/stack ownership. No
   arbitrary memory writes, unsigned-code default, or replay bypass.
   Stage chunks in a bounded transaction; verify commit before executing.
4. Persistent module install/removal via the same signed, typed packed
   library routines. Atomic commit/readback/recovery, no flash writes while
   transmitting, no legacy loader fallback on a busy/timeout response.
5. Job IDs, bounded completion cache, reconnect/status recovery and
   cooperative cancellation. The synchronous module runner cannot service
   arbitrary App operations from its stack. Poll only a narrowly scoped
   stop/status channel at existing safe module yield points, keep signed
   module semantics, return pending/cancelled/completed separately. A host
   timeout is not a cancellation. Do not blindly retry execution.
6. Binary result frames with stream/job IDs, chunk sequence, gap and drop
   counters, size/credit limits. No unbounded allocation on radio/host.
   Integrate existing negotiated high-rate transport and IF/IQ/FSK services.
7. CAT controls: calibrated RSSI, squelch, power, offsets, split/tones,
   memory select/write, telemetry subscriptions; Hamlib client compatibility
   tests. Native asynchronous CWID, remote PTT only with independently
   verified lease and TX watchdog, local abort/override, band/lock/busy
   restrictions and release on disconnect. No general raw register write
   CAT operation. Bridge local-only initially; no default remote access.

## Verification gates

Host tests, actual Hamlib NET client, fixed-slot and packed compiled-image
emulation, repeated overlay builds, exact flash/RAM size budgets, malformed
frames/timeout/reconnect/corrupted module tests, no SPI modifications on
read/volatile CAT paths. Bench use: verify no unintended PTT or reboot,
mode/frequency changes, local control and audio continuity, safety checks
with properly rated RF load. Keep observations separate from claims.
Consider a native upstream Hamlib backend once wire ABI is stable.

## Direction: primary live interface

Normal operation, readback, inventory, and development should use live serial.
Retain the legacy transport for bootloader entry, MCU flash, stock/factory
provisioning and explicit recovery; migrate other mutations once their live
transactions have been verified. No automatic legacy fallback.

Transport-first checkpoint: v1 bounded downlink bursts and auto-probe at
500/192/115.2 kbaud, full SPI backup and library INFO/inventory. Separate
uplink negotiation is still required for staged variable-length module
transfer. Keep release feature enablement gated on complete profile size/
stack audits and physical verification. Do not claim the prior legacy
40 kB/s measurement as a live result.

### V2/RAM checkpoint (2026-11-20)

Initial V2 and temporary execution implemented: binary-safe 128-byte
payloads, bounded staging in arena and explicit verified commit, replay
watermark, structured reports/return code. Require K6_RAM0=stack for
temporary execution; the previous small-stack emulator build corrupted
the UART binary-mode flag, so full relocation with the original arena
and boot constructor fix were ported from CW research. Expanded-stack
image passed all compiled-image tests. The initial 4 KiB/API 1–2 limits
are deliberate; no arbitrary unsigned execution on keyed firmware.
Cable-only firmware retains its existing CRC-only physical-trust model.
Remaining: physical validation, negotiated sustained uplink, cooperative
cancel/job IDs and reconnect, larger module streaming/arena sizing,
live persistent mutations and full CAT/remote TX safety.
